import { useEffect, useState } from 'react'
import { ApiError, getDailyCandidates, getTopPicks } from './api'
import { CandidateRow } from './components/CandidateRow'
import { ConfidenceFilter } from './components/ConfidenceFilter'
import { PickOfTheDayCard } from './components/PickOfTheDayCard'
import type { ConfidenceLevel, DailyCandidate, PickOfTheDay } from './types'

type PicksState =
  | { status: 'loading' }
  | { status: 'empty' }
  | { status: 'error'; message: string }
  | { status: 'ready'; picks: PickOfTheDay[] }

function App() {
  const [minConfidence, setMinConfidence] = useState<ConfidenceLevel | null>(null)
  const [picksState, setPicksState] = useState<PicksState>({ status: 'loading' })
  const [candidates, setCandidates] = useState<DailyCandidate[]>([])
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    setPicksState({ status: 'loading' })

    getTopPicks(minConfidence ?? undefined)
      .then((picks) => setPicksState({ status: 'ready', picks }))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setPicksState({ status: 'empty' })
        } else {
          setPicksState({ status: 'error', message: err.message ?? 'No se pudo conectar con la API' })
        }
      })

    getDailyCandidates(minConfidence ?? undefined)
      .then(setCandidates)
      .catch(() => setCandidates([]))
  }, [refreshKey, minConfidence])

  const topPickKeys = new Set(
    picksState.status === 'ready' ? picksState.picks.map((p) => `${p.match_id}-${p.market}-${p.selection}`) : [],
  )
  const otherCandidates = candidates.filter((c) => !topPickKeys.has(`${c.match_id}-${c.market}-${c.selection}`))

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-4">
          <div>
            <h1 className="text-lg font-bold text-slate-900 dark:text-slate-100">⚽ Apuesta Segura</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Motor de predicciones de fútbol · cuota 1.50–2.00 · +EV
            </p>
          </div>
          <button
            onClick={() => setRefreshKey((k) => k + 1)}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            Actualizar
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-4 py-8">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Picks del día
          </h2>
          <ConfidenceFilter value={minConfidence} onChange={setMinConfidence} />
        </div>

        {picksState.status === 'loading' && (
          <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
            Cargando picks del día...
          </div>
        )}

        {picksState.status === 'error' && (
          <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
            <p className="font-semibold">No se pudo conectar con la API</p>
            <p className="mt-1 text-sm">{picksState.message}</p>
            <p className="mt-2 text-sm">
              Verifica que el backend esté corriendo en <code>http://127.0.0.1:8000</code>.
            </p>
          </div>
        )}

        {picksState.status === 'empty' && (
          <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center dark:border-slate-700 dark:bg-slate-900">
            <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              {minConfidence ? `No hay picks con confianza ${minConfidence.toLowerCase()} o mayor hoy` : 'No hay apuesta segura hoy'}
            </p>
            <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
              {minConfidence
                ? 'Prueba bajando el filtro de confianza, o vuelve más tarde cuando haya más mercados con backtest validado.'
                : 'Ningún partido cumplió el filtro de cuota 1.50–2.00 con +EV. Corre jobs/generate_daily_pick.py si aún no lo hiciste hoy.'}
            </p>
          </div>
        )}

        {picksState.status === 'ready' && (
          <div className="space-y-4">
            {picksState.picks.map((pick, i) => (
              <PickOfTheDayCard key={`${pick.match_id}-${pick.market}-${pick.selection}`} pick={pick} rank={i + 1} />
            ))}
          </div>
        )}

        {otherCandidates.length > 0 && (
          <div className="mt-8">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Otros candidatos evaluados hoy
            </h3>
            <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
              <table className="w-full text-left">
                <thead>
                  <tr className="text-xs uppercase text-slate-400 dark:text-slate-500">
                    <th className="pb-2 font-medium">Partido</th>
                    <th className="pb-2 font-medium">Mercado</th>
                    <th className="pb-2 font-medium">Cuota</th>
                    <th className="pb-2 font-medium">EV</th>
                    <th className="pb-2 font-medium">Confianza</th>
                  </tr>
                </thead>
                <tbody>
                  {otherCandidates.map((c) => (
                    <CandidateRow key={`${c.match_id}-${c.market}-${c.selection}`} candidate={c} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

export default App
