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
    # Habilidad demostrada del modelo en este mercado = accuracy - tasa base.
    # Es lo que determina la confianza, porque es comparable entre mercados
    # (una accuracy cruda no lo es). None si el mercado no tiene backtest.
    historical_skill: float | None
    backtest_n_bets: int | None
    confidence_label: str  # 'ALTA' | 'MEDIA' | 'BAJA', ver ml/value_betting/confidence.py


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
