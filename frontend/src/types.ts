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
}

/** Por debajo de esto, los porcentajes son ruido estadístico, no evidencia. */
export const MIN_PICKS_FOR_SIGNIFICANCE = 30

export const MARKET_LABELS: Record<string, string> = {
  '1X2': 'Ganador del partido',
  DOUBLE_CHANCE: 'Doble oportunidad',
  'OU_1.5': 'Más/menos de 1.5 goles',
  'OU_2.5': 'Más/menos de 2.5 goles',
  BTTS: 'Ambos marcan',
  'CORNERS_OU_8.5': 'Más/menos de 8.5 córners',
  'CORNERS_OU_9.5': 'Más/menos de 9.5 córners',
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
