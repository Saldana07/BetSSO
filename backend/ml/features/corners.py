"""Modelo de córners: Poisson multiplicativo sobre tasas rodantes por equipo.

Misma idea que el modelo de goles (poisson_xg.py): cada equipo tiene una
tasa de córners a favor ("ataque") y en contra ("defensa"), normalizadas
por el promedio de la liga; el número esperado de córners de cada equipo es
el producto ataque × defensa rival × promedio de liga (con factor local /
visitante), y el total ~ Poisson(λ_local + λ_visitante).

Fuente de datos: data/raw/club_football/Matches.csv (espejo en Kaggle de
football-data.co.uk, licencia abierta), 122k partidos con córners de 22
ligas europeas desde 2000 y actualizado hasta pocos días atrás. Se usa
DIRECTAMENTE desde el CSV, sin pasar por la tabla `matches`, porque:
  - cubre 26 años de 22 ligas que en su mayoría no están en la BD, y
  - los nombres de equipo son un tercer namespace distinto (ni 538 ni
    API-Football); el mapeo se resuelve aquí con normalización + alias.

Supuesto y su límite: los córners están algo sobredispersos respecto a una
Poisson (varianza/media ≈ 1.2 en el total), así que las colas se subestiman
un poco. Se valida empíricamente en ml/backtesting/validate_corners.py; si
la calibración lo exige, el paso natural es una Binomial Negativa.
"""
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson

from ml.features.poisson_xg import expected_goals, team_strengths

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "raw" / "club_football" / "Matches.csv"
ROLLING_WINDOW = 20          # partidos recientes por equipo para estimar su tasa
MIN_MATCHES = 10             # por debajo de esto no se emite predicción (mismo espíritu que min_team_history_matches)
# Encogimiento bayesiano de la tasa del equipo hacia el promedio de liga.
# Sin esto el modelo es sobreconfiado: un promedio de 20 partidos es ruidoso y
# sus extremos son en buena parte azar que regresa a la media (validado en
# validate_corners.py: predicciones de 78% acertaban 67%; de 25%, 39%).
# Un prior de 20 = "hace falta una temporada de evidencia para creerle del
# todo a la tasa propia del equipo".
SHRINKAGE_PRIOR_MATCHES = 20
# Solo las líneas donde validate_corners.py demostró habilidad REAL (el modelo
# supera la tasa base tanto en Brier como en accuracy). En 10.5 y 11.5 la
# accuracy alta es puro desbalance de clases (casi siempre sale Under) y
# persistirla haría que el sistema etiquetara esos picks como alta confianza
# por una ilusión estadística.
LINES = (8.5, 9.5)
MAX_CORNERS = 30

# API-Football usa nombres largos; football-data.co.uk usa abreviaturas.
# Clave y valor ya normalizados (minúsculas, sin acentos).
TEAM_ALIASES = {
    "athletic club": "ath bilbao",
    "atletico madrid": "ath madrid",
    "espanyol": "espanol",
    "eintracht frankfurt": "ein frankfurt",
    "fortuna sittard": "for sittard",
    "1. fc koln": "fc koln",
    "fc cologne": "fc koln",
    "manchester united": "man united",
    "manchester city": "man city",
    "tottenham": "tottenham",
    "wolves": "wolves",
    "nottingham forest": "nott'm forest",
    "newcastle": "newcastle",
    "psv eindhoven": "psv",
    "inter": "inter",
    "as roma": "roma",
    "bayer leverkusen": "leverkusen",
    "borussia dortmund": "dortmund",
    "borussia monchengladbach": "m'gladbach",
    "vfb stuttgart": "stuttgart",
    "sporting cp": "sp lisbon",
    "sc braga": "sp braga",
    "rayo vallecano": "vallecano",
    "real sociedad": "sociedad",
    "real betis": "betis",
    "celta vigo": "celta",
    "deportivo alaves": "alaves",
    "paris saint germain": "paris sg",
    "olympique marseille": "marseille",
    "olympique lyonnais": "lyon",
    "willem ii": "willem ii",
    "go ahead eagles": "go ahead eagles",
}


def normalize(name: str) -> str:
    stripped = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(stripped.lower().replace(".", "").split())


@dataclass
class TeamCornerProfile:
    corners_for_avg: float
    corners_against_avg: float
    n_matches: int
    division: str


