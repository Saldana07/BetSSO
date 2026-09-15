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
    bookmaker_odds: float
    model_probability: float
    implied_probability: float
    expected_value: float
    kelly_fraction: float | None
    historical_accuracy: float | None
    confidence_label: str  # 'ALTA' | 'MEDIA' | 'BAJA' derivado de EV + accuracy


class DailyCandidateResponse(PickOfTheDayResponse):
    is_pick_of_day: bool
