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

/** El apartado NBA tiene su propia API (/api/nba) con las mismas respuestas;
 *  fútbol usa /api/predictions con scope=europa|colombia. */
function route(path: string, scope: Scope): [string, string | undefined] {
  return scope === 'nba' ? [`/api/nba${path}`, undefined] : [`/api/predictions${path}`, scope]
}

export function getTopPicks(minConfidence?: ConfidenceLevel, day?: string, scope: Scope = 'europa'): Promise<PickOfTheDay[]> {
  const [path, s] = route('/top-picks', scope)
  return getJson<PickOfTheDay[]>(withParams(path, { min_confidence: minConfidence, day, tz: LOCAL_TZ, scope: s }))
}

export function getDailyCandidates(minConfidence?: ConfidenceLevel, day?: string, scope: Scope = 'europa'): Promise<DailyCandidate[]> {
  const [path, s] = route('/daily-candidates', scope)
  return getJson<DailyCandidate[]>(withParams(path, { min_confidence: minConfidence, day, tz: LOCAL_TZ, scope: s }))
}

export function getPerformance(scope: Scope = 'europa'): Promise<Performance> {
  const [path, s] = route('/performance', scope)
  return getJson<Performance>(withParams(path, { scope: s }))
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
  const [path, s] = scope === 'nba' ? ['/api/nba/games/today', undefined] : route('/matches/today', scope)
  return getJson<MatchSummary[]>(withParams(path, { day, tz: LOCAL_TZ, scope: s }))
}

export function getMatchMarkets(matchId: number, scope: Scope = 'europa'): Promise<MatchMarkets> {
  return getJson<MatchMarkets>(scope === 'nba' ? `/api/nba/games/${matchId}/markets` : `/api/predictions/${matchId}/markets`)
}

export function getModelVsMarket(scope: Scope = 'europa'): Promise<ModelVsMarket> {
  const [path, s] = route('/model-vs-market', scope)
  return getJson<ModelVsMarket>(withParams(path, { scope: s }))
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
