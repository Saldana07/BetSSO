"""Factor árbitro de tarjetas: nombres, encogimiento, guarda de validación y
que solo afecte a tarjetas."""
import joblib
import pytest

from ml.features.referees import RefereeFactors, referee_key, shrunk_factor


@pytest.mark.parametrize("name,key", [
    ("A. Taylor", "a taylor"),
    ("Anthony Taylor, England", "a taylor"),        # la API alterna formatos entre temporadas
    ("Antonio Alberola Rojas, Spain", "a alberola"),
    ("A. Alberola", "a alberola"),
    ("Carlos del Cerro Grande", "c cerro"),          # partícula "del"
    ("Alejandro Muñiz", "a muniz"),                  # sin tildes
    (None, None),
    ("", None),
])
def test_clave_de_arbitro(name, key):
    assert referee_key(name) == key


def test_sin_historial_el_factor_es_uno():
    assert shrunk_factor(0.0, 0.0, lam=4.0) == pytest.approx(1.0)


def test_el_factor_se_encoge_hacia_uno():
    # 5 partidos sacando 50% más de lo esperado: el factor sube, pero menos de 1.5.
    f = shrunk_factor(actual_sum=30.0, expected_sum=20.0, lam=4.0)
    assert 1.0 < f < 1.5
    # Con mucho historial se acerca al ratio real.
    assert shrunk_factor(600.0, 400.0, lam=4.0) == pytest.approx(1.5, abs=0.05)


def test_tabla_no_validada_no_se_usa(tmp_path):
    path = tmp_path / "ref.joblib"
    joblib.dump({"table": {("England", "a taylor"): (30.0, 20.0, 5)}, "validated": False}, path)
    assert RefereeFactors.load(path) is None
    joblib.dump({"table": {("England", "a taylor"): (30.0, 20.0, 5)}, "validated": True}, path)
    rf = RefereeFactors.load(path)
    assert rf.factor("England", "Anthony Taylor, England", 4.0) > 1.0
    assert rf.factor("Spain", "Anthony Taylor, England", 4.0) == 1.0   # otro país, otro árbitro
    assert rf.factor("England", None, 4.0) == 1.0


def test_el_factor_solo_toca_tarjetas():
    import pandas as pd
    from ml.features.count_stats import CountStatModels

    class Always(RefereeFactors):
        def factor(self, country, referee, lam):
            return 2.0

    from ml.features.market_adjustment import MarketAdjustment

    models = CountStatModels.__new__(CountStatModels)
    models.referees = Always({})
    models.market = MarketAdjustment({})   # sin ajuste por cuotas: se mide solo el árbitro
    model = object()
    assert models._scale("cards", type("M", (), {"expected_values": lambda *a: (2.0, 2.0)})(), "A", "B",
                         pd.Timestamp("2025-01-01"), "England", "A. Taylor") == 2.0
    assert models._scale("fouls", model, "A", "B", pd.Timestamp("2025-01-01"), "England", "A. Taylor") == 1.0
    assert models._scale("cards", model, "A", "B", pd.Timestamp("2025-01-01"), "England", None) == 1.0
