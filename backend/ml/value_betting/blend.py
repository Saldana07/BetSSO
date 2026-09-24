"""Cuánto creerle al modelo por encima de la casa, medido con lo ya liquidado.

Para cada mercado evaluado hay dos probabilidades del mismo suceso: la del
modelo (m) y la de la casa sin margen (c). Si el modelo sabe algo que la casa
no, los resultados se desvían de c en la dirección de m:

    resultado - c = w · (m - c)

w = 0: el modelo no aporta nada sobre la casa; w = 1: hay que creerle del
todo. Se estima por mínimos cuadrados sin constante, con el error estándar
agrupado por PARTIDO (las líneas de un partido fallan o aciertan juntas). La
probabilidad con la que se decide un pick es

    p = c + w · (m - c)

Así la "ventaja" deja de ser la discrepancia cruda del modelo (que, al
ordenar por EV, elige justo los partidos donde más se equivoca) y pasa a ser
la parte de esa discrepancia que los resultados respaldan.

Qué peso usa cada familia de mercados:
  - la casa predice significativamente mejor (veredicto CASA en
    model_vs_market): 0;
  - la familia tiene muestra propia (MIN_MATCHES partidos) y su peso es
    significativo (|t| >= 2): el suyo;
  - si no, el peso conjunto de todas las familias (con pocos partidos, uno por
    familia es puro ruido).
Siempre recortado a [0, 1]; sin MIN_MATCHES partidos liquidados, 0 (o el de
`fallback`: Colombia usa el de Europa hasta tener muestra propia).
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field

from ml.value_betting.model_vs_market import MIN_MATCHES, T_THRESHOLD, EvaluationRow, compare, market_family


@dataclass
class WeightEstimate:
    w: float
    se: float | None
    t: float | None
    n_matches: int


@dataclass
class LiveWeights:
    pooled: WeightEstimate
    default: float                                            # peso conjunto recortado (o el de respaldo)
    by_family: dict[str, float] = field(default_factory=dict)
    source: dict[str, str] = field(default_factory=dict)      # familia -> "propio" | "conjunto" | "casa"
    borrowed: bool = False                                    # el peso viene de otro apartado

    def weight(self, market: str) -> float:
        return self.by_family.get(market_family(market), self.default)


def estimate(rows: list[EvaluationRow]) -> WeightEstimate:
    sxx = sxy = 0.0
    for r in rows:
        d = r.model_probability - r.market_probability
        sxx += d * d
        sxy += d * ((1.0 if r.won else 0.0) - r.market_probability)
    matches = {r.match_id for r in rows}
    if sxx <= 0:
        return WeightEstimate(0.0, None, None, len(matches))
    w = sxy / sxx
    score: dict[int, float] = defaultdict(float)
    for r in rows:
        d = r.model_probability - r.market_probability
        score[r.match_id] += d * ((1.0 if r.won else 0.0) - r.market_probability - w * d)
    se = math.sqrt(sum(g * g for g in score.values())) / sxx if len(score) >= 2 else None
    t = w / se if se else None
    return WeightEstimate(w, se, t, len(matches))


def _clip(w: float) -> float:
    return min(1.0, max(0.0, w))


def live_weights(rows: list[EvaluationRow], fallback: "LiveWeights | None" = None) -> LiveWeights:
    pooled = estimate(rows)
    enough = pooled.n_matches >= MIN_MATCHES
    if enough:
        default = _clip(pooled.w)
    else:
        default = fallback.default if fallback else 0.0
    out = LiveWeights(pooled=pooled, default=default, borrowed=not enough and fallback is not None)

    by_family: dict[str, list[EvaluationRow]] = defaultdict(list)
    for r in rows:
        by_family[market_family(r.market)].append(r)
    verdicts = {c.family: c.verdict for c in compare(rows)}
    for family, fam_rows in by_family.items():
        own = estimate(fam_rows)
        if verdicts.get(family) == "CASA":
            out.by_family[family], out.source[family] = 0.0, "casa"
        elif own.n_matches >= MIN_MATCHES and own.t is not None and abs(own.t) >= T_THRESHOLD:
            out.by_family[family], out.source[family] = _clip(own.w), "propio"
        else:
            out.by_family[family], out.source[family] = default, "conjunto"
    if fallback is not None and not enough:
        # Las familias que en el otro apartado ya tienen veredicto lo heredan.
        for family, source in fallback.source.items():
            if source in ("casa", "propio") and out.source.get(family, "conjunto") == "conjunto":
                out.by_family[family], out.source[family] = fallback.by_family[family], source
    return out


def blended_probability(market_probability: float | None, model_probability: float, w: float) -> float | None:
    """p = casa + w · (modelo - casa). Sin probabilidad de la casa no hay contra
    qué medir: None (no hay pick), salvo que el modelo pese del todo."""
    if market_probability is None:
        return model_probability if w >= 1.0 else None
    return market_probability + w * (model_probability - market_probability)


def settled_rows(db, model_version: str) -> list[EvaluationRow]:
    from sqlalchemy import select

    from app.models import MarketEvaluation
    return [
        EvaluationRow(e.match_id, e.market, float(e.model_probability), float(e.market_fair_probability), bool(e.is_win))
        for e in db.execute(select(MarketEvaluation).where(
            MarketEvaluation.model_version == model_version, MarketEvaluation.is_win.isnot(None),
            MarketEvaluation.market_fair_probability.isnot(None))).scalars()
    ]


def load_live_weights(db, settings, model_version: str) -> LiveWeights:
    """Pesos en vivo de un apartado; Colombia hereda los de Europa hasta tener
    muestra propia."""
    europe = live_weights(settled_rows(db, settings.model_version))
    if model_version == settings.model_version:
        return europe
    return live_weights(settled_rows(db, model_version), fallback=europe)
