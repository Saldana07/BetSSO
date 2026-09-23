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
    # Cuota mínima a la que el pick sigue teniendo el EV mínimo exigido, en
    # CUALQUIER casa (Rushbet no está en la fuente de cuotas: el usuario
    # compara esta cifra con lo que le pagan allí).
    min_odds: float | None = None
    # Resultado, si el partido ya se jugó y el pick se liquidó (vista "Ayer").
    is_settled: bool = False
    is_win: bool | None = None
    profit_loss: float | None = None
    final_score: str | None = None      # "2-1"
    actual_stat: int | None = None      # total real de córners/tiros/tarjetas/faltas del mercado
    closing_odds: float | None = None


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


class MatchSummary(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    league: str
    kickoff_utc: datetime
    n_markets: int          # mercados con cuota disponibles para analizar
    has_pick: bool          # ¿alguno pasó el filtro estricto de picks del día?
    status: str = "SCHEDULED"
    final_score: str | None = None



class MarketRow(BaseModel):
    """Un mercado/selección de un partido con la cuota real y la evaluación del modelo."""

    market: str
    selection: str
    bookmaker: str
    odds: float
    model_probability: float
    implied_probability: float
    expected_value: float
    # Evidencia de habilidad del modelo en ese mercado (backtest real).
    historical_skill: float | None
    backtest_n_bets: int | None
    validation: str        # 'VALIDADO' | 'SIN_VENTAJA' | 'SIN_VALIDAR'
    in_odds_range: bool    # dentro de 1.50-2.00
    ev_suspicious: bool    # EV por encima del techo plausible: casi siempre error del modelo
    is_pick: bool          # pasó el filtro estricto de picks del día


class MatchMarketsResponse(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    league: str
    kickoff_utc: datetime
    home_history: int
    away_history: int
    rows: list[MarketRow]  # ordenados por EV descendente; el primero es "lo más favorable"


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


class ModelVsMarketFamily(BaseModel):
    """Modelo contra la casa en una familia de mercados, sobre lo ya liquidado."""

    family: str                 # CORNERS | SHOTS | SOT | CARDS | FOULS | 1X2 | GOALS_OU | BTTS | AH
    n_matches: int              # unidad independiente (las líneas de un partido van juntas)
    n_markets: int
    brier_model: float
    brier_market: float         # probabilidad de las casas sin margen
    t_stat: float | None
    verdict: str                # MODELO | CASA | EMPATE | POCA_MUESTRA
    # "Si hubiera apostado todo lo que el sistema consideraba valor" (EV >= piso,
    # cuota en rango, mercado validado, EV plausible), a la mejor cuota disponible.
    value_bets: int
    value_bets_won: int
    value_bets_pnl: float


class ModelVsMarketResponse(BaseModel):
    evaluated: int              # mercados evaluados guardados (liquidados o no)
    settled: int
    families: list[ModelVsMarketFamily]
