import type {
  ConfidenceLevel,
  DailyCandidate,
  MatchMarkets,
  MatchSummary,
  Performance,
  PickOfTheDay,
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

/** Día en UTC (la API y la BD trabajan en UTC): hoy + offset días, como YYYY-MM-DD. */
export function utcDay(offsetDays: number): string {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() + offsetDays)
  return d.toISOString().slice(0, 10)
}

function withParams(path: string, params: Record<string, string | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
  const qs = q.toString()
  return qs ? `${path}?${qs}` : path
}

export function getTopPicks(minConfidence?: ConfidenceLevel, day?: string): Promise<PickOfTheDay[]> {
  return getJson<PickOfTheDay[]>(withParams('/api/predictions/top-picks', { min_confidence: minConfidence, day }))
}

export function getDailyCandidates(minConfidence?: ConfidenceLevel, day?: string): Promise<DailyCandidate[]> {
  return getJson<DailyCandidate[]>(withParams('/api/predictions/daily-candidates', { min_confidence: minConfidence, day }))
}

export function getPerformance(): Promise<Performance> {
  return getJson<Performance>('/api/predictions/performance')
}

export function getSimulation(matchId: number): Promise<Simulation> {
  return getJson<Simulation>(`/api/predictions/${matchId}/simulation`)
}

export function getTodaysMatches(day?: string): Promise<MatchSummary[]> {
  return getJson<MatchSummary[]>(withParams('/api/predictions/matches/today', { day }))
}

export function getMatchMarkets(matchId: number): Promise<MatchMarkets> {
  return getJson<MatchMarkets>(`/api/predictions/${matchId}/markets`)
}
