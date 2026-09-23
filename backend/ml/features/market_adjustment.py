"""Ajuste del total esperado según lo que el mercado de 1X2 dice del partido.

Las cuotas de ganador (gratis: están en el histórico y en cada partido que
sincronizamos) resumen algo que el modelo por equipos no ve: si el partido es
parejo o hay un favorito claro. Medido sobre 7,451 partidos (ajuste en
2024-25, prueba en 2025-26): en TARJETAS, cuanto mayor el desequilibrio
|P(local) - P(visitante)|, menos tarjetas (coeficiente -0.31; Brier 0.2376 ->
0.2361, t = -3.5). En tiros, córners, tiros a puerta y faltas no aportó, así
que no se aplica ahí.

λ_ajustado = λ · exp(intercepto + coef · (desequilibrio - CENTRO)).
Lo ajusta y valida ml/training/fit_market_adjustment.py; solo se aplican los
ajustes con validated=True.
"""
import math
from pathlib import Path

import joblib

from ml.features.count_stats import MODELS_DIR

MARKET_ADJUSTMENT_PATH = MODELS_DIR / "market_adjustment.joblib"
GAP_CENTER = 0.3


class MarketAdjustment:
    def __init__(self, params: dict[str, dict]):
        self.params = {k: v for k, v in params.items() if v.get("validated")}

    @classmethod
    def load(cls, path: Path = MARKET_ADJUSTMENT_PATH) -> "MarketAdjustment":
        return cls(joblib.load(path) if Path(path).exists() else {})

    def factor(self, stat_key: str, p_home: float | None, p_away: float | None) -> float:
        prm = self.params.get(stat_key)
        if prm is None or p_home is None or p_away is None:
            return 1.0
        try:
            gap = abs(float(p_home) - float(p_away))
        except (TypeError, ValueError):
            return 1.0
        if math.isnan(gap):
            return 1.0
        return math.exp(prm["intercept"] + prm["coef_gap"] * (gap - GAP_CENTER))
