"""Partidos recientes de la BD (estadísticas traídas de API-Football) que
completan el histórico del CSV mientras el espejo de Kaggle no se actualiza.

Por qué: el espejo se publica una vez al mes (última versión: 05/09). Con 19
días de retraso, medido sobre 6,900 partidos, los mejores mercados (tiros y
faltas) perdían ~0.5-1 punto de acierto. jobs/fetch_match_data.py trae las
estadísticas de los partidos de liga de las divisiones cubiertas; aquí se
convierten al formato del CSV.

Solo entran partidos de LIGA de las divisiones del CSV: ambos equipos
emparejados con el histórico, en la misma división actual y del país de la
competición. Cómo se mezclan (y la guarda que comprueba que la API cuenta
igual que football-data antes de mezclar) está en count_stats.overlay_for_spec.
"""
import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import League, Match
from ml.features.count_stats import (
    COVERED_COUNTRIES, DB_STAT_COLUMNS, DIVISION_COUNTRY, SPI_SOURCE_TAG, TeamResolver, competition_country,
)

_STAT_COLUMNS = sorted({c for cols in DB_STAT_COLUMNS.values() for h, a, _ in cols for c in (h, a)})


def load_overlay_raw(db: Session, resolver: TeamResolver) -> pd.DataFrame:
    matches = db.execute(
        select(Match).join(League, Match.league_id == League.id).where(
            Match.status == "FINISHED",
            League.country.in_(COVERED_COUNTRIES | {SPI_SOURCE_TAG}),
            or_(*(getattr(Match, c).isnot(None) for c in ("home_corners", "home_shots", "home_fouls", "home_yellow"))),
        )
    ).scalars().all()

    rows = []
    for m in matches:
        country = competition_country(m.league.name, m.league.country)
        hk = resolver.resolve(m.home_team.name, country)
        ak = resolver.resolve(m.away_team.name, country)
        if hk is None or ak is None:
            continue
        div = resolver.last_division[hk]
        if resolver.last_division[ak] != div or DIVISION_COUNTRY.get(div) != country:
            continue
        kickoff = pd.Timestamp(m.kickoff_utc).tz_convert("UTC").tz_localize(None).normalize()
        row = {"Division": div, "MatchDate": kickoff, "HomeTeam": m.home_team.name, "AwayTeam": m.away_team.name,
               "home_key": hk, "away_key": ak}
        for c in _STAT_COLUMNS:
            value = getattr(m, c)
            row[c] = float(value) if value is not None else None
        rows.append(row)
    columns = ["Division", "MatchDate", "HomeTeam", "AwayTeam", "home_key", "away_key", *_STAT_COLUMNS]
    return pd.DataFrame(rows, columns=columns)
