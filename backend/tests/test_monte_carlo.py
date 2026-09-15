import pytest

from ml.features.poisson_xg import market_probabilities
from ml.simulation.monte_carlo import compare_with, simulate_match


def test_probabilidades_suman_uno_y_son_reproducibles():
    a = simulate_match(1.5, 1.1, home_history=40, away_history=40, seed=7)
    b = simulate_match(1.5, 1.1, home_history=40, away_history=40, seed=7)
    assert a.p_home + a.p_draw + a.p_away == pytest.approx(1.0)
    assert (a.p_home, a.p_draw, a.p_away) == (b.p_home, b.p_draw, b.p_away)


def test_con_mucho_historial_converge_a_la_poisson_analitica():
    # Con k grande la Gamma se estrecha y la simulación debe acercarse a la
    # fórmula cerrada: es la comprobación de que no hay error de implementación.
    lam_h, lam_a = 1.6, 1.0
    sim = simulate_match(lam_h, lam_a, home_history=50, away_history=50, n_simulations=200_000, seed=1)
    exact = market_probabilities(lam_h, lam_a)
    assert sim.p_home == pytest.approx(exact["1X2_HOME"], abs=0.015)
    assert sim.p_draw == pytest.approx(exact["1X2_DRAW"], abs=0.015)
    assert sim.p_away == pytest.approx(exact["1X2_AWAY"], abs=0.015)


def test_menos_historial_da_intervalos_mas_anchos():
    # Es la razón de ser de la simulación: la incertidumbre debe crecer
    # cuando sabemos menos de los equipos.
    poco = simulate_match(1.4, 1.2, home_history=5, away_history=5, seed=3)
    mucho = simulate_match(1.4, 1.2, home_history=50, away_history=50, seed=3)
    ancho = lambda ci: ci[1] - ci[0]
    assert ancho(poco.ci_home) > ancho(mucho.ci_home)


def test_distribucion_de_goles_es_coherente():
    sim = simulate_match(1.5, 1.2, home_history=30, away_history=30, seed=5)
    assert sum(sim.total_goals_distribution.values()) == pytest.approx(1.0)
    assert sim.top_scorelines[0][1] > 0
    assert 0 < sim.p_over_2_5 < 1
    assert 0 < sim.p_btts < 1


def test_discrepancia_entre_modelos_no_es_valor():
    # Aunque Montecarlo supere al mercado por mucho, si el clasificador no
    # coincide, la conclusión debe ser incertidumbre, no valor.
    sim = simulate_match(2.2, 0.8, home_history=50, away_history=50, seed=2)  # P(local) alta
    clf = {"HOME": 0.45, "DRAW": 0.28, "AWAY": 0.27}                          # clasificador tibio
    odds = {"HOME": 2.2, "DRAW": 3.4, "AWAY": 3.2}
    cmp = compare_with(sim, clf, odds)
    assert cmp.model_disagreement > 0.08
    assert cmp.value_candidates == []
    assert "discrepan" in cmp.conclusion


def test_valor_solo_cuando_ambos_modelos_superan_al_mercado():
    sim = simulate_match(1.9, 0.9, home_history=50, away_history=50, seed=4)
    clf = {"HOME": sim.p_home, "DRAW": sim.p_draw, "AWAY": sim.p_away}   # modelos de acuerdo
    odds = {"HOME": 2.4, "DRAW": 3.3, "AWAY": 3.0}                          # mercado infravalora al local
    cmp = compare_with(sim, clf, odds)
    assert cmp.market_fair is not None
    assert sum(cmp.market_fair.values()) == pytest.approx(1.0)              # margen eliminado
    assert any(c["outcome"] == "HOME" for c in cmp.value_candidates)
    assert "candidato a apuesta de valor" in cmp.conclusion


def test_sin_cuotas_no_se_evalua_valor():
    sim = simulate_match(1.4, 1.2, home_history=30, away_history=30, seed=6)
    clf = {"HOME": sim.p_home, "DRAW": sim.p_draw, "AWAY": sim.p_away}
    cmp = compare_with(sim, clf, None)
    assert cmp.market_fair is None
    assert cmp.value_candidates == []
    assert "no hay cuotas" in cmp.conclusion
