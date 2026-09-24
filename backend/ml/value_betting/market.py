"""Probabilidad implícita del mercado SIN el margen de la casa.

Una casa publica, para un mercado cuyos resultados son excluyentes y cubren
todos los casos (Over/Under, 1X2, Sí/No...), cuotas cuyas inversas suman
1 + margen (~1.07 en tiros, tarjetas y córners). Dividir cada 1/cuota por
esa suma da la probabilidad "justa" que la casa le asigna a cada resultado
(método proporcional). Se toma la mediana entre casas para no depender de
una sola que llegue tarde a un movimiento de línea.

Es la vara contra la que se mide el modelo (ver MarketEvaluation): el modelo
solo aporta algo si predice mejor que esta probabilidad.
"""
import statistics
from collections import defaultdict

# Suma de 1/cuota fuera de este rango = cuotas incoherentes (arbitraje, una
# selección desactualizada, error de carga): esa casa no cuenta para ese mercado.
MIN_OVERROUND = 0.98   # los exchanges (Betfair) rondan 1.00
MAX_OVERROUND = 1.35


def complete_selection_set(market: str) -> frozenset[str] | None:
    """Selecciones que cubren todos los resultados del mercado, o None si el
    mercado no es una partición (doble oportunidad se solapa; marcador exacto
    nunca se publica completo)."""
    if market == "1X2":
        return frozenset({"HOME", "DRAW", "AWAY"})
    if market == "BTTS":
        return frozenset({"YES", "NO"})
    if market.startswith("AH_") or market == "ML" or market.startswith("SPREAD_"):
        return frozenset({"HOME", "AWAY"})
    if market.startswith("TOTAL_"):
        return frozenset({"OVER", "UNDER"})
    prefix, _, line = market.rpartition("_")
    if (prefix == "OU" or prefix.endswith("_OU") or prefix.startswith("TEAM_OU_")) and line.replace(".", "", 1).isdigit():
        return frozenset({"OVER", "UNDER"})
    return None


def fair_probabilities(market: str, odds_by_bookmaker: dict[str, dict[str, float]]) -> tuple[dict[str, float], int]:
    """({selección: probabilidad sin margen}, nº de casas usadas).

    `odds_by_bookmaker`: {casa: {selección: cuota}}. Solo cuentan las casas que
    publican TODAS las selecciones del mercado (sin el otro lado no se puede
    saber cuánto margen hay)."""
    full = complete_selection_set(market)
    if full is None:
        return {}, 0
    per_selection: dict[str, list[float]] = defaultdict(list)
    used = 0
    for selections in odds_by_bookmaker.values():
        if set(selections) != full or any(o <= 1.0 for o in selections.values()):
            continue
        inverse = {s: 1.0 / o for s, o in selections.items()}
        overround = sum(inverse.values())
        if not MIN_OVERROUND <= overround <= MAX_OVERROUND:
            continue
        used += 1
        for s, v in inverse.items():
            per_selection[s].append(v / overround)
    if not used:
        return {}, 0
    medians = {s: statistics.median(v) for s, v in per_selection.items()}
    total = sum(medians.values())
    return {s: v / total for s, v in medians.items()}, used
