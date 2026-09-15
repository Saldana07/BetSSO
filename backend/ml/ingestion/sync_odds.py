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

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Match
from ml.ingestion.api_football_client import APIFootballClient
from ml.ingestion.mappers import upsert_odds_for_match


def sync_odds(date: str, snapshot_type: str) -> int:
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
    args = parser.parse_args()

    n = sync_odds(args.date, args.snapshot_type)
    print(f"Insertadas {n} filas de cuotas ({args.snapshot_type}) para {args.date}")


if __name__ == "__main__":
    main()
