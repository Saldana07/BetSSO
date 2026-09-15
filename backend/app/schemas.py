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


class OutcomeProbs(BaseModel):
    HOME: float
    DRAW: float
    AWAY: float


class ValueCandidate(BaseModel):
    outcome: str
    market_fair: float
    monte_carlo: float
    classifier: float
    edge_min: float


class SimulationResponse(BaseModel):
    """Resultado de 10,000 simulaciones de Montecarlo con incertidumbre sobre
    la fuerza de los equipos, y su comparación con el clasificador y el mercado."""

    match_id: int
    home_team: str
    away_team: str
    n_simulations: int
    home_history: int
    away_history: int
    expected_home_goals: float
    expected_away_goals: float
    monte_carlo: OutcomeProbs
    # Intervalos al 90%: reflejan cuánto podría moverse cada % por lo poco o
    # mucho que sabemos de los equipos. Se estrechan con más historial.
    ci_home: tuple[float, float]
    ci_draw: tuple[float, float]
    ci_away: tuple[float, float]
    total_goals_distribution: dict[int, float]
    top_scorelines: list[tuple[str, float]]
    p_over_2_5: float
    p_btts: float
    classifier: OutcomeProbs
    market_fair: OutcomeProbs | None
    model_disagreement: float
    value_candidates: list[ValueCandidate]
    conclusion: str


class PerformanceResponse(BaseModel):
    """Desempeño REAL de los picks ya liquidados (no el backtest simulado)."""

    settled_picks: int
    wins: int
    losses: int
    hit_rate: float | None
    profit_loss_units: float
    yield_pct: float | None
    pending_picks: int
    # Closing Line Value: la medida profesional de ventaja. Converge mucho
    # antes que el P&L porque no depende de acertar el partido, solo de haber
    # tomado un precio mejor que el de cierre. Medido contra la misma casa.
    clv_picks: int
    avg_clv_pct: float | None
    positive_clv_rate: float | None
