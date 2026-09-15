from ml.value_betting.confidence import SkillEvidence, confidence_label


def ev(accuracy, base, n=500):
    return SkillEvidence(accuracy=accuracy, base_accuracy=base, n_bets=n)


def test_accuracy_alta_sin_habilidad_es_baja():
    # El caso que motivó el rediseño: 60.4% en un mercado cuya base es 59.9%.
    # Con la etiqueta vieja salía MEDIA; es ~0 habilidad y debe ser BAJA.
    assert confidence_label(0.10, ev(0.604, 0.599)) == "BAJA"


def test_misma_accuracy_distinta_base_distinta_etiqueta():
    # 60% en victoria local (base 44%) es una ventaja enorme; 60% en córners
    # (base 60%) no es nada. La accuracy cruda no lo distingue; la habilidad sí.
    assert confidence_label(0.05, ev(0.60, 0.44)) == "ALTA"
    assert confidence_label(0.05, ev(0.60, 0.60)) == "BAJA"


def test_ev_es_piso_no_multiplicador():
    # Un EV grande no sube la confianza (suele ser un modelo mal calibrado)...
    assert confidence_label(0.19, ev(0.55, 0.53)) == "MEDIA"
    # ...pero un EV minúsculo sí la baja aunque el modelo sea bueno.
    assert confidence_label(0.005, ev(0.60, 0.44)) == "BAJA"


def test_muestra_pequena_nunca_es_confiable():
    assert confidence_label(0.10, ev(0.70, 0.44, n=30)) == "BAJA"


def test_sin_backtest_es_baja():
    assert confidence_label(0.10, SkillEvidence(None, None, None)) == "BAJA"
    assert confidence_label(0.10, ev(0.60, None)) == "BAJA"
