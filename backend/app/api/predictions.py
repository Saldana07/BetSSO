from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.database import get_db
from app.models import Match, Prediction
from app.models import Odds
from app.schemas import (
    DailyCandidateResponse,
    OutcomeProbs,
    PerformanceResponse,
    PickOfTheDayResponse,
    SimulationResponse,
    ValueCandidate,
)
from ml.serving.predict import get_models, match_context
from ml.simulation.monte_carlo import compare_with, simulate_match
from ml.value_betting.confidence import SkillEvidence, confidence_label

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

ConfidenceLevel = Literal["ALTA", "MEDIA", "BAJA"]
_CONFIDENCE_RANK = {"BAJA": 0, "MEDIA": 1, "ALTA": 2}


def _to_response(pred: Prediction, cls=PickOfTheDayResponse):
    match = pred.match
    return cls(
        match_id=match.id,
        home_team=match.home_team.name,
        away_team=match.away_team.name,
        league=match.league.name,
        kickoff_utc=match.kickoff_utc,
        market=pred.market,
        selection=pred.selection,
        bookmaker=pred.bookmaker,
        bookmaker_odds=float(pred.bookmaker_odds),
        model_probability=float(pred.model_probability),
        implied_probability=float(pred.implied_probability),
        expected_value=float(pred.expected_value),
        kelly_fraction=float(pred.kelly_fraction) if pred.kelly_fraction is not None else None,
        historical_accuracy=float(pred.historical_accuracy) if pred.historical_accuracy is not None else None,
        historical_skill=float(pred.historical_skill) if pred.historical_skill is not None else None,
        backtest_n_bets=pred.backtest_n_bets,
        confidence_label=confidence_label(
            float(pred.expected_value),
            # Se reconstruye la evidencia desde la foto guardada en el pick:
            # skill = accuracy - base, así que base = accuracy - skill.
            SkillEvidence(
                accuracy=float(pred.historical_accuracy) if pred.historical_accuracy is not None else None,
                base_accuracy=(
                    float(pred.historical_accuracy) - float(pred.historical_skill)
                    if pred.historical_accuracy is not None and pred.historical_skill is not None
                    else None
                ),
                n_bets=pred.backtest_n_bets,
            ),
        ),
        is_pick_of_day=pred.is_pick_of_day,
    )


