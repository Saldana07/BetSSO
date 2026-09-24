"""Modelos de conteo (córners, tiros, tarjetas...): índice temporal sin fuga,
combinación de columnas y capa de calibración."""
import numpy as np
import pandas as pd
import pytest
from sklearn.isotonic import IsotonicRegression

from ml.features.count_stats import (
    CARDS,
    CORNERS,
    MIN_MATCHES,
    SHOTS,
    CountStatModel,
    StatSpec,
    apply_calibration,
    normalize,
    over_under_probabilities,
    spec_for_market,
)


def _frame(rows):
    df = pd.DataFrame(rows, columns=["Division", "MatchDate", "HomeTeam", "AwayTeam", "home_value", "away_value"])
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
    return CountStatModel(CORNERS, _frame(rows), calibration_path=None)


def test_perfil_solo_usa_partidos_anteriores_al_corte(model):
    # Al 2025-01-05 solo hay 4 fechas -> 4 partidos de A: por debajo del mínimo.
    assert model.team_profile("A", pd.Timestamp("2025-01-05")) is None
    # B juega 2 por fecha: al 2025-01-06 lleva 10 = MIN_MATCHES.
    prof = model.team_profile("B", pd.Timestamp("2025-01-06"))
    assert prof is not None and prof.n_matches == MIN_MATCHES
    # B: contra A saca 3 y recibe 6; contra C saca 4 y recibe 4.
    assert prof.for_avg == pytest.approx(3.5)
    assert prof.against_avg == pytest.approx(5.0)


def test_partido_del_mismo_dia_no_cuenta(model):
    # Corte = medianoche del día: los partidos de ese día quedan fuera (sin fuga).
    assert model.team_profile("B", pd.Timestamp("2025-01-06")) == model.team_profile("B", pd.Timestamp("2025-01-06 20:00"))


def test_equipo_desconocido_devuelve_none(model):
    assert model.team_profile("Equipo Inventado", pd.Timestamp("2025-02-01")) is None
    assert model.expected_values("A", "Equipo Inventado", pd.Timestamp("2025-02-01")) is None
    assert model.market_probabilities("A", "Equipo Inventado", pd.Timestamp("2025-02-01")) is None


def test_promedio_liga_respeta_el_corte(model):
    assert model.league_averages("SP1", pd.Timestamp("2025-01-01")) == CORNERS.fallback_league_avg  # sin datos: prior
    home, away = model.league_averages("SP1", pd.Timestamp("2025-01-03"))
    assert home == pytest.approx(5.0) and away == pytest.approx(3.5)
    assert model.league_averages("XX", pd.Timestamp("2025-02-01")) == CORNERS.fallback_league_avg


def test_probabilidades_por_linea_suman_uno_y_decrecen():
    probs = over_under_probabilities(24.0, SHOTS)
    for line in SHOTS.lines:
        assert probs[f"SHOTS_OU_{line}_OVER"] + probs[f"SHOTS_OU_{line}_UNDER"] == pytest.approx(1.0)
    overs = [probs[f"SHOTS_OU_{line}_OVER"] for line in SHOTS.lines]
    assert overs == sorted(overs, reverse=True)          # más línea, menos P(over)
    assert 0.4 < probs["SHOTS_OU_23.5_OVER"] < 0.6        # λ=24 -> la línea 23.5 está cerca de la mediana


def test_spec_por_mercado():
    assert spec_for_market("SHOTS_OU_24.5") is SHOTS
    assert spec_for_market("CARDS_OU_4.5") is CARDS
    assert spec_for_market("OU_2.5") is None
    assert spec_for_market("1X2") is None


def test_sin_calibrador_las_probabilidades_salen_crudas():
    raw = over_under_probabilities(9.5, CORNERS)
    assert apply_calibration(raw, {}, CORNERS) == raw


def test_calibrador_encoge_los_extremos_y_recompone_under():
    # Curva que "desconfía" de los extremos: 0.7 -> 0.6, 0.3 -> 0.4.
    iso = IsotonicRegression(out_of_bounds="clip").fit([0.3, 0.5, 0.7], [0.4, 0.5, 0.6])
    raw = {"CORNERS_OU_9.5_OVER": 0.7, "CORNERS_OU_9.5_UNDER": 0.3,
           "CORNERS_OU_8.5_OVER": 0.8, "CORNERS_OU_8.5_UNDER": 0.2}
    out = apply_calibration(raw, {9.5: iso}, CORNERS)
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


