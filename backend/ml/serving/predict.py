"""Cómputo de features "en vivo" (estado actual de los equipos) para generar
predicciones sobre un partido próximo, y su combinación con los modelos
entrenados para producir probabilidades por mercado.

Reutiliza la MISMA lógica de reconstrucción de Elo/forma/Poisson que
ml/training/build_dataset.py (para que el modelo vea, en producción,
features calculadas exactamente igual que en entrenamiento), pero aplicada
sobre el histórico real hasta HOY en vez de sobre todo el dataset de golpe.

*** LIMITACIÓN CONOCIDA ***
Los equipos sincronizados desde API-Football (partidos del día) son filas
nuevas en `teams`, sin relación con los equipos del histórico de
FiveThirtyEight usado para entrenar (identidades de datos distintas, sin
reconciliación de nombres todavía). Mientras no se acumule suficiente
historial real vía `ml/ingestion/sync_fixtures.py` para esos equipos
específicos, sus features de Elo/forma partirán de valores por defecto
(neutros) — el modelo funcionará, pero sin señal predictiva real hasta que
haya historial. Esto es esperado, no un bug.
"""
from collections import deque
from dataclasses import dataclass
from functools import lru_cache

import joblib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match
from ml.features.corners import CornerModel, corner_market_probabilities
from ml.features.elo import elo_win_draw_loss_probs, update_elo
from ml.features.form import MatchResult, build_form_features
from ml.features.poisson_xg import expected_goals, extended_market_probabilities, market_probabilities, team_strengths
from ml.training.build_dataset import (
    FALLBACK_LEAGUE_AVG_GOALS,
    LEAGUE_PRIOR_WEIGHT,
    ROLLING_GOALS_WINDOW,
    FORM_HISTORY_WINDOW,
    REST_DAYS_DEFAULT,
    REST_DAYS_CAP,
)
from ml.training.train_classifier import FEATURE_COLUMNS


@lru_cache(maxsize=1)
def get_models(model_dir: str = "models", model_version: str = "v1") -> "TrainedModels":
    """Singleton para la API: cargar los joblib y el CSV de córners (45 MB)
    en cada request sería inaceptable."""
    return TrainedModels(model_dir, model_version)


@dataclass
class MatchContext:
    """Lo que necesita la simulación de Montecarlo, calculado con el estado
    real de los equipos hasta hoy (misma lógica que las predicciones)."""
    lambda_home: float
    lambda_away: float
    home_history: int
    away_history: int
    classifier: dict[str, float]  # {"HOME": p, "DRAW": p, "AWAY": p}


def match_context(db: Session, match: Match, models: "TrainedModels") -> MatchContext:
    features = compute_pre_match_features(db, match)
    X = [[features[col] for col in FEATURE_COLUMNS]]

    proba = models.classifier.predict_proba(X)[0]
    idx = {label: i for i, label in enumerate(models.encoder.classes_)}

    return MatchContext(
        lambda_home=max(float(models.goals_home_model.predict(X)[0]), 0.05),
        lambda_away=max(float(models.goals_away_model.predict(X)[0]), 0.05),
        home_history=features["_home_history"],
        away_history=features["_away_history"],
        classifier={"HOME": float(proba[idx["H"]]), "DRAW": float(proba[idx["D"]]), "AWAY": float(proba[idx["A"]])},
    )


class TrainedModels:
    def __init__(self, model_dir: str, model_version: str):
        self.classifier = joblib.load(f"{model_dir}/classifier_{model_version}.joblib")
        self.encoder = joblib.load(f"{model_dir}/label_encoder_{model_version}.joblib")
        self.goals_home_model = joblib.load(f"{model_dir}/goals_home_{model_version}.joblib")
        self.goals_away_model = joblib.load(f"{model_dir}/goals_away_{model_version}.joblib")
        # El modelo de córners no se entrena: son tasas rodantes + Poisson
        # sobre el CSV de football-data.co.uk (ver ml/features/corners.py).
        self.corners = CornerModel()


