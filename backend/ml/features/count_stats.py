"""Modelos de estadísticas de conteo por partido: córners, tiros, tiros a
puerta, tarjetas y faltas. Un solo modelo parametrizado por `StatSpec`.

Idea (la misma del modelo de goles, poisson_xg.py): cada equipo tiene una
tasa "a favor" y "en contra" de la estadística, estimada sobre sus últimos
partidos y encogida hacia el promedio de la liga; el valor esperado de cada
equipo es ataque × defensa rival × promedio de liga (con factor local /
visitante), y el total ~ Poisson(λ_local + λ_visitante) → P(Over/Under línea).

Por qué vale la pena generalizarlo (validado en validate_count_stats.py,
holdout 2025-26, 6,900 partidos): tiros y faltas dependen del estilo de juego
y son mucho más persistentes que los córners. Acierto del modelo vs. tasa
base en la línea central: córners 55.6% vs 50.7%; tiros totales 60.5% vs
51.0%; faltas 69.4% vs 59.5%; tiros a puerta 56.3% vs 52.3%; tarjetas 63.7%
vs 61.8%. Ganarle a la tasa base NO es ganarle a la casa (que también sabe
esto y centra su línea en cada partido); eso solo lo dice el CLV acumulado.

Fuente: data/raw/club_football/Matches.csv (espejo en Kaggle de
football-data.co.uk, licencia abierta): 122k partidos de 22 ligas europeas
desde 2000 con todas estas estadísticas, actualizado hasta pocos días atrás.
Se usa directamente desde el CSV porque cubre ligas que no están en la BD y
sus nombres de equipo son un tercer namespace (resuelto con alias).

Supuesto y su límite: todas estas cuentas están sobredispersas respecto a
una Poisson (varianza/media: córners 1.2, tarjetas 1.2, tiros 1.6, faltas
2.1), y un promedio de 20 partidos es ruidoso. Dos correcciones empíricas:
encogimiento bayesiano de la tasa del equipo (prior = 20 partidos de liga) y
una calibración isotónica por línea ajustada sobre predicciones walk-forward
(ml/training/calibrate_count_stats.py). Sin la calibración el modelo era
sobreconfiado justo en los extremos, que es donde el ranking apuesta.
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
MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

ROLLING_WINDOW = 20          # partidos recientes por equipo para estimar su tasa
MIN_MATCHES = 10             # por debajo de esto no se emite predicción (mismo espíritu que min_team_history_matches)
# Encogimiento bayesiano de la tasa del equipo hacia el promedio de liga.
# Un prior de 20 = "hace falta una temporada de evidencia para creerle del
# todo a la tasa propia del equipo". Validado en córners: sin él, predicciones
# de 78% acertaban 67%.
SHRINKAGE_PRIOR_MATCHES = 20
MAX_COUNT = 80


@dataclass(frozen=True)
class StatSpec:
    key: str                 # identificador corto: "corners", "shots"...
    market_prefix: str       # "CORNERS_OU" -> mercados CORNERS_OU_9.5
    home_col: str            # columna del CSV (local)
    away_col: str            # columna del CSV (visitante)
    lines: tuple[float, ...]  # líneas .5 que se modelan y para las que se ingieren cuotas
    label: str               # nombre humano (para logs/README)
    # Prior cuando la liga no tiene historial: (promedio local, promedio visitante).
    fallback_league_avg: tuple[float, float]
    # Columnas extra que se suman a la principal (tarjetas: amarillas + 2×rojas).
    extra_cols: tuple[tuple[str, str, float], ...] = ()

    @property
    def calibration_path(self) -> Path:
        return MODELS_DIR / f"{self.key}_calibration.joblib"

    def market(self, line: float) -> str:
        return f"{self.market_prefix}_{line}"


# Líneas: solo .5 (una línea entera puede empatar y la liquidación no contempla
# devoluciones) y solo las que las casas publican de verdad para partidos
# normales. Las que no pasen validación no reciben backtest_results y el job
# no las apuesta (SIN_VALIDAR).
CORNERS = StatSpec("corners", "CORNERS_OU", "HomeCorners", "AwayCorners", (8.5, 9.5), "Córners", (5.6, 4.6))
SHOTS = StatSpec("shots", "SHOTS_OU", "HomeShots", "AwayShots",
                 (20.5, 21.5, 22.5, 23.5, 24.5, 25.5, 26.5, 27.5, 28.5), "Tiros totales", (13.0, 10.5))
SHOTS_ON_TARGET = StatSpec("sot", "SOT_OU", "HomeTarget", "AwayTarget",
                           (6.5, 7.5, 8.5, 9.5, 10.5), "Tiros a puerta", (5.1, 4.1))
# Tarjetas según la convención habitual de las casas (Bet365, Pinnacle):
# amarilla = 1, roja = 2. Una segunda amarilla que expulsa cuenta 1 + 2.
CARDS = StatSpec("cards", "CARDS_OU", "HomeYellow", "AwayYellow", (2.5, 3.5, 4.5, 5.5, 6.5), "Tarjetas", (1.8, 2.1),
                 extra_cols=(("HomeRed", "AwayRed", 2.0),))
FOULS = StatSpec("fouls", "FOULS_OU", "HomeFouls", "AwayFouls",
                 (21.5, 22.5, 23.5, 24.5, 25.5, 26.5, 27.5, 28.5, 29.5), "Faltas", (12.5, 13.0))

ALL_SPECS: tuple[StatSpec, ...] = (CORNERS, SHOTS, SHOTS_ON_TARGET, CARDS, FOULS)
SPECS_BY_KEY = {s.key: s for s in ALL_SPECS}
SPECS_BY_PREFIX = {s.market_prefix: s for s in ALL_SPECS}


def spec_for_market(market: str) -> StatSpec | None:
    """'SHOTS_OU_24.5' -> SHOTS; None si el mercado no es de conteo."""
    prefix, _, _ = market.rpartition("_")
    return SPECS_BY_PREFIX.get(prefix)


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


@lru_cache(maxsize=1)
def _load_raw() -> pd.DataFrame:
    cols = {"Division", "MatchDate", "HomeTeam", "AwayTeam"}
    for s in ALL_SPECS:
        cols.update((s.home_col, s.away_col))
        for h, a, _ in s.extra_cols:
            cols.update((h, a))
    df = pd.read_csv(CSV_PATH, low_memory=False, usecols=sorted(cols))
    df["MatchDate"] = pd.to_datetime(df["MatchDate"])
    df["home_key"] = df["HomeTeam"].map(normalize)
    df["away_key"] = df["AwayTeam"].map(normalize)
    return df.sort_values("MatchDate").reset_index(drop=True)


def load_stat_frame(spec: StatSpec) -> pd.DataFrame:
    """Filas del CSV con la estadística disponible, en columnas genéricas
    `home_value` / `away_value` (ya combinadas: tarjetas = amarillas + 2×rojas)."""
    raw = _load_raw()
    needed = [spec.home_col, spec.away_col] + [c for h, a, _ in spec.extra_cols for c in (h, a)]
    df = raw.dropna(subset=needed)
    home = df[spec.home_col].astype(float)
    away = df[spec.away_col].astype(float)
    for h, a, w in spec.extra_cols:
        home = home + w * df[h].astype(float)
        away = away + w * df[a].astype(float)
    out = df[["Division", "MatchDate", "HomeTeam", "AwayTeam", "home_key", "away_key"]].copy()
    out["home_value"] = home.to_numpy()
    out["away_value"] = away.to_numpy()
    return out.reset_index(drop=True)


@dataclass
class TeamProfile:
    for_avg: float
    against_avg: float
    n_matches: int
    division: str


class CountStatModel:
    def __init__(self, spec: StatSpec, df: pd.DataFrame | None = None, calibration_path: Path | None | str = "default"):
        self.spec = spec
        self.df = df if df is not None else load_stat_frame(spec)
        # Índices por equipo y por división, ordenados por fecha: el corte
        # temporal se resuelve con searchsorted en vez de filtrar 122k filas
        # por predicción (la validación pasaba de ~10 min a segundos).
        self._teams = self._index_teams(self.df)
        self._divisions = {
            div: (g["MatchDate"].to_numpy(), g["home_value"].to_numpy(float), g["away_value"].to_numpy(float))
            for div, g in self.df.groupby("Division", sort=False)
        }
        if calibration_path == "default":
            calibration_path = spec.calibration_path
        self.calibrators: dict[float, object] = {}
        if calibration_path is not None and Path(calibration_path).exists():
            self.calibrators = joblib.load(calibration_path)

    @staticmethod
    def _index_teams(df: pd.DataFrame) -> dict[str, tuple]:
        home = pd.DataFrame({"key": df["home_key"], "date": df["MatchDate"], "div": df["Division"],
                             "for": df["home_value"], "against": df["away_value"]})
        away = pd.DataFrame({"key": df["away_key"], "date": df["MatchDate"], "div": df["Division"],
                             "for": df["away_value"], "against": df["home_value"]})
        long = pd.concat([home, away]).sort_values("date", kind="stable")
        return {
            key: (g["date"].to_numpy(), g["for"].to_numpy(float), g["against"].to_numpy(float), g["div"].to_numpy())
            for key, g in long.groupby("key", sort=False)
        }

    def team_profile(self, team_name: str, as_of: date | datetime) -> TeamProfile | None:
        """Tasas rodantes del equipo usando SOLO partidos anteriores a `as_of`
        (sin fuga temporal). Devuelve None si no hay historial suficiente."""
        entry = self._teams.get(_resolve_key(team_name))
        if entry is None:
            return None
        dates, values_for, values_against, divisions = entry
        end = int(np.searchsorted(dates, np.datetime64(_cutoff(as_of)), side="left"))
        start = max(0, end - ROLLING_WINDOW)
        if end - start < MIN_MATCHES:
            return None
        return TeamProfile(
            for_avg=float(values_for[start:end].mean()),
            against_avg=float(values_against[start:end].mean()),
            n_matches=end - start,
            division=str(divisions[end - 1]),
        )

    def league_averages(self, division: str, as_of: date | datetime, window_matches: int = 400) -> tuple[float, float]:
        """Promedio local/visitante de la liga en sus últimos partidos antes de `as_of`."""
        entry = self._divisions.get(division)
        if entry is None:
            return self.spec.fallback_league_avg
        dates, home_v, away_v = entry
        end = int(np.searchsorted(dates, np.datetime64(_cutoff(as_of)), side="left"))
        if end == 0:
            return self.spec.fallback_league_avg
        start = max(0, end - window_matches)
        return float(home_v[start:end].mean()), float(away_v[start:end].mean())

    def expected_values(self, home_team: str, away_team: str, as_of: date | datetime) -> tuple[float, float] | None:
        """(λ_local, λ_visitante) o None si a alguno le falta historial."""
        home = self.team_profile(home_team, as_of)
        away = self.team_profile(away_team, as_of)
        if home is None or away is None:
            return None

        league_home, league_away = self.league_averages(home.division, as_of)
        league_overall = (league_home + league_away) / 2

        def shrink(team_avg: float, n: int) -> float:
            return (SHRINKAGE_PRIOR_MATCHES * league_overall + n * team_avg) / (SHRINKAGE_PRIOR_MATCHES + n)

        home_attack, home_defense = team_strengths(
            shrink(home.for_avg, home.n_matches), shrink(home.against_avg, home.n_matches), league_overall
        )
        away_attack, away_defense = team_strengths(
            shrink(away.for_avg, away.n_matches), shrink(away.against_avg, away.n_matches), league_overall
        )
        return expected_goals(home_attack, home_defense, away_attack, away_defense, league_home, league_away)

    def raw_probabilities(self, lambda_home: float, lambda_away: float) -> dict[str, float]:
        return over_under_probabilities(lambda_home + lambda_away, self.spec)

    def market_probabilities(self, home_team: str, away_team: str, as_of: date | datetime) -> dict[str, float] | None:
        """Probabilidades por línea, calibradas si hay calibrador. None si falta historial."""
        exp = self.expected_values(home_team, away_team, as_of)
        if exp is None:
            return None
        return apply_calibration(self.raw_probabilities(*exp), self.calibrators, self.spec)


def over_under_probabilities(total_lambda: float, spec: StatSpec) -> dict[str, float]:
    """P(total > línea) para cada línea del spec, bajo total ~ Poisson(λ)."""
    cdf = poisson.cdf(np.arange(MAX_COUNT + 1), total_lambda)
    out = {}
    for line in spec.lines:
        p_over = float(1.0 - cdf[int(np.floor(line))])  # P(X > 9.5) = 1 - P(X <= 9)
        out[f"{spec.market(line)}_OVER"] = p_over
        out[f"{spec.market(line)}_UNDER"] = 1.0 - p_over
    return out


def apply_calibration(raw: dict[str, float], calibrators: dict[float, object], spec: StatSpec) -> dict[str, float]:
    """Pasa P(over) de cada línea por su isotónica (si la hay) y recompone P(under)."""
    if not calibrators:
        return raw
    out = dict(raw)
    for line, cal in calibrators.items():
        key = f"{spec.market(line)}_OVER"
        if key not in out:
            continue
        p_over = float(np.clip(cal.predict([out[key]])[0], 0.0, 1.0))
        out[key] = p_over
        out[f"{spec.market(line)}_UNDER"] = 1.0 - p_over
    return out


class CountStatModels:
    """Los cinco modelos cargados una vez (comparten el CSV en memoria)."""

    def __init__(self, specs: tuple[StatSpec, ...] = ALL_SPECS):
        self.models = {s.key: CountStatModel(s) for s in specs}

    def market_probabilities(self, home_team: str, away_team: str, as_of: date | datetime) -> dict[str, float]:
        out: dict[str, float] = {}
        for model in self.models.values():
            probs = model.market_probabilities(home_team, away_team, as_of)
            if probs:
                out.update(probs)
        return out

    def expected_totals(self, home_team: str, away_team: str, as_of: date | datetime) -> dict[str, float | None]:
        """Total esperado por estadística (para diagnósticos / UI)."""
        out = {}
        for key, model in self.models.items():
            exp = model.expected_values(home_team, away_team, as_of)
            out[key] = sum(exp) if exp else None
        return out
