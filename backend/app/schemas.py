from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PickOfTheDayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    match_id: int
    home_team: str
    away_team: str
    league: str
    kickoff_utc: datetime
    market: str
    selection: str
    bookmaker: str | None
    bookmaker_odds: float
    model_probability: float
    implied_probability: float
    expected_value: float
    kelly_fraction: float | None
    historical_accuracy: float | None
    confidence_label: str  # 'ALTA' | 'MEDIA' | 'BAJA' derivado de EV + accuracy


class DailyCandidateResponse(PickOfTheDayResponse):
    is_pick_of_day: bool


class PerformanceResponse(BaseModel):
    """Desempeño REAL de los picks ya liquidados (no el backtest simulado)."""

    settled_picks: int
    wins: int
    losses: int
    hit_rate: float | None
    profit_loss_units: float
    yield_pct: float | None
    pending_picks: int
