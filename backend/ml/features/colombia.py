"""Modelo de córners y tarjetas SOLO con fútbol colombiano.

El histórico europeo (football-data) no tiene Colombia. Aquí el mismo modelo
de conteos (tasas por equipo encogidas hacia la media de la liga, Binomial
Negativa) se construye con los partidos colombianos de la BD cuyas
estadísticas ya se trajeron de API-Football (ml/ingestion/import_colombia.py
+ jobs/fetch_match_data.py). Solo córners y tarjetas: son los únicos
mercados de conteo que las casas publican para la liga colombiana.

Sin calibración ni validación propias hasta que haya muestra: sus picks son
EXPERIMENTALES y se miden en un contador aparte (versión de modelo
COLOMBIA_MODEL_VERSION), que es precisamente lo que dirá si sirve.
"""
from datetime import date, datetime

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import League, Match
from ml.features.count_stats import (
    CARDS, CORNERS, DB_STAT_COLUMNS, CountStatModel, StatSpec, TeamResolver, competition_country, normalize,
)

COLOMBIA = "Colombia"
COLOMBIA_DIVISIONS = {239: "COL1", 240: "COL2"}   # Primera A, Primera B
COLOMBIA_SPECS: tuple[StatSpec, ...] = (CORNERS, CARDS)


def is_colombian(match: Match) -> bool:
    """Partido de fútbol masculino colombiano (liga o copa)."""
    league = match.league
    return league is not None and competition_country(league.name, league.country) == COLOMBIA


def load_colombia_frame(db: Session) -> pd.DataFrame:
    """Partidos colombianos terminados con estadísticas, en el formato del CSV."""
    matches = db.execute(
        select(Match).join(League, Match.league_id == League.id).where(
            League.api_league_id.in_(COLOMBIA_DIVISIONS),
            Match.status == "FINISHED",
            or_(Match.home_corners.isnot(None), Match.home_yellow.isnot(None)),
        )
    ).scalars().all()
    stat_cols = sorted({c for cols in DB_STAT_COLUMNS.values() for h, a, _ in cols for c in (h, a)})
    rows = []
    for m in matches:
        row = {
            "Division": COLOMBIA_DIVISIONS[m.league.api_league_id],
            "MatchDate": pd.Timestamp(m.kickoff_utc).tz_convert("UTC").tz_localize(None).normalize(),
            "HomeTeam": m.home_team.name, "AwayTeam": m.away_team.name,
            "home_key": normalize(m.home_team.name), "away_key": normalize(m.away_team.name),
        }
        for c in stat_cols:
            v = getattr(m, c)
            row[c] = float(v) if v is not None else None
        rows.append(row)
    return pd.DataFrame(rows, columns=["Division", "MatchDate", "HomeTeam", "AwayTeam", "home_key", "away_key", *stat_cols])


def _frame_for(spec: StatSpec, raw: pd.DataFrame) -> pd.DataFrame:
    cols = DB_STAT_COLUMNS[spec.key]
    needed = [c for h, a, _ in cols for c in (h, a)]
    df = raw.dropna(subset=needed)
    out = df[["Division", "MatchDate", "HomeTeam", "AwayTeam", "home_key", "away_key"]].copy()
    out["home_value"] = sum(w * df[h] for h, _, w in cols)
    out["away_value"] = sum(w * df[a] for _, a, w in cols)
    return out.sort_values("MatchDate", kind="stable").reset_index(drop=True)


class ColombiaCountModels:
    def __init__(self, raw: pd.DataFrame):
        self.models: dict[str, CountStatModel] = {}
        self.n_matches: dict[str, int] = {spec.key: 0 for spec in COLOMBIA_SPECS}
        if raw.empty:
            return   # estado inicial: aún no se trajo ninguna estadística colombiana
        for spec in COLOMBIA_SPECS:
            frame = _frame_for(spec, raw)
            self.n_matches[spec.key] = len(frame)
            if len(frame):
                self.models[spec.key] = CountStatModel(spec, df=frame, calibration_path=None,
                                                       resolver=TeamResolver.from_frame(frame))

    def market_probabilities(self, home_team: str, away_team: str, as_of: date | datetime) -> dict[str, float]:
        out: dict[str, float] = {}
        for model in self.models.values():
            probs = model.market_probabilities(home_team, away_team, as_of)
            if probs:
                out.update(probs)
        return out

    def expected_totals(self, home_team: str, away_team: str, as_of: date | datetime) -> dict[str, float | None]:
        out = {}
        for key, model in self.models.items():
            exp = model.expected_values(home_team, away_team, as_of)
            out[key] = sum(exp) if exp else None
        return out
