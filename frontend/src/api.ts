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

export function getTopPicks(minConfidence?: ConfidenceLevel, day?: string, scope: Scope = 'europa'): Promise<PickOfTheDay[]> {
  return getJson<PickOfTheDay[]>(withParams('/api/predictions/top-picks', { min_confidence: minConfidence, day, tz: LOCAL_TZ, scope }))
}

export function getDailyCandidates(minConfidence?: ConfidenceLevel, day?: string, scope: Scope = 'europa'): Promise<DailyCandidate[]> {
  return getJson<DailyCandidate[]>(withParams('/api/predictions/daily-candidates', { min_confidence: minConfidence, day, tz: LOCAL_TZ, scope }))
}

export function getPerformance(scope: Scope = 'europa'): Promise<Performance> {
  return getJson<Performance>(withParams('/api/predictions/performance', { scope }))
}

export function getColombiaStatus(): Promise<ColombiaStatus> {
  return getJson<ColombiaStatus>('/api/predictions/colombia/status')
}

export function getSimulation(matchId: number): Promise<Simulation> {
  return getJson<Simulation>(`/api/predictions/${matchId}/simulation`)
}

export function getTodaysMatches(day?: string, scope: Scope = 'europa'): Promise<MatchSummary[]> {
  return getJson<MatchSummary[]>(withParams('/api/predictions/matches/today', { day, tz: LOCAL_TZ, scope }))
}

export function getMatchMarkets(matchId: number): Promise<MatchMarkets> {
  return getJson<MatchMarkets>(`/api/predictions/${matchId}/markets`)
}

export function getModelVsMarket(scope: Scope = 'europa'): Promise<ModelVsMarket> {
  return getJson<ModelVsMarket>(withParams('/api/predictions/model-vs-market', { scope }))
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