@lru_cache(maxsize=1)
def _load() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, low_memory=False, usecols=["Division", "MatchDate", "HomeTeam", "AwayTeam", "HomeCorners", "AwayCorners"])
    df = df.dropna(subset=["HomeCorners", "AwayCorners"])
    df["MatchDate"] = pd.to_datetime(df["MatchDate"])
    df["home_key"] = df["HomeTeam"].map(normalize)
    df["away_key"] = df["AwayTeam"].map(normalize)
    return df.sort_values("MatchDate").reset_index(drop=True)


def _resolve_key(team_name: str) -> str:
    key = normalize(team_name)
    return TEAM_ALIASES.get(key, key)


def _cutoff(as_of: date | datetime) -> pd.Timestamp:
    """Fecha de corte tz-naive a medianoche. Las fechas del CSV son naive; las
    de la BD (TIMESTAMPTZ) llegan tz-aware y pandas se niega a compararlas."""
    ts = pd.Timestamp(as_of)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


class CornerModel:
    def __init__(self, df: pd.DataFrame | None = None):
        self.df = df if df is not None else _load()

    def team_profile(self, team_name: str, as_of: date | datetime) -> TeamCornerProfile | None:
        """Tasas rodantes del equipo usando SOLO partidos anteriores a `as_of`
        (sin fuga temporal). Devuelve None si no hay historial suficiente."""
        key = _resolve_key(team_name)
        cutoff = _cutoff(as_of)
        df = self.df
        mask = ((df["home_key"] == key) | (df["away_key"] == key)) & (df["MatchDate"] < cutoff)
        recent = df.loc[mask].tail(ROLLING_WINDOW)
        if len(recent) < MIN_MATCHES:
            return None

        is_home = recent["home_key"] == key
        corners_for = np.where(is_home, recent["HomeCorners"], recent["AwayCorners"])
        corners_against = np.where(is_home, recent["AwayCorners"], recent["HomeCorners"])
        return TeamCornerProfile(
            corners_for_avg=float(corners_for.mean()),
            corners_against_avg=float(corners_against.mean()),
            n_matches=len(recent),
            division=str(recent["Division"].iloc[-1]),
        )

    def league_averages(self, division: str, as_of: date | datetime, window_matches: int = 400) -> tuple[float, float]:
        """Promedio de córners local/visitante de la liga en sus últimos partidos antes de `as_of`."""
        cutoff = _cutoff(as_of)
        recent = self.df[(self.df["Division"] == division) & (self.df["MatchDate"] < cutoff)].tail(window_matches)
        if recent.empty:
            return 5.6, 4.6  # promedios globales de la fuente, como prior
        return float(recent["HomeCorners"].mean()), float(recent["AwayCorners"].mean())

    def expected_corners(self, home_team: str, away_team: str, as_of: date | datetime) -> tuple[float, float] | None:
        home = self.team_profile(home_team, as_of)
        away = self.team_profile(away_team, as_of)
        if home is None or away is None:
            return None

        league_home, league_away = self.league_averages(home.division, as_of)
        league_overall = (league_home + league_away) / 2

        def shrink(team_avg: float, n: int) -> float:
            return (SHRINKAGE_PRIOR_MATCHES * league_overall + n * team_avg) / (SHRINKAGE_PRIOR_MATCHES + n)

        home_attack, home_defense = team_strengths(
            shrink(home.corners_for_avg, home.n_matches), shrink(home.corners_against_avg, home.n_matches), league_overall
        )
        away_attack, away_defense = team_strengths(
            shrink(away.corners_for_avg, away.n_matches), shrink(away.corners_against_avg, away.n_matches), league_overall
        )

        return expected_goals(home_attack, home_defense, away_attack, away_defense, league_home, league_away)


def corner_market_probabilities(lambda_home: float, lambda_away: float) -> dict[str, float]:
    """P(total de córners > línea) para cada línea, bajo total ~ Poisson(λh + λa)."""
    total_lambda = lambda_home + lambda_away
    cdf = poisson.cdf(np.arange(MAX_CORNERS + 1), total_lambda)
    out = {}
    for line in LINES:
        p_over = float(1.0 - cdf[int(np.floor(line))])  # P(X > 9.5) = 1 - P(X <= 9)
        out[f"CORNERS_OU_{line}_OVER"] = p_over
        out[f"CORNERS_OU_{line}_UNDER"] = 1.0 - p_over
    return out
