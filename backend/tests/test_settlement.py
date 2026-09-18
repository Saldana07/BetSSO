import pytest

from ml.value_betting.settlement import MissingStatsError, UnknownMarketError, bet_won, profit_loss


@pytest.mark.parametrize(
    "market,selection,hc,ac,expected",
    [
        ("CORNERS_OU_9.5", "OVER", 6, 4, True),    # 10 > 9.5
        ("CORNERS_OU_9.5", "OVER", 5, 4, False),   # 9
        ("CORNERS_OU_9.5", "UNDER", 5, 4, True),
        ("CORNERS_OU_9.5", "UNDER", 6, 4, False),
        ("CORNERS_OU_8.5", "OVER", 5, 4, True),    # 9 > 8.5
        ("CORNERS_OU_8.5", "UNDER", 4, 4, True),   # 8
    ],
)
def test_corners_over_under(market, selection, hc, ac, expected):
    # Los goles no importan para este mercado; se pasan valores arbitrarios.
    assert bet_won(market, selection, 1, 1, home_corners=hc, away_corners=ac) is expected


def test_corners_sin_datos_no_se_liquida():
    # Sin córners cargados, NO se debe inventar un resultado: se deja pendiente.
    with pytest.raises(MissingStatsError):
        bet_won("CORNERS_OU_9.5", "OVER", 1, 1)
    with pytest.raises(MissingStatsError):
        bet_won("CORNERS_OU_9.5", "OVER", 1, 1, home_corners=5, away_corners=None)
    with pytest.raises(MissingStatsError):
        bet_won("CORNERS_OU_9.5", "OVER", 1, 1, stats={"corners": None, "shots": 20})


@pytest.mark.parametrize(
    "market,selection,stats,expected",
    [
        ("SHOTS_OU_24.5", "OVER", {"shots": 25}, True),
        ("SHOTS_OU_24.5", "OVER", {"shots": 24}, False),
        ("SHOTS_OU_24.5", "UNDER", {"shots": 24}, True),
        ("SOT_OU_8.5", "OVER", {"shots_on_target": 9}, True),
        ("SOT_OU_8.5", "UNDER", {"shots_on_target": 9}, False),
        ("CARDS_OU_4.5", "OVER", {"cards": 5}, True),      # 3 amarillas + 1 roja = 5
        ("CARDS_OU_4.5", "UNDER", {"cards": 4}, True),
        ("FOULS_OU_25.5", "OVER", {"fouls": 26}, True),
        ("FOULS_OU_25.5", "OVER", {"fouls": 25}, False),
        ("CORNERS_OU_9.5", "OVER", {"corners": 10}, True),  # córners también por stats
    ],
)
def test_mercados_de_conteo(market, selection, stats, expected):
    assert bet_won(market, selection, 0, 0, stats=stats) is expected


def test_mercado_de_conteo_sin_su_estadistica_espera():
    # Tener los córners no sirve para liquidar tiros.
    with pytest.raises(MissingStatsError):
        bet_won("SHOTS_OU_24.5", "OVER", 0, 0, stats={"corners": 10})
    with pytest.raises(MissingStatsError):
        bet_won("FOULS_OU_25.5", "UNDER", 0, 0, stats={"fouls": None})


@pytest.mark.parametrize(
    "selection,home,away,expected",
    [
        ("HOME", 2, 1, True),
        ("HOME", 1, 1, False),
        ("HOME", 0, 1, False),
        ("DRAW", 1, 1, True),
        ("DRAW", 0, 0, True),
        ("DRAW", 2, 1, False),
        ("AWAY", 0, 1, True),
        ("AWAY", 1, 1, False),
    ],
)
def test_1x2(selection, home, away, expected):
    assert bet_won("1X2", selection, home, away) is expected


@pytest.mark.parametrize(
    "selection,home,away,expected",
    [
        ("1X", 2, 1, True),   # gana local
        ("1X", 1, 1, True),   # empate tambien cuenta
        ("1X", 0, 1, False),
        ("X2", 0, 1, True),
        ("X2", 1, 1, True),
        ("X2", 2, 1, False),
        ("12", 2, 1, True),
        ("12", 0, 1, True),
        ("12", 1, 1, False),  # el empate es justo lo que pierde
    ],
)
def test_double_chance(selection, home, away, expected):
    assert bet_won("DOUBLE_CHANCE", selection, home, away) is expected


