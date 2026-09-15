"""Sincroniza partidos (y de paso ligas/equipos) de una fecha dada desde API-Football.

Uso:
    python -m ml.ingestion.sync_fixtures --date 2026-09-14 [--league 39] [--season 2026]

Sin --league sincroniza TODOS los partidos del día en todas las ligas
disponibles para tu plan — con el plan Free eso puede agotar la cuota diaria
rápido, así que en Free conviene siempre pasar --league.
"""
import argparse

from app.database import SessionLocal
from ml.ingestion.api_football_client import APIFootballClient
from ml.ingestion.mappers import upsert_match


def sync_fixtures(date: str, league: int | None = None, season: int | None = None) -> int:
    db = SessionLocal()
    count = 0
    try:
        with APIFootballClient() as client:
            fixtures = client.get_fixtures_by_date(date, league=league, season=season)
            for fixture_payload in fixtures:
                upsert_match(db, fixture_payload)
                count += 1
            db.commit()
    finally:
        db.close()
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--league", type=int, default=None, help="ID de liga en API-Football (ej. 39 = Premier League)")
    parser.add_argument("--season", type=int, default=None, help="Año de temporada, ej. 2026")
    args = parser.parse_args()

    n = sync_fixtures(args.date, args.league, args.season)
    print(f"Sincronizados {n} partidos para {args.date}")


if __name__ == "__main__":
    main()
