import type { ConfidenceLevel, DailyCandidate, Performance, PickOfTheDay } from './types'

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
