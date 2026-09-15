"""Forma reciente ponderada, separando rendimiento local/visitante.

Los partidos más recientes pesan más (decaimiento exponencial) y se calcula
por separado el "form as home" y "form as away" porque el rendimiento de un
equipo suele diferir mucho entre condición de local y visitante.
"""
from dataclasses import dataclass

import numpy as np

RESULT_POINTS = {"W": 3, "D": 1, "L": 0}


@dataclass
class MatchResult:
    result: str          # 'W' | 'D' | 'L' desde la perspectiva del equipo
    goals_for: int
    goals_against: int
    xg_for: float | None = None
    xg_against: float | None = None


def weighted_form(
    recent_matches: list[MatchResult],
    window: int = 5,
    decay: float = 0.75,
) -> dict[str, float]:
    """Calcula puntos ponderados, goles y xG ponderados sobre los últimos `window` partidos.

    `decay` < 1 da más peso a los partidos más recientes: el partido más
    reciente tiene peso 1.0, el anterior `decay`, el anterior `decay**2`, etc.
    """
    matches = recent_matches[-window:]
    if not matches:
        return {"weighted_points_pct": 0.5, "weighted_gf": 0.0, "weighted_ga": 0.0, "weighted_xg_diff": 0.0}

    n = len(matches)
    weights = np.array([decay ** (n - 1 - i) for i in range(n)])
    weights = weights / weights.sum()

    points = np.array([RESULT_POINTS[m.result] for m in matches])
    gf = np.array([m.goals_for for m in matches])
    ga = np.array([m.goals_against for m in matches])
    xg_diff = np.array([
        (m.xg_for - m.xg_against) if m.xg_for is not None and m.xg_against is not None else (m.goals_for - m.goals_against)
        for m in matches
    ])

    max_points = 3.0
    return {
        "weighted_points_pct": float(np.dot(weights, points) / max_points),
        "weighted_gf": float(np.dot(weights, gf)),
        "weighted_ga": float(np.dot(weights, ga)),
        "weighted_xg_diff": float(np.dot(weights, xg_diff)),
    }


def build_form_features(
    home_recent: list[MatchResult],
    away_recent: list[MatchResult],
    window: int = 5,
    decay: float = 0.75,
) -> dict[str, float]:
    """Genera el vector de features de forma para un partido (local vs visitante)."""
    home_form = weighted_form(home_recent, window, decay)
    away_form = weighted_form(away_recent, window, decay)
    return {
        "home_weighted_points_pct": home_form["weighted_points_pct"],
        "home_weighted_xg_diff": home_form["weighted_xg_diff"],
        "away_weighted_points_pct": away_form["weighted_points_pct"],
        "away_weighted_xg_diff": away_form["weighted_xg_diff"],
        "form_points_pct_diff": home_form["weighted_points_pct"] - away_form["weighted_points_pct"],
        "form_xg_diff_delta": home_form["weighted_xg_diff"] - away_form["weighted_xg_diff"],
    }
