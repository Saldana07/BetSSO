"""Job diario: evalúa todos los mercados de los partidos del día y persiste
el TOP N de "picks del día" (mayor EV dentro de cuota 1.50-2.00 y accuracy
histórica mínima). Pensado para ejecutarse vía cron/scheduler (ej.
APScheduler, Celery beat, o un cron job del sistema) una vez al día antes de
la primera jornada.

Flujo:
  1. Carga partidos del día (ya ingeridos por ml/ingestion/fetch_data.py)
  2. Calcula features (Elo, Poisson/xG, forma ponderada) por partido
  3. Genera probabilidades por mercado (modelo calibrado + Poisson)
  4. Cruza contra cuotas actuales -> EV y Kelly fraccional
  5. Filtra por [odds_min, odds_max] y accuracy histórica del backtest
  6. Marca is_pick_of_day=True en el top N (settings.top_picks_count) por EV,
     tomando como máximo UN candidato por partido (para no llenar el top 5
     con 5 mercados del mismo juego)
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import BacktestResult, Match, Odds, Prediction
from ml.value_betting.confidence import SkillEvidence
from ml.value_betting.ev_kelly import evaluate_bet, is_safe_bet_candidate

settings = get_settings()

MARKETS = ["1X2", "DOUBLE_CHANCE", "OU_1.5", "OU_2.5", "BTTS", "CORNERS_OU_8.5", "CORNERS_OU_9.5"]


def _skill_evidence(db: Session, market: str) -> SkillEvidence:
    """Evidencia de habilidad del modelo en este mercado, del backtest más reciente."""
    row = db.execute(
        select(BacktestResult)
        .where(
            BacktestResult.model_version == settings.model_version,
            BacktestResult.market == market,
        )
        .order_by(BacktestResult.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return SkillEvidence(None, None, None)
    return SkillEvidence(
        accuracy=float(row.accuracy) if row.accuracy is not None else None,
        base_accuracy=float(row.base_accuracy) if row.base_accuracy is not None else None,
        n_bets=row.n_bets,
    )


def _todays_matches(db: Session) -> list[Match]:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    stmt = select(Match).where(
        Match.kickoff_utc >= today_start,
        Match.kickoff_utc < today_end,
        Match.status == "SCHEDULED",
    )
    return list(db.execute(stmt).scalars().all())


def _latest_odds(db: Session, match_id: int, market: str) -> list[Odds]:
    """Devuelve SOLO las cuotas del snapshot más reciente de ese mercado.

    Antes esto devolvía todos los snapshots históricos y el job se quedaba con
    la cuota más alta entre todos ellos — es decir, podía calcular el EV sobre
    un precio de hace horas que el mercado ya movió y que nadie puede tomar.
    Las cuotas son perecederas: solo vale la foto más reciente.
    """
    latest_recorded_at = db.execute(
        select(func.max(Odds.recorded_at)).where(
            Odds.match_id == match_id,
            Odds.market == market,
            Odds.snapshot_type == "LIVE",
        )
    ).scalar_one_or_none()

    if latest_recorded_at is None:
        return []

    # Cuotas demasiado viejas tampoco sirven: el mercado ya se movió.
    age_hours = (datetime.now(timezone.utc) - latest_recorded_at).total_seconds() / 3600
    if age_hours > settings.max_odds_age_hours:
        return []

    stmt = select(Odds).where(
        Odds.match_id == match_id,
        Odds.market == market,
        Odds.snapshot_type == "LIVE",
        Odds.recorded_at == latest_recorded_at,
    )
    return list(db.execute(stmt).scalars().all())


def run(model_probabilities_fn):
    """model_probabilities_fn(match) -> dict[market_selection] = probability

    Se inyecta como parámetro para desacoplar el job de la implementación
    concreta del modelo (features + XGBoost/Poisson); ver ml/features y
    ml/training para la construcción real de ese callable en producción.
    """
    db = SessionLocal()
    try:
        matches = _todays_matches(db)

        # Idempotencia: si el job se corre más de una vez el mismo día (ej.
        # las cuotas se movieron y se quiere recalcular), hay que limpiar las
        # predicciones previas de estos partidos antes de reinsertar, o la
        # restricción única (match, modelo, mercado, selección) revienta.
        match_ids = [m.id for m in matches]
        if match_ids:
            db.execute(
                Prediction.__table__.delete().where(
                    Prediction.match_id.in_(match_ids),
                    Prediction.model_version == settings.model_version,
                )
            )

        candidates: list[Prediction] = []
        evidence_cache: dict[str, SkillEvidence] = {}

        skipped_no_history = 0

        for match in matches:
            probabilities = model_probabilities_fn(match)

            # Guarda de suficiencia de datos: sin historial propio de AMBOS
            # equipos, el modelo solo puede devolver la tasa base de la
            # población, y cualquier discrepancia con el mercado se vería
            # como "+EV" cuando en realidad es ignorancia. Ver README.
            diagnostics = probabilities.get("_diagnostics", {})
            history = min(diagnostics.get("home_history", 0), diagnostics.get("away_history", 0))
            if history < settings.min_team_history_matches:
                skipped_no_history += 1
                continue

            for market in MARKETS:
                if market not in evidence_cache:
                    evidence_cache[market] = _skill_evidence(db, market)
                evidence = evidence_cache[market]
                hist_accuracy = evidence.accuracy

                odds_rows = _latest_odds(db, match.id, market)

                # Varias casas de apuestas ofrecen la misma selección con cuotas
                # distintas; nos quedamos con la MEJOR cuota disponible por
                # selección (mayor pago para la misma probabilidad = mayor EV,
                # y es la cuota que un apostador realmente conseguiría comparando
                # casas). `predictions` es única por (match, modelo, mercado,
                # selección) — no tiene sentido/cabida una fila por bookmaker.
                best_odds_by_selection: dict[str, Odds] = {}
                for odds_row in odds_rows:
                    current_best = best_odds_by_selection.get(odds_row.selection)
                    if current_best is None or odds_row.odds_value > current_best.odds_value:
                        best_odds_by_selection[odds_row.selection] = odds_row

                for odds_row in best_odds_by_selection.values():
                    key = f"{market}_{odds_row.selection}"
                    model_prob = probabilities.get(key)
                    if model_prob is None:
                        continue

                    bet = evaluate_bet(model_prob, float(odds_row.odds_value), settings.kelly_fraction_cap)

                    if not is_safe_bet_candidate(
                        odds=float(odds_row.odds_value),
                        expected_val=bet.expected_value,
                        historical_skill=evidence.skill,
                        odds_min=settings.odds_min,
                        odds_max=settings.odds_max,
                        min_ev=settings.min_pick_ev,
                        min_historical_skill=settings.min_historical_skill,
                        max_ev=settings.max_plausible_ev,
                    ):
                        continue

                    prediction = Prediction(
                        match_id=match.id,
                        model_version=settings.model_version,
                        market=market,
                        selection=odds_row.selection,
                        model_probability=model_prob,
                        bookmaker=odds_row.bookmaker,
                        bookmaker_odds=float(odds_row.odds_value),
                        implied_probability=bet.implied_probability,
                        expected_value=bet.expected_value,
                        kelly_fraction=bet.kelly_fraction,
                        historical_accuracy=hist_accuracy,
                        historical_skill=evidence.skill,
                        backtest_n_bets=evidence.n_bets,
                        is_pick_of_day=False,
                        generated_at=datetime.now(timezone.utc),
                    )
                    db.add(prediction)
                    candidates.append(prediction)

        db.flush()  # asigna IDs sin comitear aun

        if candidates:
            # Como máximo un candidato por partido (el de mayor EV de ese
            # partido), para que el top N muestre partidos distintos en vez
            # de varios mercados del mismo juego.
            best_per_match: dict[int, Prediction] = {}
            for c in candidates:
                current = best_per_match.get(c.match_id)
                if current is None or c.expected_value > current.expected_value:
                    best_per_match[c.match_id] = c

            top_picks = sorted(best_per_match.values(), key=lambda p: p.expected_value, reverse=True)
            top_picks = top_picks[: settings.top_picks_count]

            for pick in top_picks:
                pick.is_pick_of_day = True

            print(f"Top {len(top_picks)} picks del día:")
            for i, p in enumerate(top_picks, 1):
                print(f"  {i}. match={p.match_id} {p.market}/{p.selection} odds={p.bookmaker_odds} EV={p.expected_value:.3f}")
        else:
            print("No se encontraron candidatos que cumplan los criterios hoy.")

        if skipped_no_history:
            print(
                f"\n{skipped_no_history} partidos descartados por historial insuficiente "
                f"(<{settings.min_team_history_matches} partidos por equipo). "
                "Sin historial el modelo solo repite la tasa base y todo parece +EV."
            )

        db.commit()
    finally:
        db.close()


def main():
    from ml.serving.predict import TrainedModels, predict_match_probabilities

    models = TrainedModels(model_dir="models", model_version=settings.model_version)

    def model_probabilities_fn(match: Match) -> dict:
        db = SessionLocal()
        try:
            return predict_match_probabilities(db, match, models)
        finally:
            db.close()

    run(model_probabilities_fn)


if __name__ == "__main__":
    main()
