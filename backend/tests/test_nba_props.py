"""Líneas de jugadores de la NBA: lectura de fuentes, proyección y probabilidades."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from jobs.nba_props import parse_props_market, props_market
from ml.nba.player_sources import match_player, name_key, parse_apisports_props, parse_espn_props, parse_player_log
from ml.nba.props import PropParams, add_projections, fit_alpha, latest_states, prob, prob_over


def _espn(tname, athlete, line, over, under, open_line=None):
    """Par de ESPN tal como llega: dos registros, el primero es "más de"."""
    def item(price):
        return {"type": {"name": tname}, "athlete": {"$ref": f"http://x/athletes/{athlete}?lang=en"},
                "odds": {"decimal": {"value": str(price), "open": str(price)},
                         "total": {"value": str(line), "open": str(open_line or line)}}}
    return [item(over), item(under)]


def test_espn_primero_es_mas_de():
    items = _espn("Total Points", 869, 7.5, 1.86, 1.95, open_line=6.5)
    items.append({"type": {"name": "Points Milestones"}, "athlete": {"$ref": "http://x/athletes/869"},
                  "odds": {"decimal": {"value": "1.86", "open": "1.88"}},
                  "current": {"target": {"value": 8.0}}, "open": {"target": {"value": 7.0}}})
    rows = parse_espn_props(items, "CLOSE")
    close = {(r["stat"], r["line"], r["side"], r["odds"]) for r in rows if r["snapshot"] == "CLOSE"}
    assert ("PTS", 7.5, "OVER", 1.86) in close and ("PTS", 7.5, "UNDER", 1.95) in close
    assert ("PTS", 7.5, "OVER", 1.86) in close                     # hito 8+ = más de 7.5
    opened = {(r["line"], r["side"]) for r in rows if r["snapshot"] == "OPEN"}
    assert (6.5, "OVER") in opened and (6.5, "UNDER") in opened
    assert all(r["athlete_id"] == "869" for r in rows)


def test_espn_par_incompleto_se_descarta():
    items = _espn("Total Rebounds", 5, 6.5, 1.9, 1.9)[:1]
    assert parse_espn_props(items, "LIVE") == []


def test_api_sports_formatos_de_jugador():
    resp = [{"bookmakers": [{"name": "Betano", "bets": [
        {"name": "Player Points", "values": [{"value": "LeBron James - Over 24.5", "odd": "1.85"},
                                             {"value": "Under 24.5 (LeBron James)", "odd": "1.95"},
                                             {"value": "Anthony Davis Over 26.5", "odd": "1.90"},
                                             {"value": "algo raro", "odd": "2.00"}]},
        {"name": "Player Points Milestones", "values": [{"value": "Chris Goulding - 10", "odd": "1.40"}]},
        {"name": "Home/Away", "values": [{"value": "Home", "odd": "1.5"}]},
    ]}]}]
    rows, unparsed = parse_apisports_props(resp)
    got = {(r["player_name"], r["stat"], r["line"], r["side"], r["odds"]) for r in rows}
    assert ("LeBron James", "PTS", 24.5, "OVER", 1.85) in got
    assert ("LeBron James", "PTS", 24.5, "UNDER", 1.95) in got
    assert ("Anthony Davis", "PTS", 26.5, "OVER", 1.90) in got
    assert ("Chris Goulding", "PTS", 9.5, "OVER", 1.40) in got        # 10+ = más de 9.5
    assert unparsed == 1                                                 # lo que no se entiende no se adivina


def test_nombres():
    assert name_key("Luka Dončić") == "luka doncic"
    assert name_key("Jaren Jackson Jr.") == "jaren jackson"
    players = {1: "Luka Dončić", 2: "Jaren Jackson Jr.", 3: "Nicolas Claxton", 4: "Jalen Williams", 5: "Jaylin Williams"}
    assert match_player("Luka Doncic", players) == 1
    assert match_player("Jaren Jackson", players) == 2
    assert match_player("Nic Claxton", players) == 3                   # mismo apellido, único
    assert match_player("J. Williams", players) is None                # ambiguo: no se adivina


def test_mercado_de_jugador_ida_y_vuelta():
    m = props_market(1629029, "PRA", 45.5)
    assert m == "P1629029_PRA_45.5" and len(m) <= 30
    assert parse_props_market(m) == (1629029, "PRA", 45.5)
    assert parse_props_market("TOTAL_220.5") is None


def test_registro_de_jugadores():
    rows = parse_player_log([{"GAME_ID": "0022400001", "PLAYER_ID": 7, "PLAYER_NAME": "X", "TEAM_ID": 1, "MIN": 34,
                              "PTS": 30, "REB": 8, "AST": 5, "FG3M": 4}])
    assert rows[0]["minutes"] == 34.0 and rows[0]["pts"] == 30


def test_probabilidades_binomial_negativa():
    assert prob_over(25.0, 24.5, 0.0) == pytest.approx(0.55, abs=0.03)   # Poisson: un poco más de la mitad
    assert prob(25.0, 24.5, "UNDER", 0.05) == pytest.approx(1 - prob_over(25.0, 24.5, 0.05))
    # Más dispersión: más probabilidad en las colas.
    assert prob_over(10.0, 20.5, 0.2) > prob_over(10.0, 20.5, 0.0)
    y = np.random.default_rng(0).negative_binomial(5, 5 / (5 + 20), 20000)
    assert fit_alpha(y.astype(float), np.full(len(y), 20.0)) == pytest.approx(0.2, abs=0.03)


def _frame(n_games=30):
    rows = []
    for d in range(n_games):
        day = date(2024, 11, 1) + timedelta(days=2 * d)
        gid = f"{d:010d}"
        rows.append({"game_id": gid, "player_id": 1, "team_id": 10, "minutes": 30.0 + (d >= 20) * 6, "pts": 20 + (d >= 20) * 10,
                     "reb": 5, "ast": 4, "fg3m": 2, "game_date": day, "season": "2024-25", "season_type": "REGULAR",
                     "home_team_id": 10, "away_team_id": 20, "is_home": True})
    df = pd.DataFrame(rows)
    ctx = pd.DataFrame({"poss": 100.0, "league_pace": 100.0, "home_pace": 0.0, "away_pace": 0.0, "home_dfn": 0.0,
                        "away_dfn": 0.0, "league_ortg": 115.0}, index=df["game_id"])
    return df, ctx


def test_proyeccion_sin_ver_el_partido_que_predice():
    df, ctx = _frame()
    f = add_projections(df, ctx, PropParams(min_games=5))
    assert f.loc[:4, "mu_pts"].isna().all()                        # pocos partidos previos: sin proyección
    assert f.loc[10, "mu_pts"] == pytest.approx(20.0)              # 10 partidos de 20 puntos
    # El día 20 sube a 30 puntos: la proyección de ESE partido todavía no lo sabe.
    assert f.loc[20, "mu_pts"] == pytest.approx(20.0)
    assert f.loc[25, "mu_pts"] > 22                                  # después, lo va notando
    st = latest_states(df, PropParams())
    assert st.loc[1, "n_games"] == 30 and bool(st.loc[1, "same_team_3"])
