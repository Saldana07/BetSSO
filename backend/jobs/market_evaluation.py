"""Evaluación de TODOS los mercados de un partido contra sus cuotas.

La usan el job diario (generate_daily_pick: cuotas actuales, y de aquí salen
los candidatos a pick) y el relleno de partidos pasados (backfill_evaluations:
la última foto de cuotas ANTES del inicio). Cada mercado con cuota y
probabilidad del modelo se guarda como MarketEvaluation, pase o no el filtro
de pick: son la materia prima para medir el modelo contra la casa.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.core.config import Settings
from app.models import Match, MarketEvaluation, Odds, Prediction
from ml.value_betting.confidence import SkillEvidence
from ml.value_betting.blend import blended_probability
from ml.value_betting.ev_kelly import evaluate_bet, is_safe_bet_candidate
from ml.value_betting.market import fair_probabilities


def validation_label(skill: float | None, settings: Settings) -> str:
    if skill is None:
        return "SIN_VALIDAR"
    return "VALIDADO" if skill >= settings.min_historical_skill else "SIN_VENTAJA"


# Doble oportunidad: sus selecciones se solapan (no hay "precio justo" propio);
# su referencia sale del 1X2 de las mismas casas.
DOUBLE_CHANCE_FROM_1X2 = {"1X": ("HOME", "DRAW"), "X2": ("DRAW", "AWAY"), "12": ("HOME", "AWAY")}


def _by_bookmaker(odds_rows: list[Odds]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for o in odds_rows:
        out.setdefault(o.bookmaker, {})[o.selection] = float(o.odds_value)
    return out


def reference_probabilities(market: str, by_bookmaker: dict[str, dict[str, float]], exclude: str | None,
                            odds_for_market: Callable[[str], list[Odds]]) -> dict[str, float]:
    """Probabilidad sin margen de cada selección según las casas distintas de
    `exclude` (la que paga el pick: nunca se compara una casa consigo misma)."""
    if market == "DOUBLE_CHANCE":
        onextwo = {b: s for b, s in _by_bookmaker(odds_for_market("1X2")).items() if b != exclude}
        fair, _ = fair_probabilities("1X2", onextwo)
        return {sel: sum(fair[p] for p in parts) for sel, parts in DOUBLE_CHANCE_FROM_1X2.items()} if fair else {}
    fair, _ = fair_probabilities(market, {b: s for b, s in by_bookmaker.items() if b != exclude})
    return fair


@dataclass
class MatchEvaluation:
    evaluations: list[MarketEvaluation]
    candidates: list[Prediction]   # los que pasan el filtro estricto de pick


def evaluate_match(
    match: Match,
    probabilities: dict,
    markets: list[str],
    odds_for_market: Callable[[str], list[Odds]],
    evidence_for_market: Callable[[str], SkillEvidence],
    settings: Settings,
    now: datetime,
    model_version: str | None = None,
    require_validation: bool = True,
    weight_for_market: Callable[[str], float] | None = None,
) -> MatchEvaluation:
    """`model_version`: la de Europa por defecto; Colombia usa la suya.
    `require_validation=False` solo para los picks EXPERIMENTALES de Colombia
    (sin backtest propio todavía; su contador aparte es su validación).

    `weight_for_market`: cuánto creerle al modelo sobre la casa en ese mercado
    (ml/value_betting/blend.py). Los picks se deciden con
    p = casa + w · (modelo - casa), donde "casa" es la probabilidad sin margen
    de las DEMÁS casas (no la que paga el pick). Las evaluaciones guardan la
    probabilidad y el EV del MODELO: así se lo sigue midiendo contra la casa.
    Sin `weight_for_market` (relleno de evaluaciones) se usa el modelo tal cual."""
    model_version = model_version or settings.model_version
    evaluations: list[MarketEvaluation] = []
    candidates: list[Prediction] = []

    for market in markets:
        odds_rows = odds_for_market(market)
        if not odds_rows:
            continue
        evidence = evidence_for_market(market)

        by_bookmaker = _by_bookmaker(odds_rows)
        fair, n_bookmakers = fair_probabilities(market, by_bookmaker)

        # Mejor cuota por selección entre las casas donde se puede apostar: es
        # la cuota que el usuario conseguiría. `predictions` y las evaluaciones
        # son únicas por (partido, modelo, mercado, selección).
        best: dict[str, Odds] = {}
        for o in odds_rows:
            if not settings.bookmaker_allowed(o.bookmaker):
                continue
            if o.selection not in best or o.odds_value > best[o.selection].odds_value:
                best[o.selection] = o

        for selection, o in best.items():
            model_prob = probabilities.get(f"{market}_{selection}")
            if model_prob is None:
                continue
            odds = float(o.odds_value)
            bet = evaluate_bet(model_prob, odds, settings.kelly_fraction_cap)
            if weight_for_market is None:
                pick_prob, pick = model_prob, bet
            else:
                reference = reference_probabilities(market, by_bookmaker, o.bookmaker, odds_for_market)
                pick_prob = blended_probability(reference.get(selection), model_prob, weight_for_market(market))
                pick = evaluate_bet(pick_prob, odds, settings.kelly_fraction_cap) if pick_prob is not None else None

            evaluations.append(MarketEvaluation(
                match_id=match.id,
                model_version=model_version,
                market=market,
                selection=selection,
                model_probability=model_prob,
                market_fair_probability=fair.get(selection),
                n_bookmakers=n_bookmakers,
                best_odds=odds,
                best_bookmaker=o.bookmaker,
                expected_value=bet.expected_value,
                validation=validation_label(evidence.skill, settings),
                evaluated_at=now,
            ))

            if pick is None or not is_safe_bet_candidate(
                odds=odds,
                expected_val=pick.expected_value,
                historical_skill=evidence.skill,
                odds_min=settings.odds_min,
                odds_max=settings.odds_max,
                min_ev=settings.min_pick_ev,
                min_historical_skill=settings.min_historical_skill,
                max_ev=settings.max_plausible_ev,
                require_validation=require_validation,
            ):
                continue

            candidates.append(Prediction(
                match_id=match.id,
                model_version=model_version,
                market=market,
                selection=selection,
                model_probability=pick_prob,     # la que decidió el pick (mezcla con la casa)
                bookmaker=o.bookmaker,
                bookmaker_odds=odds,
                implied_probability=pick.implied_probability,
                expected_value=pick.expected_value,
                kelly_fraction=pick.kelly_fraction,
                historical_accuracy=evidence.accuracy,
                historical_skill=evidence.skill,
                backtest_n_bets=evidence.n_bets,
                is_pick_of_day=False,
                generated_at=now,
            ))

    return MatchEvaluation(evaluations, candidates)
