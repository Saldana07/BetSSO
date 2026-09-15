"""Importa el histórico de FiveThirtyEight (spi_matches.csv) a nuestro esquema.

Fuente: https://www.kaggle.com/datasets/thedevastator/club-soccer-predictions-using-spi-ratings-and-fo
(espejo, licencia CC0, de los archivos originales que 538 publicaba en
projects.fivethirtyeight.com/soccer-api/, retirados tras el cierre de 538 en 2023).

Cobertura: 2016-07-09 a 2023-06-04, 40 ligas, ~66,000 partidos con resultado,
SPI (análogo a Elo), xG real y las probabilidades del propio modelo de 538
(prob1/prob2/probtie) — NO son cuotas de casas de apuestas, así que este
dataset alimenta el entrenamiento de resultados/goles, pero no puede usarse
para el backtest de EV/Kelly (eso requiere cuotas reales de mercado).

A diferencia de mappers.py (API-Football, upsert por api_*_id), aquí no hay
IDs externos: equipos y ligas se resuelven por nombre.

Uso:
    python -m ml.ingestion.import_fivethirtyeight_spi --csv data/raw/fivethirtyeight/spi_matches.csv
    python -m ml.ingestion.import_fivethirtyeight_spi --csv ... --league "Barclays Premier League"  # subset
"""
import argparse
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import League, Match, Team

SOURCE_TAG = "fivethirtyeight_spi"


def _get_or_create_league(db: Session, cache: dict, name: str) -> League:
    if name in cache:
        return cache[name]
    league = db.execute(select(League).where(League.name == name, League.country == SOURCE_TAG)).scalar_one_or_none()
    if league is None:
        # country se usa aquí como "namespace" de origen de datos, ya que este
        # dataset no trae país por liga y no queremos chocar con ligas ya
        # sincronizadas desde API-Football (que sí tienen country real).
        league = League(name=name, country=SOURCE_TAG)
        db.add(league)
        db.flush()
    cache[name] = league
    return league


def _get_or_create_team(db: Session, cache: dict, name: str, league_id: int) -> Team:
    key = (name, league_id)
    if key in cache:
        return cache[key]
    team = db.execute(select(Team).where(Team.name == name, Team.league_id == league_id)).scalar_one_or_none()
    if team is None:
        team = Team(name=name, league_id=league_id)
        db.add(team)
        db.flush()
    cache[key] = team
    return team


def _find_existing_match(db: Session, league_id: int, home_id: int, away_id: int, kickoff: datetime) -> Match | None:
    stmt = select(Match).where(
        Match.league_id == league_id,
        Match.home_team_id == home_id,
        Match.away_team_id == away_id,
        Match.kickoff_utc == kickoff,
    )
    return db.execute(stmt).scalar_one_or_none()


def import_csv(csv_path: str, league_filter: str | None = None, batch_size: int = 500) -> dict:
    df = pd.read_csv(csv_path)
    if league_filter:
        df = df[df["league"] == league_filter]

    # Solo partidos ya jugados (con marcador) sirven para entrenar; el resto
    # eran partidos futuros al momento del snapshot de 538 y quedaron sin score.
    df = df.dropna(subset=["score1", "score2"]).reset_index(drop=True)

    league_cache: dict[str, League] = {}
    team_cache: dict[tuple[str, int], Team] = {}
    stats = {"rows": len(df), "inserted": 0, "updated_ext_prob": 0, "skipped_existing": 0}

    db = SessionLocal()
    try:
        for i, row in df.iterrows():
            league = _get_or_create_league(db, league_cache, row["league"])
            home = _get_or_create_team(db, team_cache, row["team1"], league.id)
            away = _get_or_create_team(db, team_cache, row["team2"], league.id)

            kickoff = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            ext_prob_home = float(row["prob1"]) if pd.notna(row.get("prob1")) else None
            ext_prob_draw = float(row["probtie"]) if pd.notna(row.get("probtie")) else None
            ext_prob_away = float(row["prob2"]) if pd.notna(row.get("prob2")) else None

            existing = _find_existing_match(db, league.id, home.id, away.id, kickoff)
            if existing is not None:
                # Backfill: partidos ya importados antes de que ext_prob_* existiera.
                if existing.ext_prob_home is None and ext_prob_home is not None:
                    existing.ext_prob_home = ext_prob_home
                    existing.ext_prob_draw = ext_prob_draw
                    existing.ext_prob_away = ext_prob_away
                    stats["updated_ext_prob"] += 1
                else:
                    stats["skipped_existing"] += 1
                continue

            match = Match(
                league_id=league.id,
                season=str(row["season"]),
                kickoff_utc=kickoff,
                home_team_id=home.id,
                away_team_id=away.id,
                status="FINISHED",
                home_goals=int(row["score1"]),
                away_goals=int(row["score2"]),
                home_xg=float(row["xg1"]) if pd.notna(row.get("xg1")) else None,
                away_xg=float(row["xg2"]) if pd.notna(row.get("xg2")) else None,
                ext_prob_home=ext_prob_home,
                ext_prob_draw=ext_prob_draw,
                ext_prob_away=ext_prob_away,
            )
            db.add(match)
            stats["inserted"] += 1

            if (i + 1) % batch_size == 0:
                db.commit()
                print(f"  ... {i + 1}/{len(df)} filas procesadas")

        db.commit()
    finally:
        db.close()

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--league", default=None, help="Filtra por un único nombre de liga (ej. 'Barclays Premier League')")
    args = parser.parse_args()

    stats = import_csv(args.csv, args.league)
    print(f"Filas en CSV (con score): {stats['rows']}")
    print(f"Partidos insertados: {stats['inserted']}")
    print(f"Actualizados con ext_prob_* (backfill): {stats['updated_ext_prob']}")
    print(f"Ya existían (sin cambios): {stats['skipped_existing']}")


if __name__ == "__main__":
    main()
