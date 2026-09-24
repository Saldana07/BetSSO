"""Modelo de la NBA: ratings de ataque, defensa y ritmo que se actualizan
partido a partido.

Por qué así: en la NBA el marcador depende de dos cosas casi independientes,
cuántas posesiones hay (ritmo) y cuántos puntos saca cada equipo por posesión
(eficiencia). Cada equipo tiene tres números, en puntos por 100 posesiones y
posesiones por partido, relativos a la media de la liga:

    posesiones  = P + ritmo_local + ritmo_visitante
    pts_local   = posesiones · (L + ataque_local + defensa_visitante) / 100 + local/2 - cansancio
    pts_visit   = posesiones · (L + ataque_visitante + defensa_local) / 100 - local/2 + cansancio

Tras cada partido, cada número se corrige una fracción `k` de su error (como
un Elo, pero de puntos): ganar a un rival fuerte mueve más que a uno débil sin
tener que ajustarlo aparte. Al empezar temporada los ratings se acercan a la
media (`carry`), porque las plantillas cambian. "Cansancio": jugar el día
después de otro partido (back-to-back).

La predicción de un partido usa SOLO partidos anteriores: el recorrido sobre
el histórico es una prueba fuera de muestra, partido a partido.

Salida: margen esperado (local - visitante) y total esperado; con la
desviación típica de cada uno (ajustada en el entrenamiento) dan las
probabilidades de ganador, hándicap y total (ml/nba/markets.py).
"""
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

LEAGUE_ORTG = 110.0   # arranque; se ajusta solo con los partidos
LEAGUE_PACE = 97.0
MAX_REST = 4


@dataclass
class Params:
    k: float = 0.035            # corrección de ataque/defensa por partido
    k_new: float = 0.04         # extra al empezar temporada (se diluye en ~15 partidos)
    k_pace: float = 0.05        # corrección del ritmo
    carry: float = 0.65         # cuánto del rating pasa a la temporada siguiente
    home: float = 2.5           # ventaja de local, en puntos
    b2b: float = 1.5            # castigo por back-to-back, en puntos
    k_league: float = 0.004     # deriva de la media de la liga (cada vez se anota más)
    k_home: float = 0.002       # deriva de la ventaja de local (en la burbuja de 2020 fue ~0)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TeamState:
    off: float = 0.0
    dfn: float = 0.0
    pace: float = 0.0
    games: int = 0            # partidos en la temporada actual
    season: str | None = None
    last_date: pd.Timestamp | None = None


@dataclass
class Ratings:
    params: Params
    league_ortg: float = LEAGUE_ORTG
    league_pace: float = LEAGUE_PACE
    home_adv: float | None = None      # arranca en params.home y deriva con los datos
    teams: dict[int, TeamState] = field(default_factory=dict)

    def __post_init__(self):
        if self.home_adv is None:
            self.home_adv = self.params.home

    def team(self, team_id: int, season: str) -> TeamState:
        t = self.teams.setdefault(team_id, TeamState())
        if t.season != season:
            if t.season is not None:   # nueva temporada: regresión a la media
                t.off *= self.params.carry
                t.dfn *= self.params.carry
                t.pace *= self.params.carry
            t.season, t.games = season, 0
        return t

    def rest_days(self, t: TeamState, day: pd.Timestamp) -> int:
        if t.last_date is None:
            return MAX_REST
        return int(min(MAX_REST, max(0, (day - t.last_date).days)))

    def predict(self, home_id: int, away_id: int, season: str, day: pd.Timestamp,
                neutral: bool = False) -> dict:
        p = self.params
        h, a = self.team(home_id, season), self.team(away_id, season)
        poss = self.league_pace + h.pace + a.pace
        home_adv = 0.0 if neutral else self.home_adv
        fatigue = p.b2b * ((self.rest_days(h, day) == 1) - (self.rest_days(a, day) == 1))
        pts_h = poss * (self.league_ortg + h.off + a.dfn) / 100 + home_adv / 2 - fatigue / 2
        pts_a = poss * (self.league_ortg + a.off + h.dfn) / 100 - home_adv / 2 + fatigue / 2
        return {"margin": pts_h - pts_a, "total": pts_h + pts_a, "poss": poss,
                "home_games": h.games, "away_games": a.games}

    def update(self, home_id: int, away_id: int, season: str, day: pd.Timestamp,
               home_pts: float, away_pts: float, poss: float | None, neutral: bool = False) -> None:
        p = self.params
        h, a = self.team(home_id, season), self.team(away_id, season)
        if poss and poss > 60:
            if not neutral:
                pred = self.predict(home_id, away_id, season, day)
                self.home_adv += p.k_home * ((home_pts - away_pts) - pred["margin"])
            home_adv100 = 0.0 if neutral else self.home_adv / poss * 100
            err_h = home_pts / poss * 100 - (self.league_ortg + h.off + a.dfn + home_adv100 / 2)
            err_a = away_pts / poss * 100 - (self.league_ortg + a.off + h.dfn - home_adv100 / 2)
            k_h = p.k + p.k_new * max(0.0, 1 - h.games / 15)
            k_a = p.k + p.k_new * max(0.0, 1 - a.games / 15)
            h.off += k_h * err_h
            a.dfn += k_a * err_h
            a.off += k_a * err_a
            h.dfn += k_h * err_a
            self.league_ortg += p.k_league * (err_h + err_a) / 2
            err_p = poss - (self.league_pace + h.pace + a.pace)
            h.pace += p.k_pace * err_p / 2
            a.pace += p.k_pace * err_p / 2
            self.league_pace += p.k_league * err_p
        h.games += 1
        a.games += 1
        h.last_date = a.last_date = day


def possessions(fga, oreb, tov, fta) -> float | None:
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in (fga, oreb, tov, fta)):
        return None
    return fga - oreb + tov + 0.44 * fta


def game_possessions(row) -> float | None:
    ph = possessions(row.home_fga, row.home_oreb, row.home_tov, row.home_fta)
    pa = possessions(row.away_fga, row.away_oreb, row.away_tov, row.away_fta)
    if ph is None or pa is None:
        return None
    return (ph + pa) / 2


def replay(games: pd.DataFrame, params: Params) -> tuple[pd.DataFrame, Ratings]:
    """Recorre los partidos en orden: predicción ANTES de cada uno, luego
    actualización. `games`: columnas id, season, game_date, home_team_id,
    away_team_id, home_pts, away_pts y la caja (fga, oreb, tov, fta); los
    partidos sin marcador solo reciben predicción (los de hoy y mañana)."""
    r = Ratings(params)
    out = []
    games = games.sort_values(["game_date", "id"])
    # Todos los partidos de un día se predicen antes de actualizar con
    # cualquiera de ellos: ningún resultado del día se filtra a otro partido.
    for game_date, day_games in games.groupby("game_date", sort=True):
        day = pd.Timestamp(game_date)
        rows = list(day_games.itertuples(index=False))
        for row in rows:
            neutral = bool(getattr(row, "neutral", False) or False)
            out.append({"id": row.id, **r.predict(row.home_team_id, row.away_team_id, row.season, day, neutral)})
        for row in rows:
            if row.home_pts is not None and not pd.isna(row.home_pts):
                r.update(row.home_team_id, row.away_team_id, row.season, day, float(row.home_pts),
                         float(row.away_pts), game_possessions(row), bool(getattr(row, "neutral", False) or False))
            else:
                # Aún sin jugar (hoy): cuenta para el cansancio de mañana (back-to-back).
                for team_id in (row.home_team_id, row.away_team_id):
                    r.team(team_id, row.season).last_date = day
    return pd.DataFrame(out), r
