"""Modelo de goles esperados basado en distribución de Poisson (Dixon-Coles simplificado).

Idea central: los goles marcados por cada equipo se modelan como variables
Poisson independientes cuya media (lambda) depende de:
  - fuerza de ataque del equipo (attack_strength)
  - fuerza de defensa del rival (defense_strength)
  - factor local/visitante
  - xG rodante reciente (blend con goles reales para reducir varianza)
"""
import numpy as np
from scipy.stats import poisson

MAX_GOALS = 8  # suficiente para cubrir >99.5% de la masa de probabilidad


def team_strengths(
    team_avg_goals_scored: float,
    team_avg_goals_conceded: float,
    league_avg_goals: float,
) -> tuple[float, float]:
    """Normaliza goles del equipo contra el promedio de la liga (Dixon-Coles)."""
    attack = team_avg_goals_scored / league_avg_goals
    defense = team_avg_goals_conceded / league_avg_goals
    return attack, defense


def expected_goals(
    home_attack: float,
    home_defense: float,
    away_attack: float,
    away_defense: float,
    league_avg_home_goals: float,
    league_avg_away_goals: float,
    home_xg_recent: float | None = None,
    away_xg_recent: float | None = None,
    xg_blend_weight: float = 0.4,
) -> tuple[float, float]:
    """Devuelve (lambda_home, lambda_away): goles esperados para cada equipo."""
    lambda_home = home_attack * away_defense * league_avg_home_goals
    lambda_away = away_attack * home_defense * league_avg_away_goals

    if home_xg_recent is not None:
        lambda_home = (1 - xg_blend_weight) * lambda_home + xg_blend_weight * home_xg_recent
    if away_xg_recent is not None:
        lambda_away = (1 - xg_blend_weight) * lambda_away + xg_blend_weight * away_xg_recent

    return max(lambda_home, 0.05), max(lambda_away, 0.05)


def scoreline_matrix(lambda_home: float, lambda_away: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Matriz P(home=i, away=j) asumiendo independencia Poisson."""
    home_probs = poisson.pmf(np.arange(max_goals + 1), lambda_home)
    away_probs = poisson.pmf(np.arange(max_goals + 1), lambda_away)
    return np.outer(home_probs, away_probs)


def market_probabilities(lambda_home: float, lambda_away: float) -> dict[str, float]:
    """Deriva probabilidades de mercado (1X2, Over/Under, BTTS) desde la matriz de marcadores."""
    matrix = scoreline_matrix(lambda_home, lambda_away)

    p_home = float(np.sum(np.tril(matrix, -1)))
    p_draw = float(np.sum(np.diag(matrix)))
    p_away = float(np.sum(np.triu(matrix, 1)))

    goals_grid = np.add.outer(np.arange(matrix.shape[0]), np.arange(matrix.shape[1]))
    p_over_15 = float(np.sum(matrix[goals_grid > 1.5]))
    p_over_25 = float(np.sum(matrix[goals_grid > 2.5]))
    p_btts_yes = float(np.sum(matrix[1:, 1:]))

    return {
        "1X2_HOME": p_home,
        "1X2_DRAW": p_draw,
        "1X2_AWAY": p_away,
        "DOUBLE_CHANCE_1X": p_home + p_draw,
        "DOUBLE_CHANCE_X2": p_draw + p_away,
        "DOUBLE_CHANCE_12": p_home + p_away,
        "OU_1.5_OVER": p_over_15,
        "OU_1.5_UNDER": 1 - p_over_15,
        "OU_2.5_OVER": p_over_25,
        "OU_2.5_UNDER": 1 - p_over_25,
        "BTTS_YES": p_btts_yes,
        "BTTS_NO": 1 - p_btts_yes,
    }
