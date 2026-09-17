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
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
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

function withConfidenceParam(path: string, minConfidence?: ConfidenceLevel): string {
  return minConfidence ? `${path}?min_confidence=${minConfidence}` : path
}

export function getTopPicks(minConfidence?: ConfidenceLevel): Promise<PickOfTheDay[]> {
  return getJson<PickOfTheDay[]>(withConfidenceParam('/api/predictions/top-picks', minConfidence))
}

export function getDailyCandidates(minConfidence?: ConfidenceLevel): Promise<DailyCandidate[]> {
  return getJson<DailyCandidate[]>(withConfidenceParam('/api/predictions/daily-candidates', minConfidence))
}

export function getPerformance(): Promise<Performance> {
  return getJson<Performance>('/api/predictions/performance')
}

export function getSimulation(matchId: number): Promise<Simulation> {
  return getJson<Simulation>(`/api/predictions/${matchId}/simulation`)
}

export function getTodaysMatches(): Promise<MatchSummary[]> {
  return getJson<MatchSummary[]>('/api/predictions/matches/today')
}

export function getMatchMarkets(matchId: number): Promise<MatchMarkets> {
  return getJson<MatchMarkets>(`/api/predictions/${matchId}/markets`)
}
