from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


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
    # Líneas de jugadores de la NBA: a quién se refiere el pick.
    player: str | None = None


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


class MatchAnalysisSummary(BaseModel):
    market: str
    selection: str
    bookmaker: str | None = None
    odds: float
    model_probability: float
    implied_probability: float
    expected_value: float
    validation: str = "SIN_VALIDAR"
    is_recommended: bool = False
    confidence_label: str = "BAJA"
    is_settled: bool = False
    is_win: bool | None = None
    profit_loss: float | None = None
    player: str | None = None


class MatchSummary(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    league: str
    kickoff_utc: datetime
    n_markets: int          # mercados con cuota disponibles para analizar
    # Mercados que el modelo analizó de verdad (evaluaciones). 0 = sin
    # analizar: sus cuotas son viejas (más de max_odds_age_hours) o no hubo
    # con qué. None donde no aplica (NBA).
    n_evaluated: int | None = None
    has_pick: bool          # ¿alguno pasó el filtro estricto de picks del día?
    status: str = "SCHEDULED"
    final_score: str | None = None
    top_analysis: MatchAnalysisSummary | None = None
    mercadoFavorable: MatchAnalysisSummary | None = None
    mercado_favorable: MatchAnalysisSummary | None = None

    @model_validator(mode="after")
    def sync_mercado_favorable(self) -> "MatchSummary":
        fav = self.top_analysis or self.mercadoFavorable or self.mercado_favorable
        if fav:
            self.top_analysis = fav
            self.mercadoFavorable = fav
            self.mercado_favorable = fav
        return self



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
    player: str | None = None   # líneas de jugadores de la NBA


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
    # Desglose de los picks marcados como recomendados:
    recommended_settled: int | None = None
    recommended_wins: int | None = None
    recommended_losses: int | None = None
    recommended_hit_rate: float | None = None
    recommended_profit_loss: float | None = None
    recommended_yield_pct: float | None = None
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
    # Peso del modelo sobre la casa con el que se deciden los picks de esta
    # familia (ml/value_betting/blend.py); None donde no aplica (NBA).
    weight: float | None = None
    weight_source: str | None = None    # propio | conjunto | casa


class ModelWeight(BaseModel):
    value: float                # peso conjunto recortado a [0, 1]
    raw: float                  # estimación sin recortar
    t_stat: float | None
    n_matches: int
    borrowed: bool              # sin muestra propia: usa el de Europa
    model_only: bool = False    # sección de prueba: los picks siguen al modelo tal cual


class ModelVsMarketResponse(BaseModel):
    evaluated: int              # mercados evaluados guardados (liquidados o no)
    settled: int
    families: list[ModelVsMarketFamily]
    model_weight: ModelWeight | None = None


class ColombiaStatus(BaseModel):
    """Cuánto historial colombiano hay: el modelo propio de Colombia solo
    predice córners y tarjetas de equipos con 10+ partidos con estadísticas."""

    matches: int                    # partidos terminados de Primera A y B en la BD
    matches_with_stats: int
    teams_ready: int                # equipos con 10+ partidos con estadísticas
    teams_total: int
    import_requests_left: int       # peticiones pendientes para completar el historial


class NbaStatus(BaseModel):
    """Apartado NBA: datos disponibles, calendario y validación del modelo."""

    games: int                              # partidos jugados en la BD (sin pretemporada)
    first_season: str | None
    last_season: str | None
    games_with_closing_odds: int            # con líneas de cierre de ESPN (la vara de la validación)
    next_game_utc: datetime | None
    next_game: str | None
    next_game_preseason: bool = False
    regular_season_start_utc: datetime | None
    regular_season_started: bool = False
    validation: dict | None                 # models/nba/validation.json (ver ml/nba/train.py)
    props_validation: dict | None = None    # models/nba/props.json (ver ml/nba/props.py)
    props_coverage: dict | None = None      # líneas de jugadores históricas guardadas