def _replay_league_state(past_matches: list[Match]) -> dict:
    """Reconstruye Elo, forma y promedios de goles a partir del histórico
    FINISHED de una liga, en el mismo orden y con la misma lógica que
    build_dataset.py. Devuelve el estado final (a "hoy") por equipo."""
    elo: dict[int, float] = {}
    form_history: dict[int, deque] = {}
    goals_scored: dict[int, deque] = {}
    goals_conceded: dict[int, deque] = {}
    last_match_date: dict[int, object] = {}
    matches_played: dict[int, int] = {}
    home_goals_sum = away_goals_sum = 0.0
    n = 0

    for m in past_matches:
        home_id, away_id = m.home_team_id, m.away_team_id
        home_elo = elo.setdefault(home_id, 1500.0)
        away_elo = elo.setdefault(away_id, 1500.0)

        elo_result = update_elo(home_elo, away_elo, m.home_goals, m.away_goals)
        elo[home_id] = elo_result.home_elo_after
        elo[away_id] = elo_result.away_elo_after

        home_points = "W" if m.home_goals > m.away_goals else ("D" if m.home_goals == m.away_goals else "L")
        away_points = "W" if m.away_goals > m.home_goals else ("D" if m.away_goals == m.home_goals else "L")
        home_xg = float(m.home_xg) if m.home_xg is not None else float(m.home_goals)
        away_xg = float(m.away_xg) if m.away_xg is not None else float(m.away_goals)

        form_history.setdefault(home_id, deque(maxlen=FORM_HISTORY_WINDOW)).append(
            MatchResult(home_points, m.home_goals, m.away_goals, home_xg, away_xg)
        )
        form_history.setdefault(away_id, deque(maxlen=FORM_HISTORY_WINDOW)).append(
            MatchResult(away_points, m.away_goals, m.home_goals, away_xg, home_xg)
        )

        goals_scored.setdefault(home_id, deque(maxlen=ROLLING_GOALS_WINDOW)).append(m.home_goals)
        goals_conceded.setdefault(home_id, deque(maxlen=ROLLING_GOALS_WINDOW)).append(m.away_goals)
        goals_scored.setdefault(away_id, deque(maxlen=ROLLING_GOALS_WINDOW)).append(m.away_goals)
        goals_conceded.setdefault(away_id, deque(maxlen=ROLLING_GOALS_WINDOW)).append(m.home_goals)

        home_goals_sum += m.home_goals
        away_goals_sum += m.away_goals
        n += 1
        last_match_date[home_id] = m.kickoff_utc
        last_match_date[away_id] = m.kickoff_utc
        matches_played[home_id] = matches_played.get(home_id, 0) + 1
        matches_played[away_id] = matches_played.get(away_id, 0) + 1

    league_avg_home = (LEAGUE_PRIOR_WEIGHT * FALLBACK_LEAGUE_AVG_GOALS + home_goals_sum) / (LEAGUE_PRIOR_WEIGHT + n)
    league_avg_away = (LEAGUE_PRIOR_WEIGHT * FALLBACK_LEAGUE_AVG_GOALS + away_goals_sum) / (LEAGUE_PRIOR_WEIGHT + n)

    return {
        "elo": elo,
        "form_history": form_history,
        "goals_scored": goals_scored,
        "goals_conceded": goals_conceded,
        "last_match_date": last_match_date,
        "matches_played": matches_played,
        "league_avg_home": league_avg_home,
        "league_avg_away": league_avg_away,
    }


