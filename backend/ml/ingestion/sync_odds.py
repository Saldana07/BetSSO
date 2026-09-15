"""Sincroniza cuotas para los partidos de una fecha ya presentes en la DB
(ejecutar sync_fixtures.py primero). Cada corrida inserta un nuevo snapshot
de cuotas (no sobreescribe) para poder reconstruir la evolución apertura ->
cierre requerida por la metodología de value betting.

Uso:
    python -m ml.ingestion.sync_odds --date 2026-09-14 --snapshot-type OPENING
    python -m ml.ingestion.sync_odds --date 2026-09-14 --snapshot-type CLOSING
"""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Match
from ml.ingestion.api_football_client import APIFootballClient
from ml.ingestion.mappers import upsert_odds_for_match

settings = get_settings()


def _team_history_count(db: Session, team_id: int) -> int:
    return db.execute(
        select(func.count(Match.id)).where(
            Match.status == "FINISHED",
            Match.home_goals.isnot(None),
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
        )
    ).scalar_one()


def sync_odds(date: str, snapshot_type: str, only_with_history: bool = True, limit: int | None = None) -> int:
    day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    db = SessionLocal()
    total_inserted = 0
    try:
        # Solo partidos aún no jugados necesitan cuotas — pedirlas para
        # partidos FINISHED/LIVE desperdicia cuota diaria de API-Football
        # (100 req/día en el plan Free) sin ningún uso para el pick del día.
        matches = db.execute(
            select(Match).where(
                Match.kickoff_utc >= day_start,
                Match.kickoff_utc < day_end,
                Match.status == "SCHEDULED",
            )
        ).scalars().all()

        if not matches:
            print(f"No hay partidos SCHEDULED en DB para {date}. Ejecuta sync_fixtures.py primero.")
            return 0

        if only_with_history:
            # Solo pedimos cuotas de partidos que podrían generar un pick.
            # generate_daily_pick descarta los equipos sin historial, así que
            # pedir sus cuotas quema cuota diaria (100 req/día en el plan Free)
            # para nada: en una jornada normal hay 200+ partidos programados.
            eligible = []
            for match in matches:
                home_hist = _team_history_count(db, match.home_team_id)
                away_hist = _team_history_count(db, match.away_team_id)
                if min(home_hist, away_hist) >= settings.min_team_history_matches:
                    eligible.append(match)
            print(
                f"{len(matches)} partidos programados, {len(eligible)} con historial suficiente "
                f"(>={settings.min_team_history_matches} partidos por equipo). Se piden cuotas solo de esos."
            )
            matches = eligible

        if limit is not None:
            matches = matches[:limit]

        if not matches:
            print("Ningún partido con historial suficiente hoy: no se gasta cuota de API.")
            return 0

        recorded_at = datetime.now(timezone.utc)
        with APIFootballClient() as client:
            for match in matches:
                odds_results = client.get_odds(fixture_id=match.api_match_id)
                for odds_payload in odds_results:
                    inserted = upsert_odds_for_match(db, match.id, odds_payload, snapshot_type, recorded_at)
                    total_inserted += inserted
            db.commit()
    finally:
        db.close()
    return total_inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--snapshot-type", required=True, choices=["OPENING", "LIVE", "CLOSING"],
    )
    parser.add_argument(
        "--all-matches", action="store_true",
        help="Pide cuotas de TODOS los partidos, no solo los que tienen historial suficiente (gasta mucha más cuota)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Tope de partidos a consultar (protege la cuota diaria)")
    args = parser.parse_args()

    n = sync_odds(args.date, args.snapshot_type, only_with_history=not args.all_matches, limit=args.limit)
    print(f"Insertadas {n} filas de cuotas ({args.snapshot_type}) para {args.date}")


if __name__ == "__main__":
    main()
