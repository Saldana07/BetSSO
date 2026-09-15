"""Resolución de apuestas: dado el marcador final, ¿ganó la selección?

Esta es la pieza de la que depende TODA la medición de desempeño real. Si
resuelve mal un mercado, el histórico de rentabilidad queda corrupto y las
decisiones que se tomen sobre él también. Por eso vive aislada y tiene tests
(tests/test_settlement.py).

Convención de empujes ("push"): en estas líneas no existen. Todas las líneas
de goles usadas son .5 (1.5, 2.5), así que nunca hay empate exacto contra la
línea; y en Doble Oportunidad el empate ya está contemplado en la selección.
"""


class UnknownMarketError(ValueError):
    pass


class MissingStatsError(ValueError):
    """El mercado necesita una estadística (ej. córners) que el partido aún no tiene."""


def bet_won(
    market: str,
    selection: str,
    home_goals: int,
    away_goals: int,
    home_corners: int | None = None,
    away_corners: int | None = None,
) -> bool:
    total_goals = home_goals + away_goals

    if market.startswith("CORNERS_OU_"):
        if home_corners is None or away_corners is None:
            raise MissingStatsError(f"El mercado {market} necesita los córners del partido y aún no están")
        line = float(market.removeprefix("CORNERS_OU_"))
        total_corners = home_corners + away_corners
        if selection == "OVER":
            return total_corners > line
        if selection == "UNDER":
            return total_corners < line
        raise UnknownMarketError(f"Selección desconocida '{selection}' para {market}")

    if market == "1X2":
        if selection == "HOME":
            return home_goals > away_goals
        if selection == "DRAW":
            return home_goals == away_goals
        if selection == "AWAY":
            return away_goals > home_goals

    elif market == "DOUBLE_CHANCE":
        if selection == "1X":
            return home_goals >= away_goals
        if selection == "X2":
            return away_goals >= home_goals
        if selection == "12":
            return home_goals != away_goals

    elif market.startswith("OU_"):
        line = float(market.removeprefix("OU_"))
        if selection == "OVER":
            return total_goals > line
        if selection == "UNDER":
            return total_goals < line

    elif market == "BTTS":
        if selection == "YES":
            return home_goals > 0 and away_goals > 0
        if selection == "NO":
            return home_goals == 0 or away_goals == 0

    raise UnknownMarketError(f"No sé resolver el mercado '{market}' con la selección '{selection}'")


def profit_loss(won: bool, odds: float, stake: float = 1.0) -> float:
    """Ganancia neta de la apuesta. Ganar paga (cuota - 1) por unidad; perder cuesta el stake."""
    return stake * (odds - 1.0) if won else -stake
