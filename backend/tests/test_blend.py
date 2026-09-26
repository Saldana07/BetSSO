"""Peso del modelo sobre la casa y su uso al decidir picks."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import Settings
from app.models import Odds
from jobs.market_evaluation import evaluate_match
from ml.value_betting.blend import blended_probability, estimate, live_weights
from ml.value_betting.confidence import SkillEvidence
from ml.value_betting.model_vs_market import EvaluationRow


def _rows(w_true: float, n_matches: int = 3000, market: str = "CORNERS_OU_9.5", seed: int = 0, offset: int = 0):
    """Partidos simulados en los que el resultado sigue a casa + w·(modelo − casa)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_matches):
        c = rng.uniform(0.3, 0.7)
        m = np.clip(c + rng.normal(0, 0.12), 0.05, 0.95)
        y = rng.random() < c + w_true * (m - c)
        rows.append(EvaluationRow(offset + i, market, float(m), float(c), bool(y)))
    return rows


def test_estima_el_peso_real():
    for w_true in (0.0, 0.5, 1.0):
        est = estimate(_rows(w_true, seed=int(w_true * 10)))
        assert est.w == pytest.approx(w_true, abs=0.15)
        assert est.n_matches == 3000 and est.se is not None


def test_pesos_en_vivo():
    rows = _rows(0.5) + _rows(0.5, market="CARDS_OU_4.5", seed=1, offset=10_000)
    w = live_weights(rows)
    assert 0.3 < w.default < 0.7
    assert w.weight("CARDS_OU_5.5") == w.by_family["CARDS"]
    # Un mercado donde el modelo va al revés de lo que pasa: la casa acierta más -> 0.
    rows_1x2 = [EvaluationRow(r.match_id, "1X2", r.model_probability, r.market_probability, r.won)
                for r in _rows(-1.0, seed=2, offset=20_000)]
    w = live_weights(rows + rows_1x2)
    assert w.by_family["1X2"] == 0.0 and w.source["1X2"] == "casa"


def test_sin_muestra_propia_hereda():
    europe = live_weights(_rows(0.5))
    few = _rows(1.0, n_matches=5, seed=3)
    w = live_weights(few, fallback=europe)
    assert w.borrowed and w.default == europe.default
    assert live_weights(few).default == 0.0                    # sin respaldo: solo la casa


def test_mezcla():
    assert blended_probability(0.50, 0.60, 0.0) == pytest.approx(0.50)
    assert blended_probability(0.50, 0.60, 0.5) == pytest.approx(0.55)
    assert blended_probability(None, 0.60, 0.5) is None        # sin casa no hay contra qué medir
    assert blended_probability(None, 0.60, 1.0) == pytest.approx(0.60)


def _odds(market, book, **prices):
    return [Odds(match_id=1, bookmaker=book, market=market, selection=s, odds_value=o, snapshot_type="LIVE")
            for s, o in prices.items()]


def _evaluate(odds_by_market, probabilities, markets, w):
    settings = Settings(allowed_bookmakers="Betano", odds_min=1.5, odds_max=2.1, min_pick_ev=0.02,
                        max_plausible_ev=0.2, min_historical_skill=0.0)
    return evaluate_match(
        SimpleNamespace(id=1), probabilities, markets,
        odds_for_market=lambda m: odds_by_market.get(m, []),
        evidence_for_market=lambda m: SkillEvidence(0.6, 0.5, 1000),
        settings=settings, now=datetime.now(timezone.utc),
        weight_for_market=(lambda m: w) if w is not None else None,
    )


def test_el_pick_se_decide_con_la_mezcla():
    # Betano paga 2.00 por Over; Bet365 y Pinnacle dicen que lo justo es ~50%.
    odds = {"CORNERS_OU_9.5": _odds("CORNERS_OU_9.5", "Betano", OVER=2.00, UNDER=1.80)
            + _odds("CORNERS_OU_9.5", "Bet365", OVER=1.90, UNDER=1.90)
            + _odds("CORNERS_OU_9.5", "Pinnacle", OVER=1.95, UNDER=1.95)}
    probs = {"CORNERS_OU_9.5_OVER": 0.60, "CORNERS_OU_9.5_UNDER": 0.40}

    raw = _evaluate(odds, probs, ["CORNERS_OU_9.5"], None)
    assert raw.candidates[0].expected_value == pytest.approx(0.20)        # modelo tal cual: +20%

    blended = _evaluate(odds, probs, ["CORNERS_OU_9.5"], 0.3)
    pick = blended.candidates[0]
    assert pick.model_probability == pytest.approx(0.53)                    # 0.50 + 0.3·(0.60 − 0.50)
    assert pick.expected_value == pytest.approx(0.06)
    # La evaluación sigue guardando al modelo (para medirlo contra la casa).
    ev = next(e for e in blended.evaluations if e.selection == "OVER")
    assert ev.model_probability == pytest.approx(0.60) and ev.expected_value == pytest.approx(0.20)

    # Con peso 0 solo cuenta el precio: 2.00 contra un justo de 50% no da ventaja.
    assert not _evaluate(odds, probs, ["CORNERS_OU_9.5"], 0.0).candidates


def test_la_casa_del_pick_no_es_su_propia_referencia():
    # Solo Betano publica la línea: no hay otra casa contra la que medirla.
    odds = {"CORNERS_OU_9.5": _odds("CORNERS_OU_9.5", "Betano", OVER=2.00, UNDER=1.80)}
    probs = {"CORNERS_OU_9.5_OVER": 0.60, "CORNERS_OU_9.5_UNDER": 0.40}
    res = _evaluate(odds, probs, ["CORNERS_OU_9.5"], 0.5)
    assert res.evaluations and not res.candidates


def test_doble_oportunidad_se_mide_con_el_1x2():
    odds = {"DOUBLE_CHANCE": _odds("DOUBLE_CHANCE", "Betano", **{"1X": 1.60, "X2": 1.70, "12": 1.30}),
            "1X2": _odds("1X2", "Pinnacle", HOME=2.50, DRAW=3.40, AWAY=3.00)}
    probs = {"DOUBLE_CHANCE_1X": 0.66, "DOUBLE_CHANCE_X2": 0.60, "DOUBLE_CHANCE_12": 0.74}
    res = _evaluate(odds, probs, ["DOUBLE_CHANCE"], 0.0)
    pick = next(c for c in res.candidates if c.selection == "1X")
    inv = {"H": 1 / 2.5, "D": 1 / 3.4, "A": 1 / 3.0}
    fair_1x = (inv["H"] + inv["D"]) / sum(inv.values())
    assert pick.model_probability == pytest.approx(fair_1x)


def test_colombia_sigue_al_modelo_tal_cual(monkeypatch):
    """Sección de prueba: w = 1, así su contador mide al modelo colombiano."""
    from ml.value_betting import blend
    monkeypatch.setattr(blend, "settled_rows", lambda db, v: _rows(0.3, n_matches=200))
    settings = Settings(colombia_model_only=True)
    w = blend.load_live_weights(None, settings, settings.colombia_model_version)
    assert w.model_only and w.weight("OU_1.5") == 1.0 and w.weight("1X2") == 1.0
    assert blended_probability(0.64, 0.77, w.weight("OU_1.5")) == pytest.approx(0.77)
    assert blended_probability(None, 0.77, 1.0) == pytest.approx(0.77)   # doble oportunidad sin referencia
    europa = blend.load_live_weights(None, settings, settings.model_version)
    assert not europa.model_only and europa.default < 1
    apagado = blend.load_live_weights(None, Settings(colombia_model_only=False), settings.colombia_model_version)
    assert not apagado.model_only
