from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.database import get_db
from app.models import Match, Prediction
from app.schemas import DailyCandidateResponse, PickOfTheDayResponse

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

ConfidenceLevel = Literal["ALTA", "MEDIA", "BAJA"]
_CONFIDENCE_RANK = {"BAJA": 0, "MEDIA": 1, "ALTA": 2}


def _confidence_label(expected_value: float, historical_accuracy: float | None) -> str:
    accuracy = historical_accuracy or 0.0
    if expected_value >= 0.15 and accuracy >= 0.60:
        return "ALTA"
    if expected_value >= 0.05 and accuracy >= 0.55:
        return "MEDIA"
    return "BAJA"


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
        bookmaker_odds=float(pred.bookmaker_odds),
        model_probability=float(pred.model_probability),
        implied_probability=float(pred.implied_probability),
        expected_value=float(pred.expected_value),
        kelly_fraction=float(pred.kelly_fraction) if pred.kelly_fraction is not None else None,
        historical_accuracy=float(pred.historical_accuracy) if pred.historical_accuracy is not None else None,
        confidence_label=_confidence_label(float(pred.expected_value), pred.historical_accuracy),
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
            Prediction.expected_value > 0,
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
