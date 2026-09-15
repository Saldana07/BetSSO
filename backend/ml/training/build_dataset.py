"""Recorre los partidos históricos (FINISHED, con marcador) en orden
cronológico por liga y materializa el parquet de features que consumen
train_classifier.py / train_goals_regressor.py.

Crítico contra el overfitting/leakage: todas las features de un partido se
calculan con el ESTADO DE LOS EQUIPOS ANTES de jugarse ese partido (Elo,
forma, promedios de goles); el resultado del partido actualiza el estado
DESPUÉS de haber registrado la fila. Nunca se usa información del futuro.

Simplificaciones explícitas (dataset histórico de FiveThirtyEight, sin datos
de lesiones): `home_injuries_impact` / `away_injuries_impact` quedan en 0
para todas las filas.

Uso:
    python -m ml.training.build_dataset --output data/processed/training_features.parquet
"""
import argparse
from collections import defaultdict, deque

import pandas as pd
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Match
from ml.features.elo import elo_win_draw_loss_probs, update_elo
from ml.features.form import MatchResult, build_form_features
from ml.features.poisson_xg import expected_goals, market_probabilities, team_strengths

DEFAULT_ELO = 1500.0
ROLLING_GOALS_WINDOW = 38   # ~ una temporada, evita mezclar eras muy distintas del equipo
FORM_HISTORY_WINDOW = 10    # build_form_features solo usa los últimos 5, guardamos margen
FALLBACK_LEAGUE_AVG_GOALS = 1.35  # promedio típico de goles por equipo por partido, antes de tener datos propios
LEAGUE_PRIOR_WEIGHT = 5  # "partidos virtuales" de prior; evita que ligas con pocos partidos (o un 0-0 inicial) den promedio 0 exacto
REST_DAYS_DEFAULT = 7.0  # asumido para el primer partido de un equipo en el histórico (sin dato previo)
REST_DAYS_CAP = 30.0     # evita que descansos largos (pretemporada, parón de selecciones) dominen el árbol


class LeagueGoalStats:
    """Promedio de goles local/visitante de la liga, actualizado incrementalmente
    (solo con partidos ya jugados hasta el momento) para no filtrar el futuro).

    Se suaviza con un prior bayesiano simple: sin esto, una liga cuyo primer
    partido registrado terminara 0-0 dejaría el promedio en exactamente 0.0,
    y la siguiente división por ese promedio (en team_strengths) explota.
    """

    def __init__(self):
        self.home_goals_sum = 0.0
        self.away_goals_sum = 0.0
        self.n = 0

    def averages(self) -> tuple[float, float]:
        home_avg = (LEAGUE_PRIOR_WEIGHT * FALLBACK_LEAGUE_AVG_GOALS + self.home_goals_sum) / (LEAGUE_PRIOR_WEIGHT + self.n)
        away_avg = (LEAGUE_PRIOR_WEIGHT * FALLBACK_LEAGUE_AVG_GOALS + self.away_goals_sum) / (LEAGUE_PRIOR_WEIGHT + self.n)
        return home_avg, away_avg

    def update(self, home_goals: int, away_goals: int):
        self.home_goals_sum += home_goals
        self.away_goals_sum += away_goals
        self.n += 1


def _result_label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "H"
    if home_goals < away_goals:
        return "A"
    return "D"


