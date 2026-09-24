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
  /** Líneas de jugadores de la NBA: a quién se refiere el pick. */
  player?: string | null
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
  /** Líneas de jugadores de la NBA. */
  player?: string | null
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
  ML: 'Ganador (incluye prórroga)',
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
  /** Peso del modelo sobre la casa en los picks de esta familia (fútbol). */
  weight?: number | null
  weight_source?: 'propio' | 'conjunto' | 'casa' | null
}

export interface ModelWeight {
  value: number
  raw: number
  t_stat: number | null
  n_matches: number
  borrowed: boolean
}

export interface ModelVsMarket {
  evaluated: number
  settled: number
  families: ModelVsMarketFamily[]
  model_weight?: ModelWeight | null
}

/** Apartado de la app: Europa (modelo validado) o fútbol colombiano (contador propio). */
export type Scope = 'europa' | 'colombia' | 'nba' | 'nba_players'

/** Validación del modelo de jugadores (models/nba/props.json). */
export interface NbaPropsMarketTest {
  n: number
  games?: number
  by_stat?: Record<string, { n: number; brier_model: number; brier_market: number; t: number | null }>
  blend?: { w_first_half: number; test_brier_blend: number | null; test_brier_market: number | null }
  betting_model?: { bets: number; roi: number | null; wins: number }
}

export interface NbaPropsValidation {
  w_live: number
  trained_at: string | null
  test_seasons: string[] | null
  outcomes: Record<string, { n: number; mae_model: number; mae_last10: number; brier: number }> | null
  market: Partial<Record<'CLOSE' | 'OPEN', NbaPropsMarketTest>> | null
}

/** Apartado NBA: datos, calendario y la validación del modelo contra las líneas de cierre. */
export interface NbaValidationPart {
  mae_model: number
  mae_market: number
  w: number
  t: number | null
  test_rmse_market: number
  test_rmse_blend: number
  used_weight: number
}

export interface NbaBetSim {
  bets: number
  wins: number
  profit: number
  roi: number | null
}

export interface NbaValidation {
  trained_at: string
  fit_seasons: string[]
  test_seasons: string[]
  n_fit: number
  n_test: number
  margin: NbaValidationPart
  total: NbaValidationPart
  ml: { n: number; brier_model: number; brier_market: number; t: number | null }
  betting: Record<'close' | 'open', { modelo_solo: NbaBetSim }>
  /** La regla en vivo con w = 0 (comprar el precio de una casa que se aparta
   *  de las demás), probada donde ESPN tiene 3+ casas. No fiable: ver `reason`. */
  price_shopping: {
    n_games: number
    seasons: string[]
    near: NbaBetSim
    far: NbaBetSim
    trusted: boolean
    reason: string
  }
  weights_live: { margin: number; total: number }
}

export interface NbaStatus {
  games: number
  first_season: string | null
  last_season: string | null
  games_with_closing_odds: number
  next_game_utc: string | null
  next_game: string | null
  next_game_preseason: boolean
  regular_season_start_utc: string | null
  regular_season_started: boolean
  validation: NbaValidation | null
  props_validation: NbaPropsValidation | null
  props_coverage: { lines: number; games: number; since: string | null } | null
}

export interface ColombiaStatus {
  matches: number
  matches_with_stats: number
  teams_ready: number
  teams_total: number
  import_requests_left: number
}

/** Botones que gastan cuota de API-Football (nada corre solo). */
export type ActionName = 'actualizar' | 'cuotas' | 'verificar' | 'completar' | 'nba' | 'nba_cuotas'

export interface ActionRun {
  action: ActionName
  title: string
  started_at: string
  step: string
  step_index: number
  steps: number
}

export interface ActionResult {
  finished_at: string
  ok: boolean
  /** Consultas gastadas (null si la acción cruzó la renovación de la cuota). */
  spent: number | null
  steps: { label: string; ok: boolean; summary: string }[]
}

export interface ActionStatus {
  quota: { used: number; limit: number; breakdown: Record<string, number>; resets_at: string }
  /** API de baloncesto (NBA): misma clave, cuota aparte. */
  quota_basketball: { used: number; limit: number; breakdown: Record<string, number> }
  running: ActionRun | null
  last: Partial<Record<ActionName, ActionResult>>
  /** Primera visita de la mañana (desde `auto_update_hour`): toca actualizar el día. */
  update_due: boolean
  /** Hora local desde la que la página actualiza el día sola (6 = 6:00 a. m.). */
  auto_update_hour: number
  /** La actualización NBA (gratis) toca: la última fue hace más de 3 horas. */
  nba_update_due: boolean
  titles: Record<ActionName, string>
}

export interface ActionEstimates {
  nba: { requests: number; games: number }
  nba_cuotas: { requests: number; games: number }
  actualizar: { requests: number; approximate: boolean }
  cuotas: { requests: number }
  verificar: { requests: number; matches: number }
  completar: { requests: number; measurement_matches: number }
}
