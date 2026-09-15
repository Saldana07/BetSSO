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


def closing_line_value(taken_odds: float, closing_odds: float) -> float:
    """Closing Line Value: cuánto mejor fue la cuota que tomamos respecto a la
    de cierre. Positivo = el mercado se movió a nuestro favor tras apostar.

    Es la medida profesional de ventaja porque converge mucho más rápido que
    el P&L: acertar o fallar un partido es azar, pero tomar sistemáticamente
    precios mejores que el cierre no lo es. Tomar 2.00 y que cierre en 1.85
    da +8.1%: el mercado terminó dándonos la razón aunque el partido se
    pierda. Si el CLV medio es negativo, no hay ventaja aunque se acierten
    rachas.
    """
    return taken_odds / closing_odds - 1.0


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
    historical_skill: float | None,
    odds_min: float = 1.50,
    odds_max: float = 2.00,
    min_ev: float = 0.0,
    min_historical_skill: float = 0.0,
    max_ev: float | None = None,
    require_validation: bool = True,
) -> bool:
    """Filtro estricto para el pick del día: rango de cuota + EV positivo +
    habilidad demostrada en el mercado + techo de EV plausible.

    Habilidad, no accuracy. La versión anterior exigía `accuracy >= 0.55`, y
    eso rechazaba córners 9.5 (accuracy 53%, pero +1.0 pts sobre su base) y
    aceptaba córners 8.5 (60%, pero solo +0.6 pts): elegía la línea con MENOS
    habilidad solo porque su tasa base era más alta. La accuracy cruda no es
    comparable entre mercados; la habilidad (accuracy - base) sí.

    `require_validation`: un mercado sin backtest no tiene habilidad conocida
    y no debe generar picks. Antes `None` pasaba el filtro en silencio, justo
    donde menos sabíamos.

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
    if historical_skill is None:
        return not require_validation
    return historical_skill >= min_historical_skill
