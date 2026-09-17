import pytest

from ml.features.poisson_xg import extended_market_probabilities, market_probabilities


@pytest.fixture
def probs():
    return extended_market_probabilities(1.5, 1.1)


def test_incluye_los_mercados_basicos_sin_cambiarlos(probs):
    base = market_probabilities(1.5, 1.1)
    for k, v in base.items():
        assert probs[k] == pytest.approx(v)


def test_over_under_es_monotono_en_la_linea(probs):
    overs = [probs[f"OU_{l}_OVER"] for l in (0.5, 1.5, 2.5, 3.5, 4.5)]
    assert overs == sorted(overs, reverse=True)
    for l in (0.5, 1.5, 2.5, 3.5, 4.5):
        assert probs[f"OU_{l}_OVER"] + probs[f"OU_{l}_UNDER"] == pytest.approx(1.0)


def test_totales_por_equipo_coherentes_con_lambda(probs):
    # Con λ_local=1.5 > λ_visitante=1.1, el local debe superar 1.5 con más probabilidad.
    assert probs["TEAM_OU_HOME_1.5_OVER"] > probs["TEAM_OU_AWAY_1.5_OVER"]
    assert probs["TEAM_OU_HOME_0.5_OVER"] + probs["TEAM_OU_HOME_0.5_UNDER"] == pytest.approx(1.0)


def test_handicap_equivale_a_mercados_conocidos(probs):
    # Local -0.5 = victoria local; local +0.5 = local o empate (doble oportunidad 1X).
    assert probs["AH_-0.5_HOME"] == pytest.approx(probs["1X2_HOME"], abs=1e-9)
    assert probs["AH_+0.5_HOME"] == pytest.approx(probs["DOUBLE_CHANCE_1X"], abs=1e-9)
    assert probs["AH_-1.5_HOME"] < probs["AH_-0.5_HOME"]      # cubrir -1.5 es más difícil
    assert probs["AH_-1.5_HOME"] + probs["AH_-1.5_AWAY"] == pytest.approx(1.0)


def test_marcadores_exactos_suman_casi_uno(probs):
    total = sum(v for k, v in probs.items() if k.startswith("EXACT_SCORE_"))
    assert 0.9 < total <= 1.0  # 0-4 goles por equipo cubre la gran mayoría de la masa
    assert probs["EXACT_SCORE_1-1"] > probs["EXACT_SCORE_4-4"]