@pytest.mark.parametrize(
    "market,selection,home,away,expected",
    [
        ("OU_1.5", "OVER", 1, 1, True),    # 2 goles > 1.5
        ("OU_1.5", "OVER", 1, 0, False),   # 1 gol
        ("OU_1.5", "UNDER", 1, 0, True),
        ("OU_1.5", "UNDER", 1, 1, False),
        ("OU_2.5", "OVER", 2, 1, True),    # 3 goles
        ("OU_2.5", "OVER", 1, 1, False),   # 2 goles
        ("OU_2.5", "UNDER", 1, 1, True),
        ("OU_2.5", "UNDER", 3, 0, False),
    ],
)
def test_over_under(market, selection, home, away, expected):
    assert bet_won(market, selection, home, away) is expected


@pytest.mark.parametrize(
    "selection,home,away,expected",
    [
        ("YES", 1, 1, True),
        ("YES", 2, 3, True),
        ("YES", 1, 0, False),
        ("YES", 0, 0, False),
        ("NO", 1, 0, True),
        ("NO", 0, 0, True),
        ("NO", 1, 1, False),
    ],
)
def test_btts(selection, home, away, expected):
    assert bet_won("BTTS", selection, home, away) is expected


@pytest.mark.parametrize(
    "market,selection,home,away,expected",
    [
        ("OU_0.5", "OVER", 1, 0, True),
        ("OU_0.5", "UNDER", 0, 0, True),
        ("OU_3.5", "OVER", 2, 2, True),
        ("OU_3.5", "UNDER", 2, 1, True),
        ("OU_4.5", "OVER", 3, 2, True),
        ("OU_4.5", "OVER", 2, 2, False),
    ],
)
def test_over_under_todas_las_lineas(market, selection, home, away, expected):
    assert bet_won(market, selection, home, away) is expected


@pytest.mark.parametrize(
    "market,selection,home,away,expected",
    [
        ("TEAM_OU_HOME_1.5", "OVER", 2, 0, True),
        ("TEAM_OU_HOME_1.5", "OVER", 1, 3, False),   # el visitante no cuenta
        ("TEAM_OU_HOME_1.5", "UNDER", 1, 3, True),
        ("TEAM_OU_AWAY_0.5", "OVER", 0, 1, True),
        ("TEAM_OU_AWAY_0.5", "UNDER", 3, 0, True),
        ("TEAM_OU_AWAY_2.5", "OVER", 0, 2, False),
    ],
)
def test_totales_por_equipo(market, selection, home, away, expected):
    assert bet_won(market, selection, home, away) is expected


@pytest.mark.parametrize(
    "market,selection,home,away,expected",
    [
        ("AH_-1.5", "HOME", 3, 1, True),    # local gana por 2: cubre -1.5
        ("AH_-1.5", "HOME", 2, 1, False),   # gana por 1: no cubre
        ("AH_-1.5", "AWAY", 2, 1, True),    # visitante +1.5 cubre
        ("AH_+0.5", "HOME", 1, 1, True),    # empate: local +0.5 cubre (= 1X)
        ("AH_-0.5", "HOME", 1, 1, False),   # empate: local -0.5 no cubre (= 1X2 local)
        ("AH_+1.5", "AWAY", 0, 3, True),    # visitante -1.5 cubre
        ("AH_+2.5", "HOME", 0, 2, True),    # local +2.5 cubre perdiendo por 2
    ],
)
def test_handicap_asiatico_lineas_medias(market, selection, home, away, expected):
    assert bet_won(market, selection, home, away) is expected


def test_handicap_entero_o_cuarto_no_se_liquida():
    # Implican devolución total o parcial; mejor fallar que inventar.
    with pytest.raises(UnknownMarketError):
        bet_won("AH_-1.0", "HOME", 2, 1)
    with pytest.raises(UnknownMarketError):
        bet_won("AH_-0.75", "HOME", 2, 1)


def test_marcador_exacto():
    assert bet_won("EXACT_SCORE", "2-1", 2, 1) is True
    assert bet_won("EXACT_SCORE", "2-1", 1, 2) is False
    assert bet_won("EXACT_SCORE", "0-0", 0, 0) is True


def test_mercado_desconocido_falla_ruidosamente():
    # Preferible a devolver False en silencio: un mercado mal resuelto
    # corrompe el historial de rentabilidad sin que nadie se entere.
    with pytest.raises(UnknownMarketError):
        bet_won("HANDICAP_ASIATICO", "HOME", 1, 0)
    with pytest.raises(UnknownMarketError):
        bet_won("1X2", "SELECCION_INVENTADA", 1, 0)


def test_profit_loss():
    assert profit_loss(won=True, odds=1.65) == pytest.approx(0.65)
    assert profit_loss(won=False, odds=1.65) == pytest.approx(-1.0)
    assert profit_loss(won=True, odds=2.00, stake=10) == pytest.approx(10.0)
