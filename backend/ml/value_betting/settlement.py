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


# Mercados de conteo -> clave de `stats` con el total del partido. Las
# tarjetas ya vienen combinadas (amarilla = 1, roja = 2), igual que en el
# modelo y que en la convención de las casas.
COUNT_MARKET_STATS = {
    "CORNERS_OU": "corners",
    "SHOTS_OU": "shots",
    "SOT_OU": "shots_on_target",
    "CARDS_OU": "cards",
    "FOULS_OU": "fouls",
}


def bet_won(
    market: str,
    selection: str,
    home_goals: int,
    away_goals: int,
    home_corners: int | None = None,
    away_corners: int | None = None,
    stats: dict[str, int | float | None] | None = None,
) -> bool:
    """`stats`: totales del partido por estadística ({"shots": 24, ...}); los
    córners pueden venir ahí o por los parámetros históricos home/away_corners."""
    total_goals = home_goals + away_goals

    prefix, _, line_str = market.rpartition("_")
    if prefix in COUNT_MARKET_STATS:
        stat = COUNT_MARKET_STATS[prefix]
        total = (stats or {}).get(stat)
        if total is None and stat == "corners" and home_corners is not None and away_corners is not None:
            total = home_corners + away_corners
        if total is None:
            raise MissingStatsError(f"El mercado {market} necesita '{stat}' del partido y aún no está")
        line = float(line_str)
        if selection == "OVER":
            return total > line
        if selection == "UNDER":
            return total < line
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

    elif market.startswith("TEAM_OU_"):
        # TEAM_OU_HOME_1.5 / TEAM_OU_AWAY_1.5
        _, _, side, line = market.split("_")
        goals = home_goals if side == "HOME" else away_goals
        if selection == "OVER":
            return goals > float(line)
        if selection == "UNDER":
            return goals < float(line)

    elif market.startswith("AH_"):
        # AH_-1.5: línea del LOCAL. Solo líneas .5 (sin devoluciones).
        line = float(market.removeprefix("AH_"))
        if line != int(line) + 0.5 and line != int(line) - 0.5:
            raise UnknownMarketError(f"Hándicap {market} no es línea .5: implica devolución y no se liquida")
        home_covers = home_goals + line > away_goals
        if selection == "HOME":
            return home_covers
        if selection == "AWAY":
            return not home_covers

    elif market == "ML":
        # NBA: ganador con prórroga incluida (no hay empate).
        if home_goals == away_goals:
            raise UnknownMarketError("ML sin ganador: el marcador final no puede ser empate")
        if selection == "HOME":
            return home_goals > away_goals
        if selection == "AWAY":
            return away_goals > home_goals

    elif market.startswith(("SPREAD_", "TOTAL_")):
        # NBA. SPREAD_<línea del local>: HOME cubre si local + línea > visitante.
        # TOTAL_<línea>: puntos totales. Solo líneas .5 (sin devolución).
        kind, _, line_str = market.partition("_")
        line = float(line_str)
        if line == int(line):
            raise UnknownMarketError(f"{market} es línea entera: implica devolución y no se liquida")
        if kind == "SPREAD":
            home_covers = home_goals + line > away_goals
            if selection == "HOME":
                return home_covers
            if selection == "AWAY":
                return not home_covers
        elif selection == "OVER":
            return total_goals > line
        elif selection == "UNDER":
            return total_goals < line

    elif market == "EXACT_SCORE":
        # selection "2-1"
        h, a = selection.split("-")
        return home_goals == int(h) and away_goals == int(a)

    elif market == "BTTS":
        if selection == "YES":
            return home_goals > 0 and away_goals > 0
        if selection == "NO":
            return home_goals == 0 or away_goals == 0

    raise UnknownMarketError(f"No sé resolver el mercado '{market}' con la selección '{selection}'")


def profit_loss(won: bool, odds: float, stake: float = 1.0) -> float:
    """Ganancia neta de la apuesta. Ganar paga (cuota - 1) por unidad; perder cuesta el stake."""
    return stake * (odds - 1.0) if won else -stake