def test_tarjetas_combinan_amarillas_y_rojas():
    # Tarjetas = amarillas + 2×rojas, la convención de las casas.
    from ml.features.count_stats import load_stat_frame

    spec = StatSpec("t", "T_OU", "HomeYellow", "AwayYellow", (4.5,), "t", (2.0, 2.0), extra_cols=(("HomeRed", "AwayRed", 2.0),))
    raw = pd.DataFrame({
        "Division": ["SP1"], "MatchDate": [pd.Timestamp("2025-01-01")], "HomeTeam": ["A"], "AwayTeam": ["B"],
        "home_key": ["a"], "away_key": ["b"], "HomeYellow": [3.0], "AwayYellow": [1.0], "HomeRed": [1.0], "AwayRed": [0.0],
    })
    import ml.features.count_stats as cs
    original = cs._load_raw
    cs._load_raw = lambda: raw
    try:
        df = load_stat_frame(spec)
    finally:
        cs._load_raw = original
    assert df.loc[0, "home_value"] == 5.0 and df.loc[0, "away_value"] == 1.0
    assert spec.calibration_path.name == "t_calibration.joblib"


def test_binomial_negativa_tiene_colas_mas_gordas_que_poisson():
    from ml.features.count_stats import total_cdf

    poisson_cdf = total_cdf(4.0, 0.0)
    nb_cdf = total_cdf(4.0, 0.07)
    # Misma media; con sobredispersión hay más masa en los extremos.
    assert 1 - nb_cdf[6] > 1 - poisson_cdf[6]      # P(> 6.5)
    assert nb_cdf[1] > poisson_cdf[1]              # P(<= 1)
    assert nb_cdf[-1] == pytest.approx(1.0, abs=1e-9)


def test_dispersion_cero_es_poisson():
    from scipy.stats import poisson

    from ml.features.count_stats import total_cdf

    assert total_cdf(9.5, 0.0) == pytest.approx(poisson.cdf(range(81), 9.5))


def test_todos_los_alias_apuntan_a_equipos_del_csv():
    # Un alias con destino inexistente deja al equipo sin predicción en silencio
    # (pasó con "psv eindhoven" -> "psv" desde el principio).
    from ml.features.count_stats import TEAM_ALIASES, default_resolver

    resolver = default_resolver()
    rotos = {k: v for k, v in TEAM_ALIASES.items() if v not in resolver.last_division}
    assert rotos == {}


def _resolver():
    from ml.features.count_stats import TeamResolver

    d = np.datetime64("2026-08-30")
    return TeamResolver({
        "magdeburg": (d, "D2"), "karlsruhe": (d, "D2"), "nacional": (d, "P1"), "barcelona": (d, "SP1"),
        "man united": (d, "E0"), "man city": (d, "E0"), "celtic": (d, "SC0"),
    })


@pytest.mark.parametrize(
    "name,country,expected",
    [
        ("1. FC Magdeburg", "Germany", "magdeburg"),    # núcleo sin forma jurídica
        ("Karlsruher SC", "Germany", "karlsruhe"),      # difuso
        ("Nacional", "Portugal", "nacional"),
        ("Nacional", "Uruguay", None),                  # liga no cubierta: aunque se llame igual
        ("Barcelona SC", "Ecuador", None),
        ("Barcelona SC", "World", None),                # internacional: nada difuso
        ("Barcelona", "World", "barcelona"),            # internacional: exacto sí
        ("Celtic", "England", None),                    # país de la liga != país del equipo
        ("Manchester United", "England", "man united"), # alias
        ("Magdeburg", None, "magdeburg"),               # sin país: sin guarda
    ],
)
def test_resolver_de_nombres(name, country, expected):
    assert _resolver().resolve(name, country) == expected


def test_validacion_exige_mejora_significativa():
    """Una "mejora" de 0.07 puntos en 7,700 partidos es ruido: no valida."""
    import numpy as np

    from ml.backtesting.validate_count_stats import T_REQUIRED, paired_t
    rng = np.random.default_rng(0)
    ruido = rng.choice([-1.0, 0.0, 1.0], size=7700, p=[0.05, 0.9007, 0.0493])
    assert abs(paired_t(ruido)) < T_REQUIRED
    real = rng.choice([-1.0, 0.0, 1.0], size=7700, p=[0.05, 0.85, 0.10])
    assert paired_t(real) > T_REQUIRED
