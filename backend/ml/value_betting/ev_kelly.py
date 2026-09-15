"""Expected Value y Criterio de Kelly (fraccional) para value betting."""
from dataclasses import dataclass


@dataclass
class ValueBet:
    model_probability: float
    bookmaker_odds: float
    implied_probability: float
    expected_value: float
    kelly_fraction: float


def implied_probability(odds: float) -> float:
    return 1.0 / odds


def expected_value(model_probability: float, odds: float) -> float:
    """EV por unidad apostada: (prob_modelo * cuota) - 1.

    EV > 0 significa que el modelo considera el evento más probable de lo
    que sugiere la cuota de la casa (ineficiencia de mercado / value bet).
    """
    return (model_probability * odds) - 1.0


def kelly_criterion(model_probability: float, odds: float, fraction_cap: float = 0.25) -> float:
    """Kelly fraccional: f* = (bp - q) / b, capado a `fraction_cap` por gestión de riesgo.

    b = odds - 1 (ganancia neta por unidad), p = prob modelo, q = 1 - p.
    Se aplica Kelly fraccionado (ej. 1/4 Kelly) porque el Kelly completo es
    demasiado agresivo dado el error de estimación del modelo.
    """
    b = odds - 1.0
    p = model_probability
    q = 1.0 - p
    if b <= 0:
        return 0.0

    full_kelly = (b * p - q) / b
    full_kelly = max(full_kelly, 0.0)  # nunca apostar con Kelly negativo (no hay edge)
    return min(full_kelly, 1.0) * fraction_cap


def evaluate_bet(model_probability: float, odds: float, fraction_cap: float = 0.25) -> ValueBet:
    return ValueBet(
        model_probability=model_probability,
        bookmaker_odds=odds,
        implied_probability=implied_probability(odds),
        expected_value=expected_value(model_probability, odds),
        kelly_fraction=kelly_criterion(model_probability, odds, fraction_cap),
    )


def is_safe_bet_candidate(
    odds: float,
    expected_val: float,
    historical_accuracy: float | None,
    odds_min: float = 1.50,
    odds_max: float = 2.00,
    min_ev: float = 0.0,
    min_historical_accuracy: float = 0.55,
    max_ev: float | None = None,
) -> bool:
    """Filtro estricto para el pick del día: rango de cuota + EV positivo +
    accuracy histórica mínima + techo de EV plausible.

    El techo (`max_ev`) es contraintuitivo pero esencial: en un mercado
    líquido, una ventaja real vive entre +2% y +8%. Si nuestro modelo dice
    tener +30%, lo abrumadoramente probable es que el modelo esté mal
    calibrado para ese partido (ej. sin historial de esos equipos, devuelve
    la tasa base de la población) y no que la casa de apuestas haya cometido
    un error gigante. Sin este techo, el ranking por EV selecciona justo los
    partidos donde MÁS nos equivocamos.
    """
    if not (odds_min <= odds <= odds_max):
        return False
    if expected_val <= min_ev:
        return False
    if max_ev is not None and expected_val > max_ev:
        return False
    if historical_accuracy is not None and historical_accuracy < min_historical_accuracy:
        return False
    return True
