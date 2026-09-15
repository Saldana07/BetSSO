"""Reconstrucción cronológica del estado de los equipos (Elo, forma, tasas de
goles, descanso) y cálculo de features PRE-partido.

Existe para que TODAS las fuentes (partidos de la BD, CSV externo) produzcan
exactamente el mismo vector de features que vio el modelo al entrenar. Si la
lógica viviera duplicada en cada script, una divergencia mínima (una ventana,
un prior) invalidaría en silencio cualquier backtest o predicción.

Regla invariable: `features()` solo usa el estado ANTERIOR al partido;
`update()` lo actualiza DESPUÉS. Nunca al revés.
"""
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Hashable

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


@dataclass
class MatchRecord:
    league_id: Hashable
    home_id: Hashable
    away_id: Hashable
    kickoff: datetime
    home_goals: int
    away_goals: int
    home_xg: float | None = None
    away_xg: float | None = None


class LeagueGoalStats:
    """Promedio de goles local/visitante de la liga, actualizado incrementalmente
    (solo con partidos ya jugados hasta el momento) para no filtrar el futuro.

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


class FeatureReplayer:
    def __init__(self):
        self.elo: dict = defaultdict(lambda: DEFAULT_ELO)
        self.form_history: dict = defaultdict(lambda: deque(maxlen=FORM_HISTORY_WINDOW))
        self.goals_scored: dict = defaultdict(lambda: deque(maxlen=ROLLING_GOALS_WINDOW))
        self.goals_conceded: dict = defaultdict(lambda: deque(maxlen=ROLLING_GOALS_WINDOW))
        self.league_stats: dict = defaultdict(LeagueGoalStats)
        self.last_match_date: dict = {}
        self.matches_played: dict = defaultdict(int)

    def _avg(self, d: dict, team_id, fallback: float) -> float:
        values = d[team_id]
        return (sum(values) / len(values)) if values else fallback

    def _rest_days(self, team_id, kickoff: datetime) -> float:
        if team_id not in self.last_match_date:
            return REST_DAYS_DEFAULT
        return min((kickoff - self.last_match_date[team_id]).total_seconds() / 86400, REST_DAYS_CAP)

    def features(self, m: MatchRecord) -> dict:
        """Features del partido con el estado PRE-partido. No muta nada."""
        home_elo, away_elo = self.elo[m.home_id], self.elo[m.away_id]
        home_win_prob_elo, _, _ = elo_win_draw_loss_probs(home_elo, away_elo)

        league_avg_home, league_avg_away = self.league_stats[m.league_id].averages()
        league_avg_overall = (league_avg_home + league_avg_away) / 2

        home_attack, home_defense = team_strengths(
            self._avg(self.goals_scored, m.home_id, league_avg_overall),
            self._avg(self.goals_conceded, m.home_id, league_avg_overall),
            league_avg_overall,
        )
        away_attack, away_defense = team_strengths(
            self._avg(self.goals_scored, m.away_id, league_avg_overall),
            self._avg(self.goals_conceded, m.away_id, league_avg_overall),
            league_avg_overall,
        )
        lambda_home, lambda_away = expected_goals(
            home_attack, home_defense, away_attack, away_defense, league_avg_home, league_avg_away,
        )
        poisson_probs = market_probabilities(lambda_home, lambda_away)
        form_feats = build_form_features(list(self.form_history[m.home_id]), list(self.form_history[m.away_id]))

        home_rest = self._rest_days(m.home_id, m.kickoff)
        away_rest = self._rest_days(m.away_id, m.kickoff)

        return {
            "elo_diff": home_elo - away_elo,
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
            "home_rest_days": home_rest,
            "away_rest_days": away_rest,
            "rest_days_diff": home_rest - away_rest,
            # diagnósticos (no son features del modelo)
            "_home_history": self.matches_played[m.home_id],
            "_away_history": self.matches_played[m.away_id],
            "_lambda_home": lambda_home,
            "_lambda_away": lambda_away,
        }

    def update(self, m: MatchRecord) -> None:
        """Incorpora el resultado del partido al estado. Llamar DESPUÉS de features()."""
        elo_result = update_elo(self.elo[m.home_id], self.elo[m.away_id], m.home_goals, m.away_goals)
        self.elo[m.home_id] = elo_result.home_elo_after
        self.elo[m.away_id] = elo_result.away_elo_after

        hg, ag = m.home_goals, m.away_goals
        home_points = "W" if hg > ag else ("D" if hg == ag else "L")
        away_points = "W" if ag > hg else ("D" if ag == hg else "L")
        home_xg = float(m.home_xg) if m.home_xg is not None else float(hg)
        away_xg = float(m.away_xg) if m.away_xg is not None else float(ag)
        self.form_history[m.home_id].append(MatchResult(home_points, hg, ag, home_xg, away_xg))
        self.form_history[m.away_id].append(MatchResult(away_points, ag, hg, away_xg, home_xg))

        self.goals_scored[m.home_id].append(hg)
        self.goals_conceded[m.home_id].append(ag)
        self.goals_scored[m.away_id].append(ag)
        self.goals_conceded[m.away_id].append(hg)

        self.league_stats[m.league_id].update(hg, ag)
        self.last_match_date[m.home_id] = m.kickoff
        self.last_match_date[m.away_id] = m.kickoff
        self.matches_played[m.home_id] += 1
        self.matches_played[m.away_id] += 1
