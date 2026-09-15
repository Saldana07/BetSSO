"""Sistema Elo específico para fútbol (variante World Football Elo Ratings).

Incorpora:
- Margen de victoria (goal difference multiplier) — ganar 4-0 pesa más que 1-0.
- Ventaja de local (home advantage) como offset de rating.
- K-factor ajustable por importancia de la competición.
"""
from dataclasses import dataclass

HOME_ADVANTAGE = 60.0   # puntos Elo equivalentes a jugar de local
BASE_K = 20.0


@dataclass
class EloUpdateResult:
    home_elo_after: float
    away_elo_after: float
    home_win_prob: float


def _expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _goal_diff_multiplier(goal_diff: int) -> float:
    """Ajuste estándar usado en World Football Elo: castiga/premia goleadas."""
    if goal_diff <= 1:
        return 1.0
    if goal_diff == 2:
        return 1.5
    return (11 + goal_diff) / 8.0


def update_elo(
    home_elo: float,
    away_elo: float,
    home_goals: int,
    away_goals: int,
    k_factor: float = BASE_K,
) -> EloUpdateResult:
    home_elo_adj = home_elo + HOME_ADVANTAGE
    home_expected = _expected_score(home_elo_adj, away_elo)

    if home_goals > away_goals:
        home_actual = 1.0
    elif home_goals == away_goals:
        home_actual = 0.5
    else:
        home_actual = 0.0

    multiplier = _goal_diff_multiplier(abs(home_goals - away_goals))
    delta = k_factor * multiplier * (home_actual - home_expected)

    return EloUpdateResult(
        home_elo_after=home_elo + delta,
        away_elo_after=away_elo - delta,
        home_win_prob=home_expected,
    )


def elo_win_draw_loss_probs(home_elo: float, away_elo: float, draw_factor: float = 0.28) -> tuple[float, float, float]:
    """Convierte Elo a probabilidades 1X2 aproximadas.

    draw_factor controla el "ancho" de la probabilidad de empate; se calibra
    empíricamente contra el histórico de la liga (valor típico 0.25-0.32).
    """
    home_elo_adj = home_elo + HOME_ADVANTAGE
    diff = home_elo_adj - away_elo

    p_home_raw = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    p_draw = draw_factor * (1.0 - abs(2 * p_home_raw - 1.0))
    p_home = p_home_raw * (1.0 - p_draw)
    p_away = 1.0 - p_home - p_draw
    return p_home, p_draw, p_away
