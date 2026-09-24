import type {
  ActionEstimates,
  ActionName,
  ActionStatus,
  ConfidenceLevel,
  DailyCandidate,
  MatchMarkets,
  ColombiaStatus,
  MatchSummary,
  ModelVsMarket,
  NbaStatus,
  Performance,
  PickOfTheDay,
  Scope,
  Simulation,
} from './types'

class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(body.detail ?? `Error ${res.status}`, res.status)
  }
  return res.json() as Promise<T>
}

export { ApiError }

/** Zona horaria del navegador (IANA, ej. America/Bogota). La API la usa para
 *  que "hoy" sea el hoy del usuario y no el de UTC: a las 9 pm en Colombia ya
 *  es "mañana" en UTC y la app mostraba vacío. */
export const LOCAL_TZ: string = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

/** Fecha LOCAL de hoy + offset días, como YYYY-MM-DD. */
export function localDay(offsetDays: number): string {
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function withParams(path: string, params: Record<string, string | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
  const qs = q.toString()
  return qs ? `${path}?${qs}` : path
}

/** La NBA tiene su propia API (/api/nba) con las mismas respuestas (kind=player
 *  para las líneas de jugadores); fútbol usa /api/predictions con scope. */
function route(path: string, scope: Scope): [string, Record<string, string | undefined>] {
  if (scope === 'nba') return [`/api/nba${path}`, {}]
  if (scope === 'nba_players') return [`/api/nba${path}`, { kind: 'player' }]
  return [`/api/predictions${path}`, { scope }]
}

export function getTopPicks(minConfidence?: ConfidenceLevel, day?: string, scope: Scope = 'europa'): Promise<PickOfTheDay[]> {
  const [path, extra] = route('/top-picks', scope)
  return getJson<PickOfTheDay[]>(withParams(path, { ...extra, min_confidence: minConfidence, day, tz: LOCAL_TZ }))
}

export function getDailyCandidates(minConfidence?: ConfidenceLevel, day?: string, scope: Scope = 'europa'): Promise<DailyCandidate[]> {
  const [path, extra] = route('/daily-candidates', scope)
  return getJson<DailyCandidate[]>(withParams(path, { ...extra, min_confidence: minConfidence, day, tz: LOCAL_TZ }))
}

export function getPerformance(scope: Scope = 'europa'): Promise<Performance> {
  const [path, extra] = route('/performance', scope)
  return getJson<Performance>(withParams(path, extra))
}

export function getNbaStatus(): Promise<NbaStatus> {
  return getJson<NbaStatus>('/api/nba/status')
}

export interface NbaInjury {
  player: string
  status: string
  out: boolean
  comment: string
  date: string | null
}

export interface NbaInjuries {
  updated_at: string | null
  home_team: string
  away_team: string
  home: NbaInjury[]
  away: NbaInjury[]
}

export function getNbaInjuries(matchId: number): Promise<NbaInjuries> {
  return getJson<NbaInjuries>(`/api/nba/games/${matchId}/injuries`)
}

export function getColombiaStatus(): Promise<ColombiaStatus> {
  return getJson<ColombiaStatus>('/api/predictions/colombia/status')
}

export function getSimulation(matchId: number): Promise<Simulation> {
  return getJson<Simulation>(`/api/predictions/${matchId}/simulation`)
}

export function getTodaysMatches(day?: string, scope: Scope = 'europa'): Promise<MatchSummary[]> {
  const [path, extra] = scope === 'nba' || scope === 'nba_players' ? route('/games/today', scope) : route('/matches/today', scope)
  return getJson<MatchSummary[]>(withParams(path, { ...extra, day, tz: LOCAL_TZ }))
}

export function getMatchMarkets(matchId: number, scope: Scope = 'europa'): Promise<MatchMarkets> {
  if (scope === 'nba' || scope === 'nba_players') {
    const [path, extra] = route(`/games/${matchId}/markets`, scope)
    return getJson<MatchMarkets>(withParams(path, extra))
  }
  return getJson<MatchMarkets>(`/api/predictions/${matchId}/markets`)
}

export function getModelVsMarket(scope: Scope = 'europa'): Promise<ModelVsMarket> {
  const [path, extra] = route('/model-vs-market', scope)
  return getJson<ModelVsMarket>(withParams(path, extra))
}

export function getActionStatus(): Promise<ActionStatus> {
  return getJson<ActionStatus>('/api/actions/status')
}

export function getActionEstimates(): Promise<ActionEstimates> {
  return getJson<ActionEstimates>('/api/actions/estimates')
}

/** Lanza una acción que gasta cuota de API; responde al instante y la acción
 *  sigue en segundo plano (su progreso, en getActionStatus). */
export async function launchAction(action: ActionName, auto = false): Promise<{ started: boolean; reason?: string }> {
  const res = await fetch(withParams(`/api/actions/${action}`, { auto: auto ? 'true' : undefined }), { method: 'POST' })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(body.detail ?? `Error ${res.status}`, res.status)
  return body
}
