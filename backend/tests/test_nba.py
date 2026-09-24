"""Apartado NBA: lectura de fuentes, mercados, modelo y liquidación."""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from jobs import on_demand
from ml.nba import sources
from ml.nba.markets import blend, implied_mu, market_expectation, probability
from ml.nba.ratings import Params, replay
from ml.nba.store import nickname
from ml.value_betting.market import fair_probabilities
from ml.value_betting.settlement import UnknownMarketError, bet_won


def _row(team_id, abbr, name, gid, day, matchup, pts):
    return {"TEAM_ID": team_id, "TEAM_ABBREVIATION": abbr, "TEAM_NAME": name, "GAME_ID": gid, "GAME_DATE": day,
            "MATCHUP": matchup, "PTS": pts, "FGA": 88, "FTA": 20, "OREB": 10, "TOV": 13}


def test_registro_de_partidos_empareja_local_y_visitante():
    rows = [_row(1, "LAL", "Los Angeles Lakers", "0022400062", "2024-10-22", "LAL vs. MIN", 110),
            _row(2, "MIN", "Minnesota Timberwolves", "0022400062", "2024-10-22", "MIN @ LAL", 103),
            _row(3, "BOS", "Boston Celtics", "0042400101", "2025-04-20", "BOS vs. ORL", 103)]   # sin rival: fuera
    games = sources.parse_game_log(rows, "2024-25")
    assert len(games) == 1
    g = games[0]
    assert g["home_team"][1] == "LAL" and g["away_team"][1] == "MIN"
    assert (g["home_pts"], g["away_pts"]) == (110, 103)
    assert g["season_type"] == "REGULAR" and g["game_date"] == date(2024, 10, 22)


def test_tipo_de_partido_por_id():
    assert sources.GAME_ID_TYPES["004"] == "PLAYOFFS" and sources.GAME_ID_TYPES["001"] == "PRESEASON"


def test_temporada_de_una_fecha():
    assert sources.season_of(date(2026, 10, 20)) == "2026-27"
    assert sources.season_of(date(2027, 4, 1)) == "2026-27"
    assert sources.season_of(date(2026, 9, 24)) == "2026-27"


def test_cuotas_americanas():
    assert sources.american_to_decimal(-110) == 1.909
    assert sources.american_to_decimal(250) == 3.5
    assert sources.american_to_decimal(None) is None


def _espn_item(book="DraftKings", spread=-7.5, total=219.0, ml_home=-330, ml_away=270, with_open=False):
    item = {
        "provider": {"name": book}, "spread": spread, "overUnder": total, "overOdds": -110, "underOdds": -105,
        "homeTeamOdds": {"moneyLine": ml_home, "spreadOdds": -110},
        "awayTeamOdds": {"moneyLine": ml_away, "spreadOdds": -110},
    }
    if with_open:
        item["homeTeamOdds"]["open"] = {"pointSpread": {"american": "-6.5"}, "spread": {"decimal": 1.9},
                                        "moneyLine": {"decimal": 1.3}}
        item["awayTeamOdds"]["open"] = {"pointSpread": {"american": "+6.5"}, "spread": {"decimal": 1.9},
                                        "moneyLine": {"decimal": 3.6}}
        item["open"] = {"total": {"american": "o220.5"}, "over": {"decimal": 1.91}, "under": {"decimal": 1.91}}
    return item


def test_cuotas_de_espn():
    rows = sources.parse_espn_odds([_espn_item(with_open=True), _espn_item(book="numberfire"),
                                    _espn_item(book="Caesars (New Jersey) - Live Odds")])
    books = {r["bookmaker"] for r in rows}
    assert books == {"DraftKings"}                        # proyecciones y cuotas en vivo, fuera
    close = {(r["market"], r["selection"]): r["odds"] for r in rows if r["snapshot"] == "CLOSE"}
    assert close[("SPREAD_-7.5", "HOME")] == 1.909
    assert close[("TOTAL_219", "UNDER")] == 1.952
    assert close[("ML", "AWAY")] == 3.7
    opened = {(r["market"], r["selection"]) for r in rows if r["snapshot"] == "OPEN"}
    assert ("SPREAD_-6.5", "HOME") in opened and ("TOTAL_220.5", "OVER") in opened and ("ML", "HOME") in opened
    assert sources.consensus_lines(rows) == {"spread": -7.5, "total": 219.0}


