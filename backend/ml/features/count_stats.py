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

Distribución del total: Binomial Negativa (Gamma-Poisson) con Var = μ + α·μ²,
no Poisson pura. Las cuentas están sobredispersas marginalmente (var/media:
córners 1.2, tarjetas 1.4, tiros 1.6, faltas 1.5), pero CONDICIONADA a la λ
del partido la sobredispersión es pequeña: α estimado por momentos sobre
predicciones walk-forward 2024-25 (residuo cuadrático menos μ, sobre μ²).
En holdout 2025-26 la NB mejora el Brier en córners y tarjetas (~0.001) y es
neutra en el resto; se usa porque es lo correcto, no porque cambie mucho.

Además: encogimiento bayesiano de la tasa del equipo (prior = 20 partidos de
liga) y calibración isotónica por línea ajustada sobre predicciones
walk-forward (ml/training/calibrate_count_stats.py). Sin la calibración el
modelo era sobreconfiado justo en los extremos, que es donde el ranking
apuesta.
"""
import difflib
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

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
    # Sobredispersión de la Binomial Negativa (Var = μ + α·μ²); 0 = Poisson.
    dispersion: float = 0.0

    @property
    def calibration_path(self) -> Path:
        return MODELS_DIR / f"{self.key}_calibration.joblib"

    def market(self, line: float) -> str:
        return f"{self.market_prefix}_{line}"


# Líneas: solo .5 (una línea entera puede empatar y la liquidación no contempla
# devoluciones) y solo las que las casas publican de verdad para partidos
# normales. Las que no pasen validación no reciben backtest_results y el job
# no las apuesta (SIN_VALIDAR).
# `dispersion` (α) estimada por momentos en 2024-25; ver docstring.
CORNERS = StatSpec("corners", "CORNERS_OU", "HomeCorners", "AwayCorners", (8.5, 9.5), "Córners", (5.6, 4.6),
                   dispersion=0.015)
SHOTS = StatSpec("shots", "SHOTS_OU", "HomeShots", "AwayShots",
                 (20.5, 21.5, 22.5, 23.5, 24.5, 25.5, 26.5, 27.5, 28.5), "Tiros totales", (13.0, 10.5),
                 dispersion=0.013)
SHOTS_ON_TARGET = StatSpec("sot", "SOT_OU", "HomeTarget", "AwayTarget",
                           (6.5, 7.5, 8.5, 9.5, 10.5), "Tiros a puerta", (5.1, 4.1), dispersion=0.012)
# Tarjetas según la convención habitual de las casas (Bet365, Pinnacle):
# amarilla = 1, roja = 2. Una segunda amarilla que expulsa cuenta 1 + 2.
CARDS = StatSpec("cards", "CARDS_OU", "HomeYellow", "AwayYellow", (2.5, 3.5, 4.5, 5.5, 6.5), "Tarjetas", (1.8, 2.1),
                 extra_cols=(("HomeRed", "AwayRed", 2.0),), dispersion=0.070)
FOULS = StatSpec("fouls", "FOULS_OU", "HomeFouls", "AwayFouls",
                 (21.5, 22.5, 23.5, 24.5, 25.5, 26.5, 27.5, 28.5, 29.5), "Faltas", (12.5, 13.0), dispersion=0.008)

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
    # Verificados el 22/09 contra el CSV (destino existe y es del mismo país):
    # equipos de ligas cubiertas que ni el emparejamiento difuso resolvía.
    "club brugge kv": "club brugge", "kv mechelen": "mechelen", "kvc westerlo": "westerlo",
    "lommel united": "lommel", "oh leuven": "oud-heverlee leuven", "standard liege": "standard",
    "union saint gilloise": "st gilloise", "zulte waregem": "waregem",
    "stoke city": "stoke", "burton albion": "burton", "cambridge united": "cambridge",
    "mansfield town": "mansfield", "oxford united": "oxford", "peterborough": "peterboro",
    "sheffield wednesday": "sheffield weds", "stockport county": "stockport", "accrington st": "accrington",
    "exeter city": "exeter", "salford city": "salford", "swindon town": "swindon",
    "aldershot town": "aldershot", "boston united": "boston utd", "fc halifax town": "halifax",
    "harrogate town": "harrogate", "kidderminster harriers": "kidderminster", "solihull moors": "solihull",
    "sutton utd": "sutton", "yeovil town": "yeovil",
    "clermont foot": "clermont", "red star fc 93": "red star", "saint etienne": "st etienne",
    "arminia bielefeld": "bielefeld", "dynamo dresden": "dresden", "eintracht braunschweig": "braunschweig",
    "energie cottbus": "cottbus", "hannover 96": "hannover",
    "aek athens fc": "aek", "aris thessalonikis": "aris", "iraklis 1908": "iraklis",
    "olympiakos piraeus": "olympiakos",
    "hellas verona": "verona", "vicenza virtus": "vicenza",
    "ayr utd": "ayr", "raith rovers": "raith rvs", "alloa athletic": "alloa",
    "hamilton academical": "hamilton", "queen of the south": "queen of sth", "elgin city": "elgin",
    "forfar athletic": "forfar", "stirling albion": "stirling", "dundee utd": "dundee united",
    "heart of midlothian": "hearts",
    "ad ceuta fc": "ceuta", "celta de vigo ii": "celta b", "real sociedad ii": "sociedad b",
    "sporting gijon": "sp gijon",
    "amed": "amedspor", "basaksehir": "buyuksehyr",
    # Nombres de las ligas reconciliadas con FiveThirtyEight (Premier, LaLiga...).
    "hull city": "hull", "leeds united": "leeds", "estac troyes": "troyes", "stade brestois 29": "brest",
    "hamburger sv": "hamburg", "sc paderborn 07": "paderborn", "internazionale": "inter",
    "ado den haag": "den haag", "nec": "nijmegen", "pec zwolle": "zwolle", "braga": "sp braga",
    "estoril praia": "estoril", "vitoria sc": "guimaraes", "deportivo la coruna": "la coruna",
    "racing santander": "santander",
}


# División del CSV -> país tal como lo nombra API-Football (`League.country`).
# Sirve de guarda al emparejar nombres: un "Nacional" de una liga uruguaya no
# puede heredar el historial del Nacional portugués por llamarse igual.
DIVISION_COUNTRY = {
    "E0": "England", "E1": "England", "E2": "England", "E3": "England", "EC": "England",
    "SC0": "Scotland", "SC1": "Scotland", "SC2": "Scotland", "SC3": "Scotland",
    "D1": "Germany", "D2": "Germany", "I1": "Italy", "I2": "Italy", "SP1": "Spain", "SP2": "Spain",
    "F1": "France", "F2": "France", "N1": "Netherlands", "B1": "Belgium", "P1": "Portugal",
    "T1": "Turkey", "G1": "Greece",
}
COVERED_COUNTRIES = frozenset(DIVISION_COUNTRY.values())

# Las ligas importadas de FiveThirtyEight (y a las que reconcile_teams repunta
# los partidos de la API) tienen como "país" la etiqueta de la fuente, no un
# país. Sin este mapa, un Parma-Genoa quedaba en una liga de país
# "fivethirtyeight_spi" y la guarda de país lo dejaba sin predicción.
SPI_SOURCE_TAG = "fivethirtyeight_spi"
SPI_LEAGUE_COUNTRY = {
    "Barclays Premier League": "England", "English League Championship": "England",
    "English League One": "England", "English League Two": "England",
    "Scottish Premiership": "Scotland",
    "German Bundesliga": "Germany", "German 2. Bundesliga": "Germany",
    "Italy Serie A": "Italy", "Italy Serie B": "Italy",
    "Spanish Primera Division": "Spain", "Spanish Segunda Division": "Spain",
    "French Ligue 1": "France", "French Ligue 2": "France",
    "Dutch Eredivisie": "Netherlands", "Belgian Jupiler League": "Belgium", "Portuguese Liga": "Portugal",
    "Turkish Turkcell Super Lig": "Turkey", "Greek Super League": "Greece",
    "UEFA Champions League": "World", "UEFA Europa League": "World", "UEFA Europa Conference League": "World",
    "Argentina Primera Division": "Argentina", "Brasileiro Série A": "Brazil", "Major League Soccer": "USA",
    "United Soccer League": "USA", "Mexican Primera Division Torneo Apertura": "Mexico",
    "Mexican Primera Division Torneo Clausura": "Mexico", "Japanese J League": "Japan",
    "Chinese Super League": "China", "South African ABSA Premier League": "South-Africa",
    "Swedish Allsvenskan": "Sweden", "Norwegian Tippeligaen": "Norway", "Danish SAS-Ligaen": "Denmark",
    "Swiss Raiffeisen Super League": "Switzerland", "Austrian T-Mobile Bundesliga": "Austria",
    "Russian Premier Liga": "Russia", "Australian A-League": "Australia",
}
# Fútbol femenino: el CSV es solo masculino. Sin esto, un "Arsenal" de la liga
# femenina heredaría el historial del Arsenal masculino.
WOMEN_MARKERS = ("women", "femen", "feminin", "frauen", "nwsl", "wsl")


def competition_country(league_name: str | None, country: str | None) -> str | None:
    """País real de una competición para la guarda del emparejamiento de nombres.
    Devuelve un valor no cubierto ("Women", "Unknown") cuando no se debe emparejar."""
    name = (league_name or "").lower()
    if any(w in name for w in WOMEN_MARKERS):
        return "Women"
    if country == SPI_SOURCE_TAG:
        return SPI_LEAGUE_COUNTRY.get(league_name or "", "Unknown")
    return country
# Palabras de "forma jurídica" que API-Football incluye y football-data omite
# ("1. FC Magdeburg" -> "Magdeburg", "SV Darmstadt 98" -> "Darmstadt").
NAME_NOISE = frozenset({
    "fc", "cf", "sc", "sv", "vfl", "vfb", "spvgg", "afc", "ac", "as", "ss", "us", "ssc", "rcd", "cd", "ud",
    "sd", "1", "club", "calcio", "bk", "fk", "if", "sk", "ff", "tsg", "bsc", "fsv", "sg", "ssv",
    "1899", "04", "05", "98", "1846", "1860", "1904", "1909", "1910", "1913",
})
# Similitud mínima (difflib) para aceptar un emparejamiento difuso: "karlsruher"
# ~ "karlsruhe" (0.95), "bristol rovers" ~ "bristol rvs" (0.88). Solo se acepta
# si hay UN candidato; ante la duda, sin predicción.
FUZZY_CUTOFF = 0.88
# Solo equipos con partidos recientes compiten en el emparejamiento difuso: un
# club desaparecido hace 15 años no debe robarle el nombre a uno actual.
RESOLVER_RECENT_DAYS = 3 * 365


def _name_core(key: str) -> str:
    return " ".join(t for t in re.split(r"[\s\-']+", key) if t and t not in NAME_NOISE)


class TeamResolver:
    """Nombre de API-Football -> clave de equipo del CSV.

    Orden: alias manual, nombre normalizado exacto, "núcleo" sin palabras de
    forma jurídica (único), y similitud difusa (única y >= FUZZY_CUTOFF).
    Con `country` (el de la liga del partido):
      - liga de un país cubierto -> la división del equipo debe ser de ese país;
      - liga de un país NO cubierto (Argentina...) -> sin clave: esos equipos
        no están en el CSV aunque se llamen igual que uno europeo;
      - competición internacional ("World") -> solo alias/exacto, nada difuso.
    Medido el 22/09: recupera 5 de 60 partidos con cuotas que se perdían solo
    por el nombre (+17% de partidos con predicción), sin emparejamientos falsos.
    """

    def __init__(self, last_seen: dict[str, tuple[np.datetime64, str]]):
        self.last_division = {k: div for k, (_, div) in last_seen.items()}
        if last_seen:
            newest = max(d for d, _ in last_seen.values())
            recent_since = newest - np.timedelta64(RESOLVER_RECENT_DAYS, "D")
        else:
            recent_since = None
        self._by_core: dict[str, list[str]] = {}
        for k, (d, _) in last_seen.items():
            if recent_since is None or d >= recent_since:
                self._by_core.setdefault(_name_core(k), []).append(k)

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> "TeamResolver":
        long = pd.concat([
            pd.DataFrame({"key": df["home_key"], "date": df["MatchDate"], "div": df["Division"]}),
            pd.DataFrame({"key": df["away_key"], "date": df["MatchDate"], "div": df["Division"]}),
        ]).sort_values("date", kind="stable")
        last = long.groupby("key", sort=False).last()
        return cls({k: (np.datetime64(r["date"]), str(r["div"])) for k, r in last.iterrows()})

    def resolve(self, team_name: str, country: str | None = None) -> str | None:
        key = _resolve_key(team_name)
        international = country is not None and country not in COVERED_COUNTRIES
        if international and country != "World":
            return None
        if key not in self.last_division:
            if international:
                return None
            key = self._fuzzy(key)
            if key is None:
                return None
        if country in COVERED_COUNTRIES and DIVISION_COUNTRY.get(self.last_division[key]) != country:
            return None
        return key

    def _fuzzy(self, key: str) -> str | None:
        core = _name_core(key)
        exact = self._by_core.get(core, [])
        if len(exact) == 1:
            return exact[0]
        if exact:
            return None  # ambiguo
        close = difflib.get_close_matches(core, list(self._by_core), n=2, cutoff=FUZZY_CUTOFF)
        if len(close) == 1 and len(self._by_core[close[0]]) == 1:
            return self._by_core[close[0]][0]
        return None


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


@lru_cache(maxsize=1)
def default_resolver() -> TeamResolver:
    return TeamResolver.from_frame(_load_raw())


# Columnas de `matches` (BD) con la misma estadística, para completar el CSV con
# partidos recientes traídos de API-Football: (col local, col visitante, peso).
DB_STAT_COLUMNS = {
    "corners": (("home_corners", "away_corners", 1.0),),
    "shots": (("home_shots", "away_shots", 1.0),),
    "sot": (("home_shots_on_target", "away_shots_on_target", 1.0),),
    "cards": (("home_yellow", "away_yellow", 1.0), ("home_red", "away_red", 2.0)),
    "fouls": (("home_fouls", "away_fouls", 1.0),),
}
# Guarda de consistencia del complemento: la API y football-data no tienen por
# qué contar igual (¿un tiro bloqueado es "tiro"?). Antes de mezclar fuentes se
# compara el total medio de los partidos de la API con el del CSV en las mismas
# ligas; si difiere más que la tolerancia (o hay pocos partidos para saberlo),
# esa estadística NO se complementa.
OVERLAY_MIN_MATCHES = 20
OVERLAY_TOLERANCE = 0.12
OVERLAY_REFERENCE_DAYS = 60


def overlay_for_spec(spec: StatSpec, raw: pd.DataFrame, base: pd.DataFrame) -> tuple[pd.DataFrame | None, dict]:
    """Filas del complemento (partidos de la API posteriores al CSV) en el formato
    genérico de `load_stat_frame`, y un informe de la guarda de consistencia.

    `raw`: Division, MatchDate, HomeTeam, AwayTeam, home_key, away_key y las
    columnas de DB_STAT_COLUMNS. Solo entran partidos posteriores al último del
    CSV en SU división: cuando el espejo se actualice, dejan de usarse solos."""
    cols = DB_STAT_COLUMNS[spec.key]
    needed = [c for h, a, _ in cols for c in (h, a)]
    df = raw.dropna(subset=needed).copy()
    last_csv = base.groupby("Division")["MatchDate"].max()
    df = df[df["Division"].map(last_csv).notna()]
    df = df[df["MatchDate"] > df["Division"].map(last_csv)]
    report: dict = {"n": int(len(df)), "used": False}
    if df.empty:
        report["reason"] = "sin partidos"
        return None, report
    home = sum(w * df[h].astype(float) for h, _, w in cols)
    away = sum(w * df[a].astype(float) for _, a, w in cols)
    out = df[["Division", "MatchDate", "HomeTeam", "AwayTeam", "home_key", "away_key"]].copy()
    out["home_value"] = home.to_numpy()
    out["away_value"] = away.to_numpy()

    # Media de referencia por división: sus últimos OVERLAY_REFERENCE_DAYS en el CSV.
    ref = {}
    for div, last in last_csv.items():
        rows = base[(base["Division"] == div) & (base["MatchDate"] > last - pd.Timedelta(days=OVERLAY_REFERENCE_DAYS))]
        if len(rows):
            totals = rows["home_value"] + rows["away_value"]
            ref[div] = (float(totals.mean()), float(totals.std()))
    totals = out["home_value"] + out["away_value"]
    expected = out["Division"].map(lambda d: ref.get(d, (np.nan, np.nan))[0])
    spread = out["Division"].map(lambda d: ref.get(d, (np.nan, np.nan))[1])
    mask = expected.notna()
    n = int(mask.sum())
    report["n"] = n
    if n < OVERLAY_MIN_MATCHES:
        report["reason"] = f"solo {n} partidos (< {OVERLAY_MIN_MATCHES}) para verificar que la API cuenta igual"
        return None, report
    actual_mean, expected_mean = float(totals[mask].mean()), float(expected[mask].mean())
    ratio = actual_mean / expected_mean
    # Tolerancia: la fija o 3 errores estándar, lo que sea mayor (con pocos
    # partidos de tarjetas, 12% está dentro del ruido).
    se = float(np.sqrt((spread[mask] ** 2).mean() / n)) / expected_mean
    tol = max(OVERLAY_TOLERANCE, 3 * se)
    report.update(actual_mean=round(actual_mean, 2), expected_mean=round(expected_mean, 2),
                  ratio=round(ratio, 3), tolerance=round(tol, 3))
    if abs(ratio - 1) > tol:
        report["reason"] = f"la API cuenta distinto: {ratio:.2f}x lo del CSV (tolerancia ±{tol:.0%})"
        return None, report
    report["used"] = True
    return out[mask].reset_index(drop=True), report


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
    def __init__(self, spec: StatSpec, df: pd.DataFrame | None = None, calibration_path: Path | None | str = "default",
                 resolver: TeamResolver | None = None):
        self.spec = spec
        self.df = df if df is not None else load_stat_frame(spec)
        if resolver is None:
            resolver = default_resolver() if df is None else TeamResolver.from_frame(self.df)
        self.resolver = resolver
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

    def team_profile(self, team_name: str, as_of: date | datetime, country: str | None = None) -> TeamProfile | None:
        """Tasas rodantes del equipo usando SOLO partidos anteriores a `as_of`
        (sin fuga temporal). Devuelve None si no hay historial suficiente.
        `country`: país de la liga del partido (guarda del emparejamiento)."""
        key = self.resolver.resolve(team_name, country)
        entry = self._teams.get(key) if key is not None else None
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

    def expected_values(self, home_team: str, away_team: str, as_of: date | datetime,
                        country: str | None = None) -> tuple[float, float] | None:
        """(λ_local, λ_visitante) o None si a alguno le falta historial."""
        home = self.team_profile(home_team, as_of, country)
        away = self.team_profile(away_team, as_of, country)
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

    def market_probabilities(self, home_team: str, away_team: str, as_of: date | datetime,
                             country: str | None = None, scale: float = 1.0) -> dict[str, float] | None:
        """Probabilidades por línea, calibradas si hay calibrador. None si falta historial.
        `scale` multiplica el total esperado (factor árbitro en tarjetas)."""
        exp = self.expected_values(home_team, away_team, as_of, country)
        if exp is None:
            return None
        return apply_calibration(over_under_probabilities(sum(exp) * scale, self.spec), self.calibrators, self.spec)


def total_cdf(total_lambda: float, dispersion: float) -> np.ndarray:
    """CDF del total en 0..MAX_COUNT: Binomial Negativa con Var = μ + α·μ²
    (parametrización n = 1/α, p = n / (n + μ)); Poisson si α = 0."""
    k = np.arange(MAX_COUNT + 1)
    if dispersion <= 0:
        return poisson.cdf(k, total_lambda)
    n = 1.0 / dispersion
    return nbinom.cdf(k, n, n / (n + total_lambda))


def over_under_probabilities(total_lambda: float, spec: StatSpec) -> dict[str, float]:
    """P(total > línea) para cada línea del spec, bajo total ~ NB(μ, α) (Poisson si α = 0)."""
    cdf = total_cdf(total_lambda, spec.dispersion)
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
    """Los cinco modelos cargados una vez (comparten el CSV en memoria).

    `overlay_raw`: partidos recientes de la BD (ver count_stats_overlay.py) que
    completan el CSV mientras el espejo no se actualiza. Medido el 22/09: con
    19 días de retraso, tiros y faltas perdían ~0.5-1 punto de acierto."""

    def __init__(self, specs: tuple[StatSpec, ...] = ALL_SPECS, overlay_raw: pd.DataFrame | None = None):
        self.specs = specs
        self.resolver = default_resolver()
        self.build(overlay_raw)

    def build(self, overlay_raw: pd.DataFrame | None) -> None:
        from ml.features.referees import RefereeFactors  # evita import circular

        # None si no hay tabla o si su validación no fue significativa.
        self.referees = RefereeFactors.load()
        self.overlay_report: dict[str, dict] = {}
        models = {}
        for spec in self.specs:
            base = load_stat_frame(spec)
            df = base
            if overlay_raw is not None and len(overlay_raw):
                extra, report = overlay_for_spec(spec, overlay_raw, base)
                self.overlay_report[spec.key] = report
                if extra is not None:
                    df = pd.concat([base, extra], ignore_index=True).sort_values("MatchDate", kind="stable")
            models[spec.key] = CountStatModel(spec, df=df, resolver=self.resolver)
        self.models = models
        self.built_at = time.monotonic()

    def age_hours(self) -> float:
        return (time.monotonic() - self.built_at) / 3600

    def _scale(self, key: str, model: "CountStatModel", home_team: str, away_team: str, as_of,
               country: str | None, referee: str | None) -> float:
        """Factor árbitro: solo tarjetas (en faltas no mejoró) y solo si la tabla
        pasó su validación (ml/training/build_referee_factors.py)."""
        if key != "cards" or self.referees is None or not referee:
            return 1.0
        exp = model.expected_values(home_team, away_team, as_of, country)
        return self.referees.factor(country, referee, sum(exp)) if exp else 1.0

    def market_probabilities(self, home_team: str, away_team: str, as_of: date | datetime,
                             country: str | None = None, referee: str | None = None) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, model in self.models.items():
            scale = self._scale(key, model, home_team, away_team, as_of, country, referee)
            probs = model.market_probabilities(home_team, away_team, as_of, country, scale)
            if probs:
                out.update(probs)
        return out

    def expected_totals(self, home_team: str, away_team: str, as_of: date | datetime,
                        country: str | None = None, referee: str | None = None) -> dict[str, float | None]:
        """Total esperado por estadística (para diagnósticos / UI)."""
        out = {}
        for key, model in self.models.items():
            exp = model.expected_values(home_team, away_team, as_of, country)
            scale = self._scale(key, model, home_team, away_team, as_of, country, referee)
            out[key] = sum(exp) * scale if exp else None
        return out
