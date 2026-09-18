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
un poco. Se valida empíricamente en ml/backtesting/validate_corners.py.

Capa de calibración (ml/training/calibrate_corners.py): la Poisson cruda es
sobreconfiada en los extremos, que es justo donde el sistema apuesta (cuando
decía 68% de Over 9.5 acertaba 61%; medido en 6,926 partidos fuera de
muestra). Como el ranking elige el pick con mayor desacuerdo con la casa,
sin corregir esto se estaba seleccionando el error del modelo, no una
ventaja. Una isotónica por línea, ajustada sobre predicciones walk-forward,
lo corrige; se guarda en models/corners_calibration.joblib y CornerModel la
aplica si existe. Sin el archivo, las probabilidades salen crudas.
"""
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import poisson

from ml.features.poisson_xg import expected_goals, team_strengths

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "raw" / "club_football" / "Matches.csv"
CALIBRATION_PATH = Path(__file__).resolve().parents[2] / "models" / "corners_calibration.joblib"
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
    def __init__(self, df: pd.DataFrame | None = None, calibration_path: Path | None = CALIBRATION_PATH):
        self.df = df if df is not None else _load()
        # Índices por equipo y por división, ordenados por fecha: el corte
        # temporal se resuelve con searchsorted en vez de filtrar 122k filas
        # por predicción (la validación pasaba de ~10 min a segundos).
        self._teams = self._index_teams(self.df)
        self._divisions = {
            div: (g["MatchDate"].to_numpy(), g["HomeCorners"].to_numpy(float), g["AwayCorners"].to_numpy(float))
            for div, g in self.df.groupby("Division", sort=False)
        }
        self.calibrators: dict[float, object] = {}
        if calibration_path is not None and Path(calibration_path).exists():
            self.calibrators = joblib.load(calibration_path)

    @staticmethod
    def _index_teams(df: pd.DataFrame) -> dict[str, tuple]:
        home = pd.DataFrame({"key": df["home_key"], "date": df["MatchDate"], "div": df["Division"],
                             "for": df["HomeCorners"], "against": df["AwayCorners"]})
        away = pd.DataFrame({"key": df["away_key"], "date": df["MatchDate"], "div": df["Division"],
                             "for": df["AwayCorners"], "against": df["HomeCorners"]})
        long = pd.concat([home, away]).sort_values("date", kind="stable")
        return {
            key: (g["date"].to_numpy(), g["for"].to_numpy(float), g["against"].to_numpy(float), g["div"].to_numpy())
            for key, g in long.groupby("key", sort=False)
        }

    def team_profile(self, team_name: str, as_of: date | datetime) -> TeamCornerProfile | None:
        """Tasas rodantes del equipo usando SOLO partidos anteriores a `as_of`
        (sin fuga temporal). Devuelve None si no hay historial suficiente."""
        entry = self._teams.get(_resolve_key(team_name))
        if entry is None:
            return None
        dates, corners_for, corners_against, divisions = entry
        end = int(np.searchsorted(dates, np.datetime64(_cutoff(as_of)), side="left"))
        start = max(0, end - ROLLING_WINDOW)
        if end - start < MIN_MATCHES:
            return None
        return TeamCornerProfile(
            corners_for_avg=float(corners_for[start:end].mean()),
            corners_against_avg=float(corners_against[start:end].mean()),
            n_matches=end - start,
            division=str(divisions[end - 1]),
        )

    def league_averages(self, division: str, as_of: date | datetime, window_matches: int = 400) -> tuple[float, float]:
        """Promedio de córners local/visitante de la liga en sus últimos partidos antes de `as_of`."""
        entry = self._divisions.get(division)
        if entry is None:
            return 5.6, 4.6  # promedios globales de la fuente, como prior
        dates, home_c, away_c = entry
        end = int(np.searchsorted(dates, np.datetime64(_cutoff(as_of)), side="left"))
        if end == 0:
            return 5.6, 4.6
        start = max(0, end - window_matches)
        return float(home_c[start:end].mean()), float(away_c[start:end].mean())

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


    def market_probabilities(self, home_team: str, away_team: str, as_of: date | datetime) -> dict[str, float] | None:
        """Probabilidades por línea, calibradas si hay calibrador. None si falta historial."""
        exp = self.expected_corners(home_team, away_team, as_of)
        if exp is None:
            return None
        return apply_calibration(corner_market_probabilities(*exp), self.calibrators)


def apply_calibration(raw: dict[str, float], calibrators: dict[float, object]) -> dict[str, float]:
    """Pasa P(over) de cada línea por su isotónica (si la hay) y recompone P(under)."""
    if not calibrators:
        return raw
    out = dict(raw)
    for line, cal in calibrators.items():
        key = f"CORNERS_OU_{line}_OVER"
        if key not in out:
            continue
        p_over = float(np.clip(cal.predict([out[key]])[0], 0.0, 1.0))
        out[key] = p_over
        out[f"CORNERS_OU_{line}_UNDER"] = 1.0 - p_over
    return out


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