def compute_pre_match_features(db: Session, match: Match) -> dict:
    past_matches = db.execute(
        select(Match).where(
            Match.league_id == match.league_id,
            Match.status == "FINISHED",
            Match.home_goals.isnot(None),
            Match.away_goals.isnot(None),
            Match.kickoff_utc < match.kickoff_utc,
        ).order_by(Match.kickoff_utc)
    ).scalars().all()

    state = _replay_league_state(past_matches)
    home_id, away_id = match.home_team_id, match.away_team_id

    home_elo = state["elo"].get(home_id, 1500.0)
    away_elo = state["elo"].get(away_id, 1500.0)
    elo_diff = home_elo - away_elo
    home_win_prob_elo, _, _ = elo_win_draw_loss_probs(home_elo, away_elo)

    league_avg_home = state["league_avg_home"]
    league_avg_away = state["league_avg_away"]
    league_avg_overall = (league_avg_home + league_avg_away) / 2

    def _avg(d: dict, team_id: int, fallback: float) -> float:
        values = d.get(team_id)
        return (sum(values) / len(values)) if values else fallback

    home_scored_avg = _avg(state["goals_scored"], home_id, league_avg_overall)
    home_conceded_avg = _avg(state["goals_conceded"], home_id, league_avg_overall)
    away_scored_avg = _avg(state["goals_scored"], away_id, league_avg_overall)
    away_conceded_avg = _avg(state["goals_conceded"], away_id, league_avg_overall)

    home_attack, home_defense = team_strengths(home_scored_avg, home_conceded_avg, league_avg_overall)
    away_attack, away_defense = team_strengths(away_scored_avg, away_conceded_avg, league_avg_overall)

    lambda_home, lambda_away = expected_goals(
        home_attack, home_defense, away_attack, away_defense, league_avg_home, league_avg_away,
    )
    poisson_probs = market_probabilities(lambda_home, lambda_away)

    form_feats = build_form_features(
        list(state["form_history"].get(home_id, [])),
        list(state["form_history"].get(away_id, [])),
    )

    last_match_date = state["last_match_date"]
    home_rest_days = (
        min((match.kickoff_utc - last_match_date[home_id]).total_seconds() / 86400, REST_DAYS_CAP)
        if home_id in last_match_date else REST_DAYS_DEFAULT
    )
    away_rest_days = (
        min((match.kickoff_utc - last_match_date[away_id]).total_seconds() / 86400, REST_DAYS_CAP)
        if away_id in last_match_date else REST_DAYS_DEFAULT
    )

    return {
        "elo_diff": elo_diff,
        "home_win_prob_elo": home_win_prob_elo,
        "poisson_home_win_prob": poisson_probs["1X2_HOME"],
        "poisson_draw_prob": poisson_probs["1X2_DRAW"],
        "poisson_away_win_prob": poisson_probs["1X2_AWAY"],
        "home_weighted_points_pct": form_feats["home_weighted_points_pct"],
        "away_weighted_points_pct": form_feats["away_weighted_points_pct"],
        "form_points_pct_diff": form_feats["form_points_pct_diff"],
        "form_xg_diff_delta": form_feats["form_xg_diff_delta"],
        "home_injuries_impact": 0,
        "away_injuries_impact": 0,
        "home_rest_days": home_rest_days,
        "away_rest_days": away_rest_days,
        "rest_days_diff": home_rest_days - away_rest_days,
        # Diagnósticos (no son features del modelo): miden cuánta información
        # real respalda esta predicción. Con historial 0, el modelo devuelve
        # la tasa base de la población y NO debe usarse para apostar.
        "_n_historical_matches": len(past_matches),
        "_home_history": state["matches_played"].get(home_id, 0),
        "_away_history": state["matches_played"].get(away_id, 0),
        "_lambda_home": lambda_home,
        "_lambda_away": lambda_away,
    }


def predict_match_probabilities(db: Session, match: Match, models: TrainedModels) -> dict[str, float]:
    """Devuelve probabilidades por mercado, con las claves que espera
    jobs/generate_daily_pick.py (ej. '1X2_HOME', 'OU_2.5_OVER', 'BTTS_YES')."""
    features = compute_pre_match_features(db, match)
    X = [[features[col] for col in FEATURE_COLUMNS]]

    proba_1x2 = models.classifier.predict_proba(X)[0]
    class_index = {label: i for i, label in enumerate(models.encoder.classes_)}
    p_home = proba_1x2[class_index["H"]]
    p_draw = proba_1x2[class_index["D"]]
    p_away = proba_1x2[class_index["A"]]

    goals_home_pred = max(float(models.goals_home_model.predict(X)[0]), 0.05)
    goals_away_pred = max(float(models.goals_away_model.predict(X)[0]), 0.05)
    # Todos los mercados de goles (líneas 0.5-4.5, totales por equipo, hándicap
    # asiático, marcador exacto) salen de la misma matriz de marcadores.
    goals_market_probs = extended_market_probabilities(goals_home_pred, goals_away_pred)

    # Córners: solo si AMBOS equipos tienen historial suficiente en la fuente
    # de córners. Si no, simplemente no hay claves CORNERS_* y el job no
    # emite picks de córners para este partido (sin datos no se apuesta).
    corner_probs: dict[str, float] = {}
    expected_corners = models.corners.expected_corners(
        match.home_team.name, match.away_team.name, match.kickoff_utc
    )
    if expected_corners is not None:
        corner_probs = corner_market_probabilities(*expected_corners)

    return {
        **goals_market_probs,
        **corner_probs,
        # El 1X2 y la doble oportunidad los da el clasificador calibrado, que
        # es mejor que la Poisson pura para resultado (la Poisson es solo una
        # de sus features); sobreescriben las claves derivadas de la matriz.
        "1X2_HOME": float(p_home),
        "1X2_DRAW": float(p_draw),
        "1X2_AWAY": float(p_away),
        "DOUBLE_CHANCE_1X": float(p_home + p_draw),
        "DOUBLE_CHANCE_X2": float(p_draw + p_away),
        "DOUBLE_CHANCE_12": float(p_home + p_away),
        "_diagnostics": {
            "n_historical_matches": features["_n_historical_matches"],
            "home_history": features["_home_history"],
            "away_history": features["_away_history"],
            "home_elo_diff": features["elo_diff"],
            "expected_corners": sum(expected_corners) if expected_corners else None,
        },
    }
