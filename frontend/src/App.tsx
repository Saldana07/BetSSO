import { useEffect, useState } from 'react'
import {
  ApiError,
  getColombiaStatus,
  getDailyCandidates,
  getMatchMarkets,
  getModelVsMarket,
  getPerformance,
  getTodaysMatches,
  getTopPicks,
  localDay,
} from './api'
import { CandidateRow } from './components/CandidateRow'
import { ConfidenceFilter } from './components/ConfidenceFilter'
import { DaySelector, dayLabel, type DayOffset } from './components/DaySelector'
import { MatchDetail, type MatchRef } from './components/MatchDetail'
import { ModelVsMarket } from './components/ModelVsMarket'
import { PerformanceBar } from './components/PerformanceBar'
import { PickOfTheDayCard } from './components/PickOfTheDayCard'
import { ColombiaBanner, SectionSelector } from './components/SectionSelector'
import type {
  ColombiaStatus,
  ConfidenceLevel,
  DailyCandidate,
  MatchSummary,
  ModelVsMarket as ModelVsMarketData,
  Performance,
  PickOfTheDay,
  Scope,
} from './types'

type PicksState =
  | { status: 'loading' }
  | { status: 'empty' }
  | { status: 'error'; message: string }
  | { status: 'ready'; picks: PickOfTheDay[] }

type Selected = { match: MatchRef; pick?: PickOfTheDay }

