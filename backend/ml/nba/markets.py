"""Probabilidades de la NBA a partir de un margen y un total esperados.

El margen final (local - visitante) y el total de puntos se tratan como
normales con la desviación típica medida en el entrenamiento (~12 y ~19
puntos). Con eso:

    P(gana local)            = P(margen > 0)
    P(local cubre línea L)   = P(margen + L > 0)
    P(más de T puntos)       = P(total > T)

El mercado se lee al revés con la misma fórmula: de la probabilidad sin margen
que una casa da a un lado de la línea sale el margen (o total) que la casa
espera. Así se comparan casas con líneas distintas (Betano a 226.5, Pinnacle a
224.5) y se mezcla lo que espera el mercado con lo que espera el modelo.
"""
import math
from statistics import NormalDist

from ml.value_betting.market import fair_probabilities

_N = NormalDist()
# Evita probabilidades 0/1 y que una línea muy alternativa domine el cálculo.
P_CLIP = (0.03, 0.97)


def _cdf(x: float) -> float:
    return _N.cdf(x)


def market_line(market: str) -> float | None:
    kind, _, line = market.partition("_")
    try:
        return float(line) if kind in ("SPREAD", "TOTAL") else None
    except ValueError:
        return None


def probability(market: str, selection: str, mu_margin: float, mu_total: float,
                sd_margin: float, sd_total: float) -> float | None:
    if market == "ML":
        p_home = 1 - _cdf(-mu_margin / sd_margin)
        return p_home if selection == "HOME" else 1 - p_home if selection == "AWAY" else None
    line = market_line(market)
    if line is None:
        return None
    if market.startswith("SPREAD_"):
        p_home = 1 - _cdf((-line - mu_margin) / sd_margin)
        return p_home if selection == "HOME" else 1 - p_home if selection == "AWAY" else None
    p_over = 1 - _cdf((line - mu_total) / sd_total)
    return p_over if selection == "OVER" else 1 - p_over if selection == "UNDER" else None


def implied_mu(market: str, fair: dict[str, float], sd_margin: float, sd_total: float) -> tuple[str, float] | None:
    """("margin" | "total", valor esperado por la casa) a partir de su
    probabilidad sin margen en ese mercado."""
    if market == "ML" and "HOME" in fair:
        p = min(max(fair["HOME"], P_CLIP[0]), P_CLIP[1])
        return "margin", sd_margin * _N.inv_cdf(p)
    line = market_line(market)
    if line is None:
        return None
    if market.startswith("SPREAD_") and "HOME" in fair:
        p = min(max(fair["HOME"], P_CLIP[0]), P_CLIP[1])
        return "margin", -line + sd_margin * _N.inv_cdf(p)
    if market.startswith("TOTAL_") and "OVER" in fair:
        p = min(max(fair["OVER"], P_CLIP[0]), P_CLIP[1])
        return "total", line + sd_total * _N.inv_cdf(p)
    return None


# Pinnacle es la casa de referencia (margen bajo, acepta apostadores
# ganadores): su línea pesa más al estimar lo que espera el mercado.
SHARP_BOOKS = {"Pinnacle": 3.0}


def market_expectation(odds: dict[str, dict[str, dict[str, float]]], sd_margin: float,
                       sd_total: float) -> dict[str, float | None]:
    """Margen y total que espera el mercado. `odds`: {mercado: {casa: {selección: cuota}}}.

    Cada par (casa, línea) sin margen da una estimación; se promedian pesando
    más las líneas cercanas al 50/50 (las principales, las más líquidas) y
    las casas de referencia. El ganador (ML) solo cuenta si no hay hándicap."""
    acc = {"margin": [0.0, 0.0], "total": [0.0, 0.0]}
    ml_only = []
    for market, books in odds.items():
        for book, sel in books.items():
            fair, n = fair_probabilities(market, {book: sel})
            if not n:
                continue
            est = implied_mu(market, fair, sd_margin, sd_total)
            if est is None:
                continue
            kind, value = est
            p = fair.get("HOME", fair.get("OVER", 0.5))
            weight = max(p * (1 - p) - 0.09, 0.0) * SHARP_BOOKS.get(book, 1.0)   # ~0 fuera de 0.1-0.9
            if market == "ML":
                ml_only.append((value, weight))
                continue
            acc[kind][0] += weight * value
            acc[kind][1] += weight
    out: dict[str, float | None] = {}
    for kind in ("margin", "total"):
        s, w = acc[kind]
        out[kind] = s / w if w > 0 else None
    if out["margin"] is None and ml_only:
        w = sum(x[1] for x in ml_only)
        out["margin"] = sum(v * x for v, x in ml_only) / w if w > 0 else None
    return out


REFERENCE_BOOK = "Pinnacle"
# Un pick de hándicap o total solo en líneas a menos de esto de lo que espera
# el mercado. Las muy apartadas son justo las que en el historial de ESPN
# "ganaban" por ser líneas viejas (ver ml/nba/train.py, compra de precio).
MAX_LINE_GAP = 1.5


def line_gap(market: str, mu_margin: float | None, mu_total: float | None) -> float | None:
    line = market_line(market)
    if line is None:
        return None
    if market.startswith("SPREAD_"):
        return None if mu_margin is None else abs(line + mu_margin)
    return None if mu_total is None else abs(line - mu_total)


def reference_probability(market: str, selection: str, books: dict[str, dict[str, float]], mu_margin: float,
                          mu_total: float, sd_margin: float, sd_total: float, exclude: str | None = None) -> float | None:
    """Probabilidad justa contra la que se mide el precio de `exclude`:
    Pinnacle sin margen en ESE mercado y línea si la tiene; si no, la mediana
    sin margen de las demás casas en esa línea; si nadie más la tiene, la
    normal alrededor de lo que espera el mercado. Nunca la propia casa."""
    others = {b: s for b, s in books.items() if b != exclude}
    for pool in ({REFERENCE_BOOK: others[REFERENCE_BOOK]} if REFERENCE_BOOK in others else None, others):
        if pool:
            fair, n = fair_probabilities(market, pool)
            if n and selection in fair:
                return fair[selection]
    return probability(market, selection, mu_margin, mu_total, sd_margin, sd_total)


def blend(model: float, market: float | None, weight: float) -> float:
    """Lo que espera el mercado + `weight` × (lo que el modelo sabe de más).
    weight = 0: el modelo no aporta nada que el mercado no sepa."""
    if market is None or math.isnan(market):
        return model
    return market + weight * (model - market)
