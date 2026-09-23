"""Factor árbitro para el mercado de TARJETAS.

El histórico de football-data no trae el árbitro; API-Football sí, y en una
sola petición por liga y temporada (plan gratuito: temporadas 2022-2024).
Cada árbitro tiene un factor = tarjetas que sacó / tarjetas que el modelo
esperaba por los equipos en esos mismos partidos, encogido hacia 1 con un
prior de REFEREE_PRIOR_MATCHES partidos. El λ del total de tarjetas se
multiplica por ese factor.

Medido (piloto 22/09: Premier, LaLiga, Serie A; factor calculado solo con
partidos ANTERIORES, test en 2024-25, 1,113 partidos): mejora el Brier en
las 5 líneas de tarjetas (-0.0015 a -0.0035) y +1.2-1.4 puntos de acierto en
las líneas 3.5 y 4.5. En faltas NO mejora (factor 0.86-1.10, efecto dentro del
ruido), así que solo se aplica a tarjetas. La tabla la construye
ml/training/build_referee_factors.py, que repite ese test sobre todas las
ligas descargadas y solo la activa si la mejora es significativa.
"""
import unicodedata
from pathlib import Path

import joblib

from ml.features.count_stats import MODELS_DIR

REFEREE_PATH = MODELS_DIR / "referee_cards.joblib"
REFEREE_PRIOR_MATCHES = 10
_PARTICLES = frozenset({"de", "del", "la", "van", "der", "di", "da", "von"})


def referee_key(name: str | None) -> str | None:
    """'Antonio Alberola Rojas, Spain' / 'A. Alberola' -> 'a alberola'.
    Inicial del nombre + primer apellido: la API escribe al mismo árbitro de
    ambas formas según la temporada."""
    if not name:
        return None
    s = unicodedata.normalize("NFKD", name.split(",")[0])
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower().replace(".", " ")
    tokens = [t for t in s.split() if t]
    if not tokens:
        return None
    rest = [t for t in tokens[1:] if t not in _PARTICLES]
    return f"{tokens[0][0]} {rest[0]}" if rest else tokens[0]


def shrunk_factor(actual_sum: float, expected_sum: float, lam: float, prior: int = REFEREE_PRIOR_MATCHES) -> float:
    """(tarjetas reales + prior·λ) / (esperadas + prior·λ): 1.0 sin historial."""
    return (actual_sum + prior * lam) / (expected_sum + prior * lam)


class RefereeFactors:
    def __init__(self, table: dict[tuple[str, str], tuple[float, float, int]]):
        self.table = table

    @classmethod
    def load(cls, path: Path = REFEREE_PATH) -> "RefereeFactors | None":
        """None si no hay tabla o si su validación no fue significativa."""
        if not Path(path).exists():
            return None
        data = joblib.load(path)
        if not data.get("validated"):
            return None
        return cls(data["table"])

    def factor(self, country: str | None, referee: str | None, lam: float) -> float:
        key = referee_key(referee)
        entry = self.table.get((country, key)) if key else None
        if entry is None:
            return 1.0
        actual_sum, expected_sum, _ = entry
        return shrunk_factor(actual_sum, expected_sum, lam)
