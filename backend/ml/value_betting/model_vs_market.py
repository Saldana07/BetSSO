"""¿El modelo predice mejor que la casa? Comparación sobre mercados liquidados.

Para cada mercado evaluado y liquidado hay dos probabilidades del mismo
suceso: la del modelo y la de la casa sin margen. El Brier (error cuadrático)
de cada una dice quién acertó mejor. La unidad independiente es el PARTIDO
(las líneas de un mismo partido están muy correlacionadas: si hubo 30 tiros,
fallan o aciertan juntas), así que la diferencia se promedia por partido y el
error estándar se calcula entre partidos. Solo hay veredicto con
MIN_MATCHES partidos y una diferencia de al menos 2 errores estándar.
"""
import math
from collections import defaultdict
from dataclasses import dataclass

MIN_MATCHES = 30
T_THRESHOLD = 2.0

# Prefijo de mercado -> familia que se muestra.
FAMILIES = {
    "CORNERS_OU": "CORNERS", "SHOTS_OU": "SHOTS", "SOT_OU": "SOT", "CARDS_OU": "CARDS", "FOULS_OU": "FOULS",
    "1X2": "1X2", "BTTS": "BTTS", "OU": "GOALS_OU", "TEAM_OU": "GOALS_OU", "AH": "AH",
}


def market_family(market: str) -> str:
    if market in FAMILIES:
        return FAMILIES[market]
    if market.startswith("TEAM_OU_"):
        return "GOALS_OU"
    if market.startswith("AH_"):
        return "AH"
    prefix = market.rpartition("_")[0]
    return FAMILIES.get(prefix, "OTRO")


@dataclass
class EvaluationRow:
    match_id: int
    market: str
    model_probability: float
    market_probability: float
    won: bool


@dataclass
class FamilyComparison:
    family: str
    n_matches: int
    n_markets: int
    brier_model: float
    brier_market: float
    t_stat: float | None
    verdict: str   # MODELO | CASA | EMPATE | POCA_MUESTRA


def compare(rows: list[EvaluationRow]) -> list[FamilyComparison]:
    by_family: dict[str, list[EvaluationRow]] = defaultdict(list)
    for r in rows:
        by_family[market_family(r.market)].append(r)

    out = []
    for family, fam_rows in by_family.items():
        per_match: dict[int, list[float]] = defaultdict(list)
        bm = bc = 0.0
        markets = set()
        for r in fam_rows:
            y = 1.0 if r.won else 0.0
            em, ec = (r.model_probability - y) ** 2, (r.market_probability - y) ** 2
            bm += em
            bc += ec
            per_match[r.match_id].append(em - ec)
            markets.add((r.match_id, r.market))
        n = len(fam_rows)
        diffs = [sum(v) / len(v) for v in per_match.values()]
        k = len(diffs)
        t = None
        if k >= 2:
            mean = sum(diffs) / k
            var = sum((d - mean) ** 2 for d in diffs) / (k - 1)
            se = math.sqrt(var / k)
            t = mean / se if se > 0 else None
        if k < MIN_MATCHES or t is None:
            verdict = "POCA_MUESTRA"
        elif t <= -T_THRESHOLD:
            verdict = "MODELO"      # diferencia negativa = el modelo se equivoca menos
        elif t >= T_THRESHOLD:
            verdict = "CASA"
        else:
            verdict = "EMPATE"
        out.append(FamilyComparison(family, k, len(markets), bm / n, bc / n, t, verdict))
    return sorted(out, key=lambda c: -c.n_matches)
