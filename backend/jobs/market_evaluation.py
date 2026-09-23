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
from ml.value_betting.ev_kelly import evaluate_bet, is_safe_bet_candidate
from ml.value_betting.market import fair_probabilities


def validation_label(skill: float | None, settings: Settings) -> str:
    if skill is None:
        return "SIN_VALIDAR"
    return "VALIDADO" if skill >= settings.min_historical_skill else "SIN_VENTAJA"


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
) -> MatchEvaluation:
    evaluations: list[MarketEvaluation] = []
    candidates: list[Prediction] = []

    for market in markets:
        odds_rows = odds_for_market(market)
        if not odds_rows:
            continue
        evidence = evidence_for_market(market)

        by_bookmaker: dict[str, dict[str, float]] = {}
        for o in odds_rows:
            by_bookmaker.setdefault(o.bookmaker, {})[o.selection] = float(o.odds_value)
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

            evaluations.append(MarketEvaluation(
                match_id=match.id,
                model_version=settings.model_version,
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

            if not is_safe_bet_candidate(
                odds=odds,
                expected_val=bet.expected_value,
                historical_skill=evidence.skill,
                odds_min=settings.odds_min,
                odds_max=settings.odds_max,
                min_ev=settings.min_pick_ev,
                min_historical_skill=settings.min_historical_skill,
                max_ev=settings.max_plausible_ev,
            ):
                continue

            candidates.append(Prediction(
                match_id=match.id,
                model_version=settings.model_version,
                market=market,
                selection=selection,
                model_probability=model_prob,
                bookmaker=o.bookmaker,
                bookmaker_odds=odds,
                implied_probability=bet.implied_probability,
                expected_value=bet.expected_value,
                kelly_fraction=bet.kelly_fraction,
                historical_accuracy=evidence.accuracy,
                historical_skill=evidence.skill,
                backtest_n_bets=evidence.n_bets,
                is_pick_of_day=False,
                generated_at=now,
            ))

    return MatchEvaluation(evaluations, candidates)