@router.get("/top-picks", response_model=list[PickOfTheDayResponse])
def get_top_picks(
    min_confidence: ConfidenceLevel | None = Query(
        default=None, description="Filtra dejando solo picks con esta confianza o mayor (BAJA < MEDIA < ALTA)."
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Devuelve el top N de 'apuestas seguras' del día (mayor EV dentro del
    rango de cuota [odds_min, odds_max], como máximo un pick por partido),
    ordenadas por EV descendente.

    El job diario (jobs/generate_daily_pick.py) es responsable de poblar
    `predictions` con `is_pick_of_day=True` para el top N; este endpoint
    solo lee ese resultado precomputado (lectura rápida, sin recalcular el
    modelo en el request).
    """
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    stmt = (
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Prediction.is_pick_of_day.is_(True),
            Match.kickoff_utc >= today_start,
            Match.kickoff_utc < today_end,
        )
        .order_by(Prediction.expected_value.desc())
    )
    picks = db.execute(stmt).scalars().all()
    responses = [_to_response(p) for p in picks]

    if min_confidence is not None:
        min_rank = _CONFIDENCE_RANK[min_confidence]
        responses = [r for r in responses if _CONFIDENCE_RANK[r.confidence_label] >= min_rank]

    if not responses:
        detail = (
            f"No hay picks con confianza {min_confidence} o mayor hoy."
            if min_confidence
            else "No hay ninguna apuesta segura disponible hoy que cumpla los criterios (cuota 1.50-2.00, +EV)."
        )
        raise HTTPException(status_code=404, detail=detail)

    return responses


def _best_1x2_odds(db: Session, match_id: int, settings: Settings) -> dict[str, float] | None:
    """Mejor cuota por selección del snapshot 1X2 más reciente (y no vencido)."""
    latest = db.execute(
        select(func.max(Odds.recorded_at)).where(
            Odds.match_id == match_id, Odds.market == "1X2", Odds.snapshot_type == "LIVE"
        )
    ).scalar_one_or_none()
    if latest is None:
        return None
    if (datetime.now(timezone.utc) - latest).total_seconds() / 3600 > settings.max_odds_age_hours:
        return None

    rows = db.execute(
        select(Odds.selection, func.max(Odds.odds_value))
        .where(Odds.match_id == match_id, Odds.market == "1X2", Odds.recorded_at == latest)
        .group_by(Odds.selection)
    ).all()
    odds = {selection: float(value) for selection, value in rows}
    return odds if {"HOME", "DRAW", "AWAY"} <= odds.keys() else None


@router.get("/{match_id}/simulation", response_model=SimulationResponse)
def get_simulation(
    match_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """10,000 simulaciones de Montecarlo del partido con incertidumbre sobre la
    fuerza de los equipos, comparadas con el clasificador y con el mercado.

    La semilla es el match_id: el resultado es reproducible entre recargas
    (si no, los % bailarían ±0.5% cada vez y parecería que algo cambió).
    """
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado.")

    ctx = match_context(db, match, get_models(model_version=settings.model_version))
    sim = simulate_match(
        ctx.lambda_home, ctx.lambda_away, ctx.home_history, ctx.away_history, seed=match_id
    )
    comparison = compare_with(sim, ctx.classifier, _best_1x2_odds(db, match_id, settings))

    return SimulationResponse(
        match_id=match.id,
        home_team=match.home_team.name,
        away_team=match.away_team.name,
        n_simulations=sim.n_simulations,
        home_history=ctx.home_history,
        away_history=ctx.away_history,
        expected_home_goals=sim.expected_home_goals,
        expected_away_goals=sim.expected_away_goals,
        monte_carlo=OutcomeProbs(**comparison.monte_carlo),
        ci_home=sim.ci_home,
        ci_draw=sim.ci_draw,
        ci_away=sim.ci_away,
        total_goals_distribution=sim.total_goals_distribution,
        top_scorelines=sim.top_scorelines,
        p_over_2_5=sim.p_over_2_5,
        p_btts=sim.p_btts,
        classifier=OutcomeProbs(**comparison.classifier),
        market_fair=OutcomeProbs(**comparison.market_fair) if comparison.market_fair else None,
        model_disagreement=comparison.model_disagreement,
        value_candidates=[ValueCandidate(**c) for c in comparison.value_candidates],
        conclusion=comparison.conclusion,
    )


@router.get("/performance", response_model=PerformanceResponse)
def get_performance(db: Session = Depends(get_db)):
    """Desempeño REAL de los picks recomendados que ya se liquidaron.

    A diferencia del backtest (que simula el pasado), esto mide lo que de
    verdad pasó con los picks que el sistema emitió. Es la única métrica que
    responde "¿esto gana dinero?". La llena jobs/settle_predictions.py.
    """
    settled = db.execute(
        select(
            func.count(Prediction.id),
            func.count(Prediction.id).filter(Prediction.is_win.is_(True)),
            func.coalesce(func.sum(Prediction.profit_loss), 0),
        ).where(Prediction.is_pick_of_day.is_(True), Prediction.settled_at.isnot(None))
    ).one()

    total, wins, pnl = settled[0], settled[1], float(settled[2])

    pending = db.execute(
        select(func.count(Prediction.id)).where(
            Prediction.is_pick_of_day.is_(True), Prediction.settled_at.is_(None)
        )
    ).scalar_one()

    return PerformanceResponse(
        settled_picks=total,
        wins=wins,
        losses=total - wins,
        hit_rate=(wins / total) if total else None,
        profit_loss_units=round(pnl, 3),
        # Yield a stake plano de 1 unidad: P&L total / unidades arriesgadas.
        yield_pct=(pnl / total * 100) if total else None,
        pending_picks=pending,
    )


@router.get("/daily-candidates", response_model=list[DailyCandidateResponse])
def get_daily_candidates(
    min_confidence: ConfidenceLevel | None = Query(
        default=None, description="Filtra dejando solo candidatos con esta confianza o mayor (BAJA < MEDIA < ALTA)."
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Lista TODOS los candidatos del día que pasaron el filtro de cuota/EV
    (incluye los del top-picks), ordenados por EV descendente — útil para
    el frontend mostrar el resto de opciones evaluadas."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    stmt = (
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Prediction.bookmaker_odds >= settings.odds_min,
            Prediction.bookmaker_odds <= settings.odds_max,
            Prediction.expected_value >= settings.min_pick_ev,
            Match.kickoff_utc >= today_start,
            Match.kickoff_utc < today_end,
        )
        .order_by(Prediction.expected_value.desc())
    )
    picks = db.execute(stmt).scalars().all()
    responses = [_to_response(p, cls=DailyCandidateResponse) for p in picks]

    if min_confidence is not None:
        min_rank = _CONFIDENCE_RANK[min_confidence]
        responses = [r for r in responses if _CONFIDENCE_RANK[r.confidence_label] >= min_rank]

    return responses