def test_hándicap_de_api_sports_es_la_línea_del_local_en_ambos_lados():
    resp = [{"bookmakers": [{"name": "Betano", "bets": [
        {"name": "Home/Away", "values": [{"value": "Home", "odd": "2.02"}, {"value": "Away", "odd": "1.70"}]},
        {"name": "Asian Handicap", "values": [{"value": "Home +1.5", "odd": "1.88"}, {"value": "Away +1.5", "odd": "1.80"}]},
        {"name": "Over/Under", "values": [{"value": "Over 187.5", "odd": "1.60"}, {"value": "Under 187.5", "odd": "2.15"}]},
    ]}]}]
    rows = {(r["market"], r["selection"]): r["odds"] for r in sources.parse_apisports_odds(resp)}
    assert rows[("SPREAD_1.5", "HOME")] == 1.88 and rows[("SPREAD_1.5", "AWAY")] == 1.80
    assert rows[("ML", "AWAY")] == 1.70 and rows[("TOTAL_187.5", "UNDER")] == 2.15


def test_apodo_estable():
    assert nickname("Portland Trail Blazers") == nickname("Blazers") == "blazers"
    assert nickname("LA Clippers") == nickname("Los Angeles Clippers")


def test_probabilidades_y_su_inversa():
    p = probability("SPREAD_-5.5", "HOME", mu_margin=5.5, mu_total=220, sd_margin=12, sd_total=18)
    assert p == pytest.approx(0.5)
    p_away = probability("SPREAD_-5.5", "AWAY", 8.0, 220, 12, 18)
    assert p_away < 0.5                                        # el local se espera que gane por 8: cubre
    assert probability("TOTAL_220.5", "OVER", 0, 230, 12, 18) > 0.5
    assert probability("ML", "HOME", 0, 220, 12, 18) == pytest.approx(0.5)
    # De la probabilidad sin margen de una línea se recupera el margen esperado.
    fair = {"HOME": probability("SPREAD_-3.5", "HOME", 6.0, 220, 12, 18)}
    kind, mu = implied_mu("SPREAD_-3.5", fair, 12, 18)
    assert kind == "margin" and mu == pytest.approx(6.0, abs=1e-6)
    kind, mu = implied_mu("TOTAL_224.5", {"OVER": probability("TOTAL_224.5", "OVER", 0, 228, 12, 18)}, 12, 18)
    assert kind == "total" and mu == pytest.approx(228, abs=1e-6)


def test_expectativa_del_mercado_pesa_más_a_pinnacle():
    odds = {"SPREAD_-4.5": {"Pinnacle": {"HOME": 1.95, "AWAY": 1.95}, "Betano": {"HOME": 1.60, "AWAY": 2.30}}}
    e = market_expectation(odds, 12, 18)
    assert 4.5 < e["margin"] < 6.0                             # cerca de Pinnacle (4.5), algo movido por Betano
    assert e["total"] is None
    assert blend(10.0, 5.0, 0.0) == 5.0 and blend(10.0, 5.0, 0.3) == pytest.approx(6.5)
    assert blend(10.0, None, 0.0) == 10.0


def test_mercados_nba_sin_margen():
    fair, n = fair_probabilities("TOTAL_224.5", {"a": {"OVER": 1.9, "UNDER": 1.9}, "b": {"OVER": 1.8}})
    assert n == 1 and fair["OVER"] == pytest.approx(0.5)


def test_liquidacion_nba():
    assert bet_won("ML", "HOME", 110, 105) and not bet_won("ML", "AWAY", 110, 105)
    assert bet_won("SPREAD_-5.5", "HOME", 110, 104) and bet_won("SPREAD_-5.5", "AWAY", 110, 105)
    assert bet_won("SPREAD_3.5", "HOME", 100, 103) and not bet_won("SPREAD_3.5", "HOME", 100, 104)
    assert bet_won("TOTAL_220.5", "OVER", 111, 110) and bet_won("TOTAL_220.5", "UNDER", 110, 110)
    with pytest.raises(UnknownMarketError):
        bet_won("TOTAL_220", "OVER", 110, 110)                  # línea entera: devolución
    with pytest.raises(UnknownMarketError):
        bet_won("ML", "HOME", 100, 100)


def _games(n_days=60):
    """Liga de 4 equipos: el 1 gana siempre por 10."""
    rows, gid = [], 0
    teams = [1, 2, 3, 4]
    for d in range(n_days):
        day = date(2024, 10, 22) + timedelta(days=d)
        for h, a in ((teams[d % 4], teams[(d + 1) % 4]), (teams[(d + 2) % 4], teams[(d + 3) % 4])):
            gid += 1
            base = 105
            hp = base + (10 if h == 1 else 0) + 2
            ap = base + (10 if a == 1 else 0)
            rows.append({"id": f"{gid:010d}", "season": "2024-25", "game_date": day, "home_team_id": h,
                         "away_team_id": a, "home_pts": hp, "away_pts": ap, "home_fga": 88, "away_fga": 88,
                         "home_oreb": 10, "away_oreb": 10, "home_tov": 13, "away_tov": 13, "home_fta": 20, "away_fta": 20})
    return pd.DataFrame(rows)


