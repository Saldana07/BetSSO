"""Matriz de marcadores: Dixon-Coles y coherencia entre mercados equivalentes."""
import numpy as np
import pytest

from ml.features.poisson_xg import DIXON_COLES_RHO, dixon_coles_tau, extended_market_probabilities, scoreline_matrix


def test_matriz_suma_uno_con_y_sin_rho():
    for rho in (0.0, -0.13, 0.05):
        assert scoreline_matrix(1.4, 1.1, rho=rho).sum() == pytest.approx(1.0)


def test_rho_negativo_sube_0_0_y_1_1_y_baja_1_0_y_0_1():
    base = scoreline_matrix(1.4, 1.1, rho=0.0)
    dc = scoreline_matrix(1.4, 1.1, rho=-0.13)
    assert dc[0, 0] > base[0, 0] and dc[1, 1] > base[1, 1]
    assert dc[1, 0] < base[1, 0] and dc[0, 1] < base[0, 1]
    # Fuera de las cuatro celdas, tau = 1: solo cambia por la renormalización.
    tau = dixon_coles_tau(1.4, 1.1, -0.13)
    assert tau[2:, :].min() == 1.0 and tau[:, 2:].min() == 1.0


def test_rho_por_defecto_es_el_ajustado_en_holdout():
    # rho = 0 es lo que mejor verosimilitud dio con las lambdas del regresor
    # (ver ml/training/fit_dixon_coles_rho.py). Cambiarlo requiere reajustar.
    assert DIXON_COLES_RHO == 0.0


def test_handicap_medio_equivale_a_1x2_en_la_matriz():
    p = extended_market_probabilities(1.5, 1.0)
    assert p["AH_-0.5_HOME"] == pytest.approx(p["1X2_HOME"])
    assert p["AH_+0.5_AWAY"] == pytest.approx(p["1X2_AWAY"])
    assert p["AH_+0.5_HOME"] == pytest.approx(p["1X2_HOME"] + p["1X2_DRAW"])
