"""Medir el modelo contra la casa: margen, veredicto, presupuesto de API y la
guarda del complemento del histórico."""
import numpy as np
import pandas as pd
import pytest

from jobs.fetch_match_data import RESERVE_AFTER_DAILY, RESERVE_BEFORE_DAILY, request_budget
from ml.features.count_stats import OVERLAY_MIN_MATCHES, SHOTS, overlay_for_spec
from ml.value_betting.market import complete_selection_set, fair_probabilities
from ml.value_betting.model_vs_market import MIN_MATCHES, EvaluationRow, compare, market_family


# --- Probabilidad sin margen ---

def test_quita_el_margen_proporcionalmente():
    fair, n = fair_probabilities("SHOTS_OU_24.5", {"Bet365": {"OVER": 1.83, "UNDER": 1.83}})
    assert n == 1
    assert fair["OVER"] == pytest.approx(0.5) and fair["UNDER"] == pytest.approx(0.5)


def test_mediana_entre_casas_y_suma_uno():
    fair, n = fair_probabilities("1X2", {
        "A": {"HOME": 2.0, "DRAW": 3.4, "AWAY": 3.8},
        "B": {"HOME": 2.1, "DRAW": 3.3, "AWAY": 3.6},
        "C": {"HOME": 1.9, "DRAW": 3.5, "AWAY": 4.0},
    })
    assert n == 3
    assert sum(fair.values()) == pytest.approx(1.0)
    assert fair["HOME"] > fair["AWAY"] > 0


def test_casa_sin_ambos_lados_no_cuenta():
    # Sin el otro lado no se sabe cuánto margen hay.
    assert fair_probabilities("CARDS_OU_4.5", {"X": {"OVER": 1.9}}) == ({}, 0)
    fair, n = fair_probabilities("CARDS_OU_4.5", {"X": {"OVER": 1.9}, "Y": {"OVER": 1.8, "UNDER": 2.0}})
    assert n == 1


def test_cuotas_incoherentes_se_descartan():
    # Suma de inversas < 0.98 (arbitraje / una cuota desactualizada).
    assert fair_probabilities("OU_2.5", {"X": {"OVER": 2.5, "UNDER": 2.5}}) == ({}, 0)


def test_mercados_que_no_son_particion():
    assert complete_selection_set("DOUBLE_CHANCE") is None
    assert complete_selection_set("EXACT_SCORE") is None
    assert complete_selection_set("TEAM_OU_HOME_1.5") == {"OVER", "UNDER"}
    assert complete_selection_set("AH_-0.5") == {"HOME", "AWAY"}


# --- Veredicto ---

def _rows(n_matches, p_model, p_market, win_rate, market="SHOTS_OU_24.5"):
    rng = np.random.default_rng(0)
    return [EvaluationRow(i, market, p_model, p_market, bool(rng.random() < win_rate)) for i in range(n_matches)]


def test_sin_muestra_no_hay_veredicto():
    (c,) = compare(_rows(MIN_MATCHES - 1, 0.7, 0.5, 0.7))
    assert c.verdict == "POCA_MUESTRA"


def test_modelo_que_acierta_mas_gana():
    # La realidad es 70%: el modelo (70%) se equivoca menos que la casa (50%).
    (c,) = compare(_rows(1000, 0.7, 0.5, 0.7))
    assert c.verdict == "MODELO" and c.brier_model < c.brier_market


def test_casa_que_acierta_mas_gana():
    (c,) = compare(_rows(1000, 0.7, 0.5, 0.5))
    assert c.verdict == "CASA"


def test_la_unidad_es_el_partido_no_la_linea():
    # 10 líneas del mismo partido no son 10 casos independientes.
    rows = [EvaluationRow(1, f"SHOTS_OU_{l}.5", 0.6, 0.5, True) for l in range(20, 30)]
    (c,) = compare(rows)
    assert c.n_matches == 1 and c.n_markets == 10 and c.verdict == "POCA_MUESTRA"


def test_familias():
    assert market_family("SHOTS_OU_24.5") == "SHOTS"
    assert market_family("SOT_OU_8.5") == "SOT"
    assert market_family("OU_2.5") == "GOALS_OU"
    assert market_family("TEAM_OU_HOME_1.5") == "GOALS_OU"
    assert market_family("AH_-0.5") == "AH"
    assert market_family("1X2") == "1X2"


# --- Presupuesto de API ---

def test_reserva_para_el_flujo_diario():
    assert request_budget(used=0, limit=100, daily_done=False) == 100 - RESERVE_BEFORE_DAILY
    assert request_budget(used=0, limit=100, daily_done=True) == 100 - RESERVE_AFTER_DAILY
    assert request_budget(used=99, limit=100, daily_done=True) == 0


# --- Guarda del complemento del histórico ---

def _base(n=200, mean=24.0):
    rng = np.random.default_rng(1)
    dates = pd.date_range("2026-07-01", periods=n, freq="6h")
    return pd.DataFrame({
        "Division": "E0", "MatchDate": dates, "HomeTeam": "A", "AwayTeam": "B", "home_key": "a", "away_key": "b",
        "home_value": rng.poisson(mean / 2, n).astype(float), "away_value": rng.poisson(mean / 2, n).astype(float),
    })


def _overlay(n, mean, start="2026-09-10"):
    rng = np.random.default_rng(2)
    return pd.DataFrame({
        "Division": "E0", "MatchDate": pd.date_range(start, periods=n, freq="12h"), "HomeTeam": "A", "AwayTeam": "B",
        "home_key": "a", "away_key": "b",
        "home_shots": rng.poisson(mean / 2, n).astype(float), "away_shots": rng.poisson(mean / 2, n).astype(float),
    })


def test_complemento_que_cuenta_igual_se_usa():
    extra, report = overlay_for_spec(SHOTS, _overlay(60, 24.0), _base())
    assert report["used"] and extra is not None and len(extra) == 60


def test_complemento_que_cuenta_distinto_no_se_usa():
    # La API contando "tiros" de otra forma (+40%) no debe mezclarse.
    extra, report = overlay_for_spec(SHOTS, _overlay(60, 34.0), _base())
    assert not report["used"] and extra is None and "distinto" in report["reason"]


def test_complemento_con_poca_muestra_no_se_usa():
    extra, report = overlay_for_spec(SHOTS, _overlay(OVERLAY_MIN_MATCHES - 1, 24.0), _base())
    assert not report["used"] and extra is None


def test_complemento_solo_despues_del_csv():
    # Partidos anteriores al último del CSV ya están en el CSV: no se duplican.
    extra, report = overlay_for_spec(SHOTS, _overlay(60, 24.0, start="2026-07-02"), _base())
    assert report["n"] < 60
