"""Sincroniza lesiones/sanciones por liga+temporada desde API-Football.

Uso:
    python -m ml.ingestion.sync_injuries --league 39 --season 2026
"""
import argparse

from app.database import SessionLocal
from ml.ingestion.api_football_client import APIFootballClient
from ml.ingestion.mappers import upsert_injuries


def sync_injuries(league: int, season: int) -> int:
    db = SessionLocal()
    try:
        with APIFootballClient() as client:
            injuries = client.get_injuries(league=league, season=season)
            n = upsert_injuries(db, injuries)
            db.commit()
    finally:
        db.close()
    return n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", type=int, required=True)
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    n = sync_injuries(args.league, args.season)
    print(f"Insertadas {n} lesiones/sanciones para liga={args.league} temporada={args.season}")


if __name__ == "__main__":
    main()