function App() {
  const [scope, setScope] = useState<Scope>(() => (new URLSearchParams(window.location.search).get('seccion') === 'colombia' ? 'colombia' : 'europa'))
  const [colombiaStatus, setColombiaStatus] = useState<ColombiaStatus | null>(null)
  const [minConfidence, setMinConfidence] = useState<ConfidenceLevel | null>(null)
  const [dayOffset, setDayOffset] = useState<DayOffset>(0)
  const [picksState, setPicksState] = useState<PicksState>({ status: 'loading' })
  const [candidates, setCandidates] = useState<DailyCandidate[]>([])
  const [matches, setMatches] = useState<MatchSummary[]>([])
  const [performance, setPerformance] = useState<Performance | null>(null)
  const [modelVsMarket, setModelVsMarket] = useState<ModelVsMarketData | null>(null)
  const [selected, setSelected] = useState<Selected | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const day = localDay(dayOffset)
  const when = dayLabel(dayOffset)

  useEffect(() => {
    setPicksState({ status: 'loading' })

    getTopPicks(minConfidence ?? undefined, day, scope)
      .then((picks) => setPicksState({ status: 'ready', picks }))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setPicksState({ status: 'empty' })
        } else {
          setPicksState({ status: 'error', message: err.message ?? 'No se pudo conectar con la API' })
        }
      })

    getDailyCandidates(minConfidence ?? undefined, day, scope)
      .then(setCandidates)
      .catch(() => setCandidates([]))

    getPerformance(scope)
      .then(setPerformance)
      .catch(() => setPerformance(null))

    getModelVsMarket(scope)
      .then(setModelVsMarket)
      .catch(() => setModelVsMarket(null))

    if (scope === 'colombia') {
      getColombiaStatus()
        .then(setColombiaStatus)
        .catch(() => setColombiaStatus(null))
    }

    getTodaysMatches(day, scope)
      .then(setMatches)
      .catch(() => setMatches([]))
  }, [refreshKey, minConfidence, day, scope])

  const changeScope = (next: Scope) => {
    setScope(next)
    const url = new URL(window.location.href)
    if (next === 'colombia') url.searchParams.set('seccion', 'colombia')
    else url.searchParams.delete('seccion')
    window.history.replaceState(null, '', url)
  }

  // Refresco automático: la liquidación corre cada 2 h en segundo plano y los
  // picks de mañana se regeneran por la mañana; sin esto había que pulsar
  // "Actualizar" para enterarse.
  useEffect(() => {
    const id = window.setInterval(() => setRefreshKey((k) => k + 1), 5 * 60 * 1000)
    return () => window.clearInterval(id)
  }, [])

  // Enlace directo a un partido: ?match=ID abre su análisis (compartible).
  useEffect(() => {
    const id = Number(new URLSearchParams(window.location.search).get('match'))
    if (!id) return
    getMatchMarkets(id)
      .then((m) => setSelected({ match: { match_id: m.match_id, home_team: m.home_team, away_team: m.away_team, league: m.league, kickoff_utc: m.kickoff_utc } }))
      .catch(() => undefined)
  }, [])

  const openPick = (pick: PickOfTheDay) => setSelected({ match: pick, pick })
  const openMatch = (m: MatchSummary) => setSelected({ match: m })

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
        <div className="mb-6">
          <SectionSelector value={scope} onChange={changeScope} />
        </div>
        {scope === 'colombia' && <ColombiaBanner status={colombiaStatus} />}
        {performance && (
          <div className="mb-6">
            <PerformanceBar performance={performance} />
          </div>
        )}
        {modelVsMarket && (
          <div className="-mt-3 mb-6">
            <ModelVsMarket data={modelVsMarket} />
          </div>
        )}

        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {scope === 'colombia' ? 'Picks Colombia' : 'Picks'} de {when}
            </h2>
            <DaySelector value={dayOffset} onChange={setDayOffset} />
          </div>
          <ConfidenceFilter value={minConfidence} onChange={setMinConfidence} />
        </div>
        {dayOffset === 1 && (
          <p className="mb-4 text-xs text-slate-400 dark:text-slate-500">
            Adelanto con las cuotas de hoy: mañana por la mañana se regeneran con las cuotas frescas y pueden cambiar.
          </p>
        )}
        {dayOffset === -1 && (
          <p className="mb-4 text-xs text-slate-400 dark:text-slate-500">
            Lo que el sistema recomendó ayer y cómo terminó. Los picks se liquidan cuando la API publica las estadísticas del partido.
          </p>
        )}

        {picksState.status === 'loading' && (
          <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
            Cargando picks de {when}...
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
              {minConfidence
                ? `No hay picks con confianza ${minConfidence.toLowerCase()} o mayor ${when}`
                : scope === 'colombia'
                  ? `No hay picks colombianos ${when}`
                  : `No hay apuesta segura ${when}`}
            </p>
            <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
              {minConfidence
                ? 'Prueba bajando el filtro de confianza, o vuelve más tarde cuando haya más mercados con backtest validado.'
                : dayOffset === 1
                  ? 'Las cuotas de mañana aún no están sincronizadas o ningún mercado validado tiene ventaja. El flujo diario las trae cada mañana.'
                  : `Ningún partido de ${when} tiene equipos con historial suficiente y una cuota con ventaja plausible. Es normal en días de pocos partidos: el sistema prefiere no recomendar antes que recomendar a ciegas.`}
            </p>
          </div>
        )}

        {picksState.status === 'ready' && (
          <div className="space-y-4">
            {picksState.picks.map((pick, i) => (
              <PickOfTheDayCard
                key={`${pick.match_id}-${pick.market}-${pick.selection}`}
                pick={pick}
                rank={i + 1}
                onOpen={() => openPick(pick)}
              />
            ))}
          </div>
        )}

        <div className="mt-8">
          <h3 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Partidos de {when} con análisis
          </h3>
          <p className="mb-3 text-xs text-slate-400 dark:text-slate-500">
            Partidos con historial suficiente y cuotas. Abre cualquiera para ver <em>todos</em> sus mercados evaluados,
            ordenados de más a menos favorable — aunque no haya pasado el filtro estricto de picks.
          </p>
          {matches.length === 0 ? (
            <div className="rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
              {dayOffset === 0 ? 'Hoy' : dayOffset === 1 ? 'Mañana' : 'Ayer'} no hay partidos con datos suficientes para analizar.
            </div>
          ) : (
            <ul className="divide-y divide-slate-100 overflow-hidden rounded-2xl border border-slate-200 bg-white dark:divide-slate-800 dark:border-slate-700 dark:bg-slate-900">
              {matches.map((m) => (
                <li key={m.match_id}>
                  <button
                    onClick={() => openMatch(m)}
                    className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60"
                  >
                    <div>
                      <p className="font-medium text-slate-900 dark:text-slate-100">
                        {m.home_team} vs {m.away_team}
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        {m.league} ·{' '}
                        {new Date(m.kickoff_utc).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' })} · {m.n_markets}{' '}
                        mercados
                        {m.final_score && <span className="ml-2 font-semibold text-slate-700 dark:text-slate-200">Final {m.final_score}</span>}
                      </p>
                    </div>
                    <span className="shrink-0 text-xs text-slate-400 dark:text-slate-500">
                      {m.has_pick && <span className="mr-2 font-semibold text-indigo-600 dark:text-indigo-400">PICK</span>}
                      Ver mercados →
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {otherCandidates.length > 0 && (
          <div className="mt-8">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Otros candidatos evaluados {when}
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

      {selected && <MatchDetail match={selected.match} pick={selected.pick} onClose={() => setSelected(null)} />}
    </div>
  )
}

export default App