def test_el_modelo_aprende_quien_es_mejor_sin_ver_el_futuro():
    games = _games()
    preds, ratings = replay(games, Params())
    assert ratings.teams[1].off > ratings.teams[2].off
    last = preds.merge(games, on="id").tail(8)
    strong_home = last[last["home_team_id"] == 1]
    assert (strong_home["margin"] > 5).all()
    # Cambiar un resultado del último día no cambia NINGUNA predicción de ese día.
    changed = games.copy()
    last_day = changed["game_date"].max()
    changed.loc[changed["game_date"] == last_day, "home_pts"] += 40
    preds2, _ = replay(changed, Params())
    same_day = games.loc[games["game_date"] == last_day, "id"]
    assert preds.set_index("id").loc[same_day].equals(preds2.set_index("id").loc[same_day])


def test_acciones_nba(tmp_path, monkeypatch):
    from ml.nba import train
    params = tmp_path / "params.json"
    monkeypatch.setattr(train, "PARAMS_PATH", params)
    steps = on_demand.steps_for("nba", date(2026, 10, 25))
    assert [s.module for s in steps] == ["ml.nba.import_history", "ml.nba.train", "jobs.nba_update"]   # 1.ª vez: entrena
    params.write_text("{}")
    steps = on_demand.steps_for("nba", date(2026, 10, 25))
    assert [s.module for s in steps] == ["ml.nba.import_history", "jobs.nba_update"]
    assert "--apisports" not in steps[-1].args
    assert on_demand.steps_for("nba_cuotas", date(2026, 10, 25))[-1].args == ("--apisports",)


def test_actualizacion_nba_automatica_cada_3_horas(tmp_path, monkeypatch):
    monkeypatch.setattr(on_demand, "STATE_PATH", tmp_path / "state.json")
    now = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    assert on_demand.nba_update_due(now)
    on_demand.update_state(last={"nba_cuotas": {"finished_at": (now - timedelta(hours=1)).isoformat()}})
    assert not on_demand.nba_update_due(now)
    assert on_demand.nba_update_due(now + timedelta(hours=3))


def test_partido_de_hoy_sin_jugar_cuenta_para_el_cansancio_de_manana():
    games = _games(20)
    last = games["game_date"].max()
    team = int(games.loc[games["game_date"] == last, "home_team_id"].iloc[0])
    games.loc[games["game_date"] == last, ["home_pts", "away_pts"]] = None            # hoy, sin jugar
    tomorrow = {"id": "9999999999", "season": "2024-25", "game_date": last + timedelta(days=1),
                "home_team_id": team, "away_team_id": 99, "home_pts": None, "away_pts": None}
    with_tomorrow = pd.concat([games, pd.DataFrame([tomorrow])], ignore_index=True)
    rested = pd.concat([games[games["game_date"] < last], pd.DataFrame([tomorrow])], ignore_index=True)
    tired = replay(with_tomorrow, Params(b2b=2.0))[0].set_index("id").at["9999999999", "margin"]
    fresh = replay(rested, Params(b2b=2.0))[0].set_index("id").at["9999999999", "margin"]
    assert tired == pytest.approx(fresh - 2.0)


def test_referencia_de_precio_nunca_es_la_propia_casa():
    from ml.nba.markets import line_gap, reference_probability
    books = {"Pinnacle": {"HOME": 1.95, "AWAY": 1.95}, "Betano": {"HOME": 2.10, "AWAY": 1.75}}
    # Betano se mide contra Pinnacle sin margen (0.5), no contra sí misma.
    assert reference_probability("SPREAD_-4.5", "HOME", books, 4.5, 220, 12, 18, exclude="Betano") == pytest.approx(0.5)
    # Sin nadie más en esa línea: la normal alrededor de lo que espera el mercado.
    only = {"Betano": {"HOME": 2.10, "AWAY": 1.75}}
    assert reference_probability("SPREAD_-4.5", "HOME", only, 4.5, 220, 12, 18, exclude="Betano") == pytest.approx(0.5)
    # Distancia de la línea a lo esperado (local -4.5 con margen esperado 6 -> 1.5 pts).
    assert line_gap("SPREAD_-4.5", 6.0, 220) == pytest.approx(1.5)
    assert line_gap("TOTAL_224.5", 0, 221.0) == pytest.approx(3.5)
    assert line_gap("ML", 3.0, 220) is None
