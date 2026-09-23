"""Paso rápido del apartado colombiano: cuotas y picks de los partidos
colombianos de las próximas horas, sin esperar al flujo de las 08:00.

Por qué existe: la cuota de API se renueva a las 19:00 (Colombia) y muchos
partidos colombianos empiezan entre las 19:30 y las 21:00; la tarea
"liquidar" pasa cada 2 horas y no pide cuotas. Esto pide SOLO las cuotas de
esos partidos (1 petición cada uno) y genera los picks del apartado
colombiano, sin tocar los de Europa ni la reserva del flujo diario.

Uso:
    python -m jobs.colombia_now [--hours 6]
"""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import League, Match, Odds
from jobs.fetch_match_data import RESERVE_BEFORE_DAILY
from ml.features.colombia import COLOMBIA_DIVISIONS
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_odds_for_match
from ml.ingestion.sync_odds import _team_history_count

MIN_HISTORY = 10
FRESH_ODDS = timedelta(hours=3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=6.0, help="Partidos que empiezan dentro de estas horas")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        matches = db.execute(
            select(Match).join(League, Match.league_id == League.id).where(
                League.api_league_id.in_(COLOMBIA_DIVISIONS), Match.status == "SCHEDULED",
                Match.kickoff_utc > now, Match.kickoff_utc < now + timedelta(hours=args.hours),
                Match.api_match_id.isnot(None),
            ).order_by(Match.kickoff_utc)
        ).scalars().all()
        ready = [m for m in matches
                 if min(_team_history_count(db, m.home_team_id), _team_history_count(db, m.away_team_id)) >= MIN_HISTORY]
        if not ready:
            print(f"Colombia: ningún partido con historial suficiente en las próximas {args.hours:g} h.")
            return

        fetched = 0
        with APIFootballClient() as client:
            used, limit = client.quota()
            budget = max(0, limit - used - RESERVE_BEFORE_DAILY)   # nunca toca la reserva del flujo de las 08:00
            # En la primera hora tras la renovación (00:00 UTC = 19:00 Colombia) la
            # cuota del día es nueva, pero /status puede seguir mostrando la de
            # ayer durante un rato: no se le cree y se piden los pocos partidos.
            if now.hour == 0:
                budget = max(budget, len(ready))
            for m in ready:
                last = db.execute(select(func.max(Odds.recorded_at)).where(
                    Odds.match_id == m.id, Odds.snapshot_type == "LIVE")).scalar_one_or_none()
                if last is not None and now - last < FRESH_ODDS:
                    continue
                if budget <= 0:
                    print("Sin cuota disponible para más cuotas (se respeta la reserva del flujo diario).")
                    break
                try:
                    for payload in client.get_odds(fixture_id=m.api_match_id):
                        upsert_odds_for_match(db, m.id, payload, "LIVE", now)
                    fetched += 1
                except APIFootballError as exc:
                    print(f"  [!] cuotas {m.home_team.name}-{m.away_team.name}: {exc}")
                budget -= 1
        db.commit()
        print(f"Colombia: {len(ready)} partidos en las próximas {args.hours:g} h, cuotas nuevas de {fetched}.")
        days = sorted({m.kickoff_utc.date() for m in ready})
    finally:
        db.close()

    import sys

    from jobs import generate_daily_pick
    for day in days:
        sys.argv = ["generate_daily_pick", "--date", day.isoformat(), "--scope", "colombia"]
        generate_daily_pick.main()


if __name__ == "__main__":
    main()