def build_dataset() -> pd.DataFrame:
    db = SessionLocal()
    try:
        matches = db.execute(
            select(Match)
            .where(Match.status == "FINISHED", Match.home_goals.isnot(None), Match.away_goals.isnot(None))
            .order_by(Match.league_id, Match.kickoff_utc)
        ).scalars().all()
    finally:
        db.close()

    elo: dict[int, float] = defaultdict(lambda: DEFAULT_ELO)
    form_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=FORM_HISTORY_WINDOW))
    goals_scored: dict[int, deque] = defaultdict(lambda: deque(maxlen=ROLLING_GOALS_WINDOW))
    goals_conceded: dict[int, deque] = defaultdict(lambda: deque(maxlen=ROLLING_GOALS_WINDOW))
    league_stats: dict[int, LeagueGoalStats] = defaultdict(LeagueGoalStats)
    last_match_date: dict[int, object] = {}

    rows = []

    for match in matches:
        home_id, away_id, league_id = match.home_team_id, match.away_team_id, match.league_id
        home_goals, away_goals = match.home_goals, match.away_goals

        # --- 1. calcular features con estado PRE-partido ---
        home_elo, away_elo = elo[home_id], elo[away_id]
        elo_diff = home_elo - away_elo
        home_win_prob_elo, _, _ = elo_win_draw_loss_probs(home_elo, away_elo)

        league_avg_home, league_avg_away = league_stats[league_id].averages()
        league_avg_overall = (league_avg_home + league_avg_away) / 2

        home_scored_avg = sum(goals_scored[home_id]) / len(goals_scored[home_id]) if goals_scored[home_id] else league_avg_overall
        home_conceded_avg = sum(goals_conceded[home_id]) / len(goals_conceded[home_id]) if goals_conceded[home_id] else league_avg_overall
        away_scored_avg = sum(goals_scored[away_id]) / len(goals_scored[away_id]) if goals_scored[away_id] else league_avg_overall
        away_conceded_avg = sum(goals_conceded[away_id]) / len(goals_conceded[away_id]) if goals_conceded[away_id] else league_avg_overall

        home_attack, home_defense = team_strengths(home_scored_avg, home_conceded_avg, league_avg_overall)
        away_attack, away_defense = team_strengths(away_scored_avg, away_conceded_avg, league_avg_overall)

        lambda_home, lambda_away = expected_goals(
            home_attack, home_defense, away_attack, away_defense,
            league_avg_home, league_avg_away,
        )
        poisson_probs = market_probabilities(lambda_home, lambda_away)

        form_feats = build_form_features(list(form_history[home_id]), list(form_history[away_id]))

        home_rest_days = (
            min((match.kickoff_utc - last_match_date[home_id]).total_seconds() / 86400, REST_DAYS_CAP)
            if home_id in last_match_date else REST_DAYS_DEFAULT
        )
        away_rest_days = (
            min((match.kickoff_utc - last_match_date[away_id]).total_seconds() / 86400, REST_DAYS_CAP)
            if away_id in last_match_date else REST_DAYS_DEFAULT
        )

        rows.append({
            "match_date": match.kickoff_utc,
            "league_id": league_id,
            "season": match.season,
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
            "result": _result_label(home_goals, away_goals),
            "home_goals": home_goals,
            "away_goals": away_goals,
            # Probabilidad de un modelo externo (ej. FiveThirtyEight SPI) para el
            # MISMO partido, cuando la fuente la trae. Nunca se usa como feature
            # de entrenamiento (sería fuga de un modelo ajeno); solo sirve para
            # simular una "cuota de mercado" en el backtest histórico cuando no
            # hay cuotas reales disponibles (ver ml/backtesting/run_historical_backtest.py).
            "ext_prob_home": float(match.ext_prob_home) if match.ext_prob_home is not None else None,
            "ext_prob_draw": float(match.ext_prob_draw) if match.ext_prob_draw is not None else None,
            "ext_prob_away": float(match.ext_prob_away) if match.ext_prob_away is not None else None,
        })

        # --- 2. actualizar estado POST-partido (nunca antes del paso 1) ---
        elo_result = update_elo(home_elo, away_elo, home_goals, away_goals)
        elo[home_id] = elo_result.home_elo_after
        elo[away_id] = elo_result.away_elo_after

        home_points = "W" if home_goals > away_goals else ("D" if home_goals == away_goals else "L")
        away_points = "W" if away_goals > home_goals else ("D" if away_goals == home_goals else "L")
        home_xg = float(match.home_xg) if match.home_xg is not None else float(home_goals)
        away_xg = float(match.away_xg) if match.away_xg is not None else float(away_goals)
        form_history[home_id].append(MatchResult(home_points, home_goals, away_goals, home_xg, away_xg))
        form_history[away_id].append(MatchResult(away_points, away_goals, home_goals, away_xg, home_xg))

        goals_scored[home_id].append(home_goals)
        goals_conceded[home_id].append(away_goals)
        goals_scored[away_id].append(away_goals)
        goals_conceded[away_id].append(home_goals)

        league_stats[league_id].update(home_goals, away_goals)
        last_match_date[home_id] = match.kickoff_utc
        last_match_date[away_id] = match.kickoff_utc

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    df = build_dataset()
    df = df.sort_values("match_date").reset_index(drop=True)

    import os
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    df.to_parquet(args.output, index=False)

    print(f"Dataset generado: {len(df)} filas -> {args.output}")
    print(df["result"].value_counts(normalize=True))


if __name__ == "__main__":
    main()
