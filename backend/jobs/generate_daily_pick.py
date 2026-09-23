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
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import BacktestResult, MarketEvaluation, Match, Odds, Prediction
from jobs.market_evaluation import evaluate_match
from ml.features.colombia import is_colombian
from ml.features.count_stats import ALL_SPECS
from ml.value_betting.confidence import SkillEvidence

settings = get_settings()

# Mercados que el job evalúa. Solo los que tengan backtest_results con
# habilidad > 0 producen picks; el resto se evalúa pero queda SIN_VALIDAR.
MARKETS = ["1X2", "DOUBLE_CHANCE", "OU_1.5", "OU_2.5", "BTTS"] + [
    spec.market(line) for spec in ALL_SPECS for line in spec.lines
]


def _skill_evidence(db: Session, market: str, model_version: str | None = None) -> SkillEvidence:
    """Evidencia de habilidad del modelo en este mercado, del backtest más reciente."""
    row = db.execute(
        select(BacktestResult)
        .where(
            BacktestResult.model_version == (model_version or settings.model_version),
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


def _todays_matches(db: Session, day: date | None = None) -> list[Match]:
    """Partidos SCHEDULED del día (UTC); hoy por defecto. Con --date el job
    genera los picks de mañana con las cuotas de hoy (se regeneran mañana
    con las cuotas frescas: el borrado de abajo lo hace idempotente)."""
    now = datetime.now(timezone.utc)
    if day is None:
        day = now.date()
    today_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)
    stmt = select(Match).where(
        Match.kickoff_utc >= today_start,
        Match.kickoff_utc < today_end,
        Match.status == "SCHEDULED",
        # Un partido ya empezado no admite apuesta pre-partido; y re-evaluarlo
        # borraría sus evaluaciones con cuotas de antes del inicio.
        Match.kickoff_utc > now,
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


def _select_top(candidates: list[Prediction], n: int) -> list[Prediction]:
    """Como máximo un candidato por partido (el de mayor EV de ese partido),
    para que el top N muestre partidos distintos en vez de varios mercados
    del mismo juego. Los elegidos quedan marcados como pick del día."""
    best_per_match: dict[int, Prediction] = {}
    for c in candidates:
        current = best_per_match.get(c.match_id)
        if current is None or c.expected_value > current.expected_value:
            best_per_match[c.match_id] = c
    top = sorted(best_per_match.values(), key=lambda p: p.expected_value, reverse=True)[:n]
    for pick in top:
        pick.is_pick_of_day = True
    return top


def run(model_probabilities_fn, day: date | None = None):
    """model_probabilities_fn(match) -> dict[market_selection] = probability

    Se inyecta como parámetro para desacoplar el job de la implementación
    concreta del modelo (features + XGBoost/Poisson); ver ml/features y
    ml/training para la construcción real de ese callable en producción.
    """
    db = SessionLocal()
    try:
        matches = _todays_matches(db, day)

        # Idempotencia: si el job se corre más de una vez el mismo día (ej.
        # las cuotas se movieron y se quiere recalcular), hay que limpiar las
        # predicciones previas de estos partidos antes de reinsertar, o la
        # restricción única (match, modelo, mercado, selección) revienta.
        match_ids = [m.id for m in matches]
        if match_ids:
            for table in (Prediction.__table__, MarketEvaluation.__table__):
                db.execute(
                    table.delete().where(
                        table.c.match_id.in_(match_ids),
                        table.c.model_version.in_([settings.model_version, settings.colombia_model_version]),
                    )
                )

        # Dos apartados que no se mezclan: Europa (modelo validado) y Colombia
        # (modelo propio, picks experimentales con contador aparte).
        candidates: list[Prediction] = []
        colombia_candidates: list[Prediction] = []
        n_evaluations = 0
        evidence_cache: dict[tuple[str, str], SkillEvidence] = {}

        def evidence_for(market: str, version: str) -> SkillEvidence:
            if (version, market) not in evidence_cache:
                evidence_cache[(version, market)] = _skill_evidence(db, market, version)
            return evidence_cache[(version, market)]

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

            # Todos los mercados con cuota quedan guardados como evaluación
            # (para medir el modelo contra la casa); los que pasan el filtro
            # estricto son candidatos a pick. Ver jobs/market_evaluation.py.
            colombian = is_colombian(match)
            version = settings.colombia_model_version if colombian else settings.model_version
            result = evaluate_match(
                match, probabilities, MARKETS,
                odds_for_market=lambda market, match_id=match.id: _latest_odds(db, match_id, market),
                evidence_for_market=lambda market, v=version: evidence_for(market, v),
                settings=settings,
                now=datetime.now(timezone.utc),
                model_version=version,
                # Colombia aún no tiene backtest propio: sus picks son
                # experimentales y su contador aparte es la validación.
                require_validation=not colombian,
            )
            db.add_all(result.evaluations)
            db.add_all(result.candidates)
            n_evaluations += len(result.evaluations)
            (colombia_candidates if colombian else candidates).extend(result.candidates)

        db.flush()  # asigna IDs sin comitear aun

        for label, pool, n in (("Europa", candidates, settings.top_picks_count),
                               ("Colombia (experimental)", colombia_candidates, settings.colombia_top_picks_count)):
            top_picks = _select_top(pool, n)
            if not top_picks:
                print(f"{label}: no se encontraron candidatos que cumplan los criterios hoy.")
                continue
            print(f"{label}: top {len(top_picks)} picks del día:")
            for i, p in enumerate(top_picks, 1):
                print(f"  {i}. match={p.match_id} {p.market}/{p.selection} odds={p.bookmaker_odds} EV={p.expected_value:.3f}")

        print(f"{n_evaluations} mercados evaluados y guardados (para medir el modelo contra la casa).")

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
    import argparse

    from ml.serving.predict import DEFAULT_MODEL_DIR, TrainedModels, predict_match_probabilities

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Día (UTC) a generar, YYYY-MM-DD. Por defecto hoy.")
    args = parser.parse_args()
    day = date.fromisoformat(args.date) if args.date else None

    models = TrainedModels(model_dir=DEFAULT_MODEL_DIR, model_version=settings.model_version)

    # Una sola sesión para todas las predicciones (antes se abría una
    # conexión nueva por partido).
    prediction_db = SessionLocal()

    def model_probabilities_fn(match: Match) -> dict:
        return predict_match_probabilities(prediction_db, match, models)

    try:
        run(model_probabilities_fn, day)
    finally:
        prediction_db.close()


if __name__ == "__main__":
    main()
