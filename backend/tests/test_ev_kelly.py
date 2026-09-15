import pytest

from ml.value_betting.ev_kelly import closing_line_value, expected_value, is_safe_bet_candidate, kelly_criterion


def test_closing_line_value():
    assert closing_line_value(2.00, 1.85) == pytest.approx(0.081, abs=1e-3)   # el mercado nos dio la razón
    assert closing_line_value(1.65, 1.58) == pytest.approx(0.044, abs=1e-3)   # el pick real de ayer
    assert closing_line_value(1.80, 1.80) == 0.0                              # sin movimiento
    assert closing_line_value(1.70, 1.90) < 0                                 # el mercado se movió en contra


def test_expected_value():
    assert expected_value(0.55, 2.0) == pytest.approx(0.10)
    assert expected_value(0.50, 2.0) == pytest.approx(0.0)
    assert expected_value(0.40, 2.0) == pytest.approx(-0.20)


def test_kelly_nunca_negativo_y_capado():
    assert kelly_criterion(0.40, 2.0) == 0.0            # sin ventaja: no apostar
    assert kelly_criterion(0.60, 2.0) == pytest.approx(0.20 * 0.25)  # Kelly completo 0.20, fraccionado 1/4
    assert kelly_criterion(0.99, 1.01) <= 0.25          # nunca por encima del cap


def ok(**kw):
    base = dict(odds=1.7, expected_val=0.05, historical_skill=0.01)
    base.update(kw)
    return is_safe_bet_candidate(**base)


def test_rango_de_cuota():
    assert ok(odds=1.49) is False
    assert ok(odds=1.50) is True
    assert ok(odds=2.00) is True
    assert ok(odds=2.01) is False


def test_ev_piso_y_techo():
    assert ok(expected_val=0.0) is False
    assert ok(expected_val=0.25, max_ev=0.20) is False   # demasiado bueno para ser verdad
    assert ok(expected_val=0.15, max_ev=0.20) is True


def test_habilidad_no_accuracy():
    # El caso que motivó el cambio: córners 9.5 tiene MÁS habilidad que 8.5
    # aunque su accuracy sea menor. Ambas deben pasar; la de habilidad
    # negativa no.
    assert ok(historical_skill=0.010) is True     # 9.5: acc 0.533, base 0.523
    assert ok(historical_skill=0.0056) is True    # 8.5: acc 0.604, base 0.599
    assert ok(historical_skill=-0.01) is False    # peor que la tasa base


def test_mercado_sin_backtest_no_pasa_por_defecto():
    # Cierra el hueco de la auditoría: antes None pasaba en silencio.
    assert ok(historical_skill=None) is False
    assert ok(historical_skill=None, require_validation=False) is True
