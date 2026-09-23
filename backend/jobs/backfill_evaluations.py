"""Evalúa retroactivamente los partidos YA JUGADOS que tienen cuotas guardadas,
para no esperar semanas a tener con qué medir el modelo contra la casa.

Sin trampa temporal:
  - cuotas: la última foto LIVE tomada ANTES del inicio del partido;
  - modelo de conteos: tasas con partidos anteriores al día del partido;
  - modelo de goles: replay con partidos anteriores al inicio.
La validación de cada mercado (VALIDADO / SIN_VALIDAR) es la de hoy: no afecta
a lo que se mide (probabilidad del modelo vs probabilidad de la casa).

Solo crea evaluaciones, nunca picks: inventar picks del pasado falsearía el
historial de recomendaciones.

Uso:
    python -m jobs.backfill_evaluations --since 2026-09-14
"""
import argparse
from datetime import datetime, timezone

from sqlalchemy import exists, func, select

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import MarketEvaluation, Match, Odds
from jobs.generate_daily_pick import MARKETS, _skill_evidence
from jobs.market_evaluation import evaluate_match
from ml.serving.predict import DEFAULT_MODEL_DIR, TrainedModels, predict_match_probabilities

settings = get_settings()


def odds_before_kickoff(db, match: Match, market: str) -> list[Odds]:
    last = db.execute(
        select(func.max(Odds.recorded_at)).where(
            Odds.match_id == match.id, Odds.market == market, Odds.snapshot_type == "LIVE",
            Odds.recorded_at < match.kickoff_utc,
        )
    ).scalar_one_or_none()
    if last is None:
        return []
    return list(db.execute(
        select(Odds).where(Odds.match_id == match.id, Odds.market == market,
                           Odds.snapshot_type == "LIVE", Odds.recorded_at == last)
    ).scalars().all())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, help="YYYY-MM-DD (UTC)")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        matches = db.execute(
            select(Match).where(
                Match.kickoff_utc >= since,
                Match.kickoff_utc < now,
                exists().where(Odds.match_id == Match.id, Odds.snapshot_type == "LIVE", Odds.recorded_at < Match.kickoff_utc),
                ~exists().where(MarketEvaluation.match_id == Match.id),
            ).order_by(Match.kickoff_utc)
        ).scalars().all()
        print(f"{len(matches)} partidos jugados con cuotas pre-partido y sin evaluar.")
        if not matches:
            return

        models = TrainedModels(DEFAULT_MODEL_DIR, settings.model_version)
        cache: dict = {}

        def evidence_for(market):
            if market not in cache:
                cache[market] = _skill_evidence(db, market)
            return cache[market]

        total = skipped = 0
        for match in matches:
            probabilities = predict_match_probabilities(db, match, models)
            diag = probabilities.get("_diagnostics", {})
            if min(diag.get("home_history", 0), diag.get("away_history", 0)) < settings.min_team_history_matches:
                skipped += 1
                continue
            result = evaluate_match(
                match, probabilities, MARKETS,
                odds_for_market=lambda market, m=match: odds_before_kickoff(db, m, market),
                evidence_for_market=evidence_for,
                settings=settings,
                now=now,
            )
            db.add_all(result.evaluations)
            db.commit()
            total += len(result.evaluations)
            print(f"  {match.kickoff_utc:%Y-%m-%d} {match.home_team.name} vs {match.away_team.name}: {len(result.evaluations)} mercados")
        print(f"Evaluaciones creadas: {total}. Partidos sin historial suficiente: {skipped}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
