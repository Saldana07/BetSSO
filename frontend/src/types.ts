export type ConfidenceLevel = 'ALTA' | 'MEDIA' | 'BAJA'

export interface PickOfTheDay {
  match_id: number
  home_team: string
  away_team: string
  league: string
  kickoff_utc: string
  market: string
  selection: string
  bookmaker: string | null
  bookmaker_odds: number
  model_probability: number
  implied_probability: number
  expected_value: number
  kelly_fraction: number | null
  historical_accuracy: number | null
  /** accuracy − tasa base del backtest: habilidad real del modelo en este mercado. */
  historical_skill: number | null
  backtest_n_bets: number | null
  confidence_label: ConfidenceLevel
  /** Resultado, si el partido ya se jugó y el pick se liquidó (vista "Ayer"). */
  is_settled: boolean
  is_win: boolean | null
  profit_loss: number | null
  final_score: string | null
  /** Total real de la estadística del mercado de conteo (córners, tiros...). */
  actual_stat: number | null
  closing_odds: number | null
  /** Cuota mínima a la que el pick sigue valiendo la pena, en cualquier casa. */
  min_odds: number | null
}

export interface DailyCandidate extends PickOfTheDay {
  is_pick_of_day: boolean
}

export interface Performance {
  settled_picks: number
  wins: number
  losses: number
  hit_rate: number | null
  profit_loss_units: number
  yield_pct: number | null
  pending_picks: number
  /** Closing Line Value: picks con cuota de cierre, CLV medio (%) y % con CLV positivo. */
  clv_picks: number
  avg_clv_pct: number | null
  positive_clv_rate: number | null
}

/** Por debajo de esto, los porcentajes son ruido estadístico, no evidencia. */
export const MIN_PICKS_FOR_SIGNIFICANCE = 30

export interface OutcomeProbs {
  HOME: number
  DRAW: number
  AWAY: number
}

export interface ValueCandidate {
  outcome: 'HOME' | 'DRAW' | 'AWAY'
  market_fair: number
  monte_carlo: number
  classifier: number
  edge_min: number
}

export interface Simulation {
  match_id: number
  home_team: string
  away_team: string
  n_simulations: number
  home_history: number
  away_history: number
  expected_home_goals: number
  expected_away_goals: number
  monte_carlo: OutcomeProbs
  ci_home: [number, number]
  ci_draw: [number, number]
  ci_away: [number, number]
  total_goals_distribution: Record<string, number>
  top_scorelines: [string, number][]
  p_over_2_5: number
  p_btts: number
  classifier: OutcomeProbs
  market_fair: OutcomeProbs | null
  model_disagreement: number
  value_candidates: ValueCandidate[]
  conclusion: string
}

export interface MatchSummary {
  match_id: number
  home_team: string
  away_team: string
  league: string
  kickoff_utc: string
  n_markets: number
  has_pick: boolean
  status: string
  final_score: string | null
}

export type Validation = 'VALIDADO' | 'SIN_VALIDAR' | 'SIN_VENTAJA'

export interface MarketRow {
  market: string
  selection: string
  bookmaker: string
  odds: number
  model_probability: number
  implied_probability: number
  expected_value: number
  historical_skill: number | null
  backtest_n_bets: number | null
  validation: Validation
  in_odds_range: boolean
  ev_suspicious: boolean
  is_pick: boolean
}

export interface MatchMarkets extends Omit<MatchSummary, 'n_markets' | 'has_pick'> {
  home_history: number
  away_history: number
  rows: MarketRow[]
}

export const VALIDATION_LABELS: Record<Validation, { label: string; hint: string }> = {
  VALIDADO: {
    label: 'Validado',
    hint: 'Backtest contra cuotas o resultados reales con habilidad ≥ 0: el modelo al menos no es peor que apostar a ciegas aquí.',
  },
  SIN_VALIDAR: {
    label: 'Sin validar',
    hint: 'No hay backtest para este mercado. El EV es una opinión del modelo sin evidencia de que sepa algo que el mercado no sepa.',
  },
  SIN_VENTAJA: {
    label: 'Sin ventaja',
    hint: 'Backtest con cuotas reales: el modelo pierde contra el mercado en este mercado. Un EV positivo aquí es casi seguro un error del modelo.',
  },
}

export const OUTCOME_LABELS: Record<keyof OutcomeProbs, string> = {
  HOME: 'Victoria local',
  DRAW: 'Empate',
  AWAY: 'Victoria visitante',
}

export const MARKET_LABELS: Record<string, string> = {
  '1X2': 'Ganador del partido',
  DOUBLE_CHANCE: 'Doble oportunidad',
  'OU_1.5': 'Más/menos de 1.5 goles',
  'OU_2.5': 'Más/menos de 2.5 goles',
  BTTS: 'Ambos marcan',
  'CORNERS_OU_8.5': 'Más/menos de 8.5 córners',
  'CORNERS_OU_9.5': 'Más/menos de 9.5 córners',
  EXACT_SCORE: 'Marcador exacto',
}

export const SELECTION_LABELS: Record<string, string> = {
  HOME: 'Local',
  DRAW: 'Empate',
  AWAY: 'Visitante',
  '1X': 'Local o empate',
  X2: 'Empate o visitante',
  '12': 'Local o visitante',
  OVER: 'Más de',
  UNDER: 'Menos de',
  YES: 'Sí',
  NO: 'No',
}

export interface ModelVsMarketFamily {
  family: string
  n_matches: number
  n_markets: number
  brier_model: number
  /** Probabilidad de las casas sin su margen. */
  brier_market: number
  t_stat: number | null
  verdict: 'MODELO' | 'CASA' | 'EMPATE' | 'POCA_MUESTRA'
  value_bets: number
  value_bets_won: number
  value_bets_pnl: number
}

export interface ModelVsMarket {
  evaluated: number
  settled: number
  families: ModelVsMarketFamily[]
}
