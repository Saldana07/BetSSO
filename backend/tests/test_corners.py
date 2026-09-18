"""Modelo de córners: índice temporal sin fuga y capa de calibración."""
import numpy as np
import pandas as pd
import pytest
from sklearn.isotonic import IsotonicRegression

from ml.features.corners import (
    MIN_MATCHES,
    CornerModel,
    apply_calibration,
    corner_market_probabilities,
    normalize,
)


def _df(rows):
    df = pd.DataFrame(rows, columns=["Division", "MatchDate", "HomeTeam", "AwayTeam", "HomeCorners", "AwayCorners"])
    df["MatchDate"] = pd.to_datetime(df["MatchDate"])
    df["home_key"] = df["HomeTeam"].map(normalize)
    df["away_key"] = df["AwayTeam"].map(normalize)
    return df.sort_values("MatchDate").reset_index(drop=True)


@pytest.fixture
def model():
    rows = []
    # 15 fechas: A local vs B (A saca 6, B 3) y luego B local vs C (B 4, C 4)
    for i in range(15):
        day = pd.Timestamp("2025-01-01") + pd.Timedelta(days=i)
        rows.append(("SP1", day, "A", "B", 6, 3))
        rows.append(("SP1", day, "B", "C", 4, 4))
    return CornerModel(_df(rows), calibration_path=None)


def test_perfil_solo_usa_partidos_anteriores_al_corte(model):
    # Al 2025-01-05 solo hay 4 fechas -> 4 partidos de A: por debajo del mínimo.
    assert model.team_profile("A", pd.Timestamp("2025-01-05")) is None
    # B juega 2 por fecha: al 2025-01-06 lleva 10 = MIN_MATCHES.
    prof = model.team_profile("B", pd.Timestamp("2025-01-06"))
    assert prof is not None and prof.n_matches == MIN_MATCHES
    # B: contra A saca 3 y recibe 6; contra C saca 4 y recibe 4.
    assert prof.corners_for_avg == pytest.approx(3.5)
    assert prof.corners_against_avg == pytest.approx(5.0)


def test_partido_del_mismo_dia_no_cuenta(model):
    # Corte = medianoche del día: los partidos de ese día quedan fuera (sin fuga).
    a = model.team_profile("B", pd.Timestamp("2025-01-06"))
    b = model.team_profile("B", pd.Timestamp("2025-01-06 20:00"))
    assert a == b


def test_equipo_desconocido_devuelve_none(model):
    assert model.team_profile("Equipo Inventado", pd.Timestamp("2025-02-01")) is None
    assert model.expected_corners("A", "Equipo Inventado", pd.Timestamp("2025-02-01")) is None


def test_promedio_liga_respeta_el_corte(model):
    assert model.league_averages("SP1", pd.Timestamp("2025-01-01")) == (5.6, 4.6)  # sin datos: prior
    home, away = model.league_averages("SP1", pd.Timestamp("2025-01-03"))
    assert home == pytest.approx(5.0) and away == pytest.approx(3.5)
    assert model.league_averages("XX", pd.Timestamp("2025-02-01")) == (5.6, 4.6)


def test_sin_calibrador_las_probabilidades_salen_crudas():
    raw = corner_market_probabilities(5.0, 4.5)
    assert apply_calibration(raw, {}) == raw
    assert raw["CORNERS_OU_9.5_OVER"] + raw["CORNERS_OU_9.5_UNDER"] == pytest.approx(1.0)


def test_calibrador_encoge_los_extremos_y_recompone_under():
    # Curva que "desconfía" de los extremos: 0.7 -> 0.6, 0.3 -> 0.4.
    iso = IsotonicRegression(out_of_bounds="clip").fit([0.3, 0.5, 0.7], [0.4, 0.5, 0.6])
    raw = {"CORNERS_OU_9.5_OVER": 0.7, "CORNERS_OU_9.5_UNDER": 0.3,
           "CORNERS_OU_8.5_OVER": 0.8, "CORNERS_OU_8.5_UNDER": 0.2}
    out = apply_calibration(raw, {9.5: iso})
    assert out["CORNERS_OU_9.5_OVER"] == pytest.approx(0.6)
    assert out["CORNERS_OU_9.5_UNDER"] == pytest.approx(0.4)
    # La línea sin calibrador no se toca.
    assert out["CORNERS_OU_8.5_OVER"] == 0.8 and out["CORNERS_OU_8.5_UNDER"] == 0.2
    assert raw["CORNERS_OU_9.5_OVER"] == 0.7  # no muta la entrada


def test_market_probabilities_aplica_el_calibrador(model):
    iso = IsotonicRegression(out_of_bounds="clip").fit([0.0, 1.0], [0.5, 0.5])  # todo a 0.5
    model.calibrators = {9.5: iso}
    probs = model.market_probabilities("A", "B", pd.Timestamp("2025-01-20"))
    assert probs is not None
    assert probs["CORNERS_OU_9.5_OVER"] == pytest.approx(0.5)
    assert probs["CORNERS_OU_8.5_OVER"] != pytest.approx(0.5)
