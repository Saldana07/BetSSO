import { useCallback, useEffect, useState } from 'react'
import {
  ApiError,
  getColombiaStatus,
  getDailyCandidates,
  getMatchMarkets,
  getModelVsMarket,
  getNbaStatus,
  getPerformance,
  getTodaysMatches,
  getTopPicks,
  localDay,
} from './api'
import { ActionBar } from './components/ActionBar'
import { ConfidenceFilter, DayTabs } from './components/Controls'
import { Icon } from './components/Icon'
import { MatchDetail, type MatchRef } from './components/MatchDetail'
import { CandidatesTable, MatchList } from './components/MatchList'
import { ModelVsMarket } from './components/ModelVsMarket'
import { PickTicket } from './components/PickTicket'
import { EmptyPicks } from './components/PicksExtras'
import { ColombiaCard, NbaDataCard, NbaPropsValidationCard, NbaSeasonCard, NbaValidationCard } from './components/Sections'
import { MobileTopBar, Sidebar, type SectionMeta } from './components/Sidebar'
import { CounterCard, KpiStrip } from './components/Stats'
import type {
  ColombiaStatus,
  ConfidenceLevel,
  DailyCandidate,
  MatchSummary,
  ModelVsMarket as ModelVsMarketData,
  NbaStatus,
  Performance,
  PickOfTheDay,
  Scope,
} from './types'
import { useActions } from './useActions'
import { dayDate, dayLabel, dayTitle, isNba, type DayOffset } from './format'

type PicksState =
  | { status: 'loading' }
  | { status: 'empty' }
  | { status: 'error'; message: string }
  | { status: 'ready'; picks: PickOfTheDay[] }

type Selected = { match: MatchRef; pick?: PickOfTheDay }

const SCOPES: Scope[] = ['europa', 'colombia', 'nba', 'nba_players']

const EYEBROW: Record<Scope, string> = {
  europa: 'Europa · mercados validados',
  colombia: 'Fútbol colombiano · contador propio',
  nba: 'NBA · experimental · contador propio',
  nba_players: 'NBA · jugadores · a prueba, contador propio',
}

const PICKS_HEADING: Record<Scope, string> = { europa: 'Picks', colombia: 'Picks Colombia', nba: 'Picks NBA', nba_players: 'Picks de jugadores' }

function emptyTitle(scope: Scope, offset: DayOffset, minConfidence: ConfidenceLevel | null): string {
  const when = dayLabel(offset)
  if (minConfidence) return `No hay picks con confianza ${minConfidence.toLowerCase()} o mayor ${when}`
  const day = dayTitle(offset)
  if (offset === -1) return scope === 'europa' ? 'Ayer no hubo apuesta segura' : 'Ayer no hubo picks'
  if (scope === 'colombia') return `${day} no hay picks colombianos`
  if (scope === 'nba') return `${day} no hay picks de la NBA`
  if (scope === 'nba_players') return `${day} no hay picks de jugadores`
  return `${day} no hay apuesta segura`
}

function emptyBody(scope: Scope, offset: DayOffset, minConfidence: ConfidenceLevel | null): string {
  if (minConfidence) return 'Prueba bajando el filtro de confianza, o vuelve cuando haya más mercados con backtest validado.'
  if (scope === 'nba_players')
    return 'Un pick de jugador necesita que Betano o Bwin paguen más de lo justo por una línea de puntos, rebotes, asistencias o triples. Las casas publican esas líneas el mismo día del partido: «Cuotas Betano / Pinnacle» las trae.'
  if (scope === 'nba')
    return 'Un pick de la NBA necesita que Betano o Bwin paguen más de lo justo (con «Cuotas Betano / Pinnacle»). Si no hay, es que el mercado está bien puesto: en la NBA pasa casi siempre.'
  if (offset === 1)
    return 'Las cuotas de mañana aún no están o ningún mercado validado tiene ventaja. Se traen con «Actualizar el día», que se hace sola la primera vez que abres la página cada mañana.'
  return `Ningún partido de ${dayLabel(offset)} tiene equipos con historial suficiente y una cuota con ventaja creíble. El sistema prefiere no recomendar antes que recomendar a ciegas. Abajo están todos los partidos que analizó.`
}

function App() {
  const [scope, setScope] = useState<Scope>(() => {
    const s = new URLSearchParams(window.location.search).get('seccion')
    return s === 'colombia' || s === 'nba' || s === 'nba_players' ? s : 'europa'
  })
  const [colombiaStatus, setColombiaStatus] = useState<ColombiaStatus | null>(null)
  const [nbaStatus, setNbaStatus] = useState<NbaStatus | null>(null)
  const [minConfidence, setMinConfidence] = useState<ConfidenceLevel | null>(null)
  const [dayOffset, setDayOffset] = useState<DayOffset>(0)
  const [picksState, setPicksState] = useState<PicksState>({ status: 'loading' })
  const [candidates, setCandidates] = useState<DailyCandidate[]>([])
  const [matches, setMatches] = useState<MatchSummary[]>([])
  const [performances, setPerformances] = useState<Record<Scope, Performance | null>>({
    europa: null,
    colombia: null,
    nba: null,
    nba_players: null,
  })
  const [modelVsMarket, setModelVsMarket] = useState<ModelVsMarketData | null>(null)
  const [selected, setSelected] = useState<Selected | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  // Estable: los efectos de las acciones la usan (una función nueva en cada
  // render los relanzaría).
  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])
  const actions = useActions(scope, reload)

  const day = localDay(dayOffset)
  const when = dayLabel(dayOffset)
  // En Colombia y la NBA todos los picks son experimentales (confianza baja):
  // el filtro de confianza solo tiene sentido en Europa.
  const confidence = scope === 'europa' ? minConfidence ?? undefined : undefined

  useEffect(() => {
    setPicksState({ status: 'loading' })

    getTopPicks(confidence, day, scope)
      .then((picks) => setPicksState({ status: 'ready', picks }))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setPicksState({ status: 'empty' })
        } else {
          setPicksState({ status: 'error', message: err.message ?? 'No se pudo conectar con la API' })
        }
      })

    getDailyCandidates(confidence, day, scope)
      .then(setCandidates)
      .catch(() => setCandidates([]))

    // Los tres contadores: el lateral muestra el de cada apartado.
    Promise.all(SCOPES.map((s) => getPerformance(s).catch(() => null))).then(([europa, colombia, nba, nba_players]) =>
      setPerformances({ europa, colombia, nba, nba_players }),
    )

    getModelVsMarket(scope)
      .then(setModelVsMarket)
      .catch(() => setModelVsMarket(null))

    getColombiaStatus()
      .then(setColombiaStatus)
      .catch(() => setColombiaStatus(null))
    getNbaStatus()
      .then(setNbaStatus)
      .catch(() => setNbaStatus(null))

    getTodaysMatches(day, scope)
      .then(setMatches)
      .catch(() => setMatches([]))

  }, [refreshKey, confidence, day, dayOffset, scope])

  const changeScope = (next: Scope) => {
    setScope(next)
    const url = new URL(window.location.href)
    if (next === 'europa') url.searchParams.delete('seccion')
    else url.searchParams.set('seccion', next)
    window.history.replaceState(null, '', url)
  }

  // Relectura periódica de la base de datos (no gasta consultas a la API): por
  // si una acción se lanzó desde otra pestaña o desde daily.bat.
  useEffect(() => {
    const id = window.setInterval(reload, 5 * 60 * 1000)
    return () => window.clearInterval(id)
  }, [reload])

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
  const perf = performances[scope]

  const nbaStart = nbaStatus?.regular_season_start_utc
  const seasonMeta = nbaStart && !nbaStatus?.regular_season_started
    ? { text: new Date(nbaStart).toLocaleDateString('es-CO', { day: 'numeric', month: 'short' }) }
    : null
  const meta: Record<Scope, SectionMeta | null> = {
    europa: performances.europa ? { text: String(performances.europa.settled_picks), mono: true } : null,
    colombia: performances.colombia ? { text: String(performances.colombia.settled_picks + performances.colombia.pending_picks), mono: true } : null,
    nba: seasonMeta ?? (performances.nba ? { text: String(performances.nba.settled_picks), mono: true } : null),
    nba_players: seasonMeta ?? (performances.nba_players ? { text: String(performances.nba_players.settled_picks), mono: true } : null),
  }

  const emptyActions =
    dayOffset === 0
      ? [
          { label: 'Ver los picks de mañana', onClick: () => setDayOffset(1), primary: true },
          { label: 'Cómo terminaron los de ayer', onClick: () => setDayOffset(-1) },
        ]
      : [{ label: 'Volver a hoy', onClick: () => setDayOffset(0), primary: true }]

  return (
    <div className="min-h-screen bg-ink-950 bg-dots text-fg lg:flex">
      <Sidebar scope={scope} onScope={changeScope} meta={meta} status={actions.status} onReload={reload} />

      <div className="min-w-0 flex-1">
        <MobileTopBar scope={scope} onScope={changeScope} status={actions.status} onReload={reload} />

        <main className="mx-auto flex max-w-[1240px] flex-col gap-7 px-4 py-6 sm:px-8 lg:px-11 lg:py-9">
          <header className="flex flex-wrap items-end justify-between gap-5">
            <div className="flex flex-col gap-1.5">
              <span className={`text-xs font-semibold tracking-[0.14em] uppercase ${isNba(scope) ? 'text-sky' : 'text-lime'}`}>{EYEBROW[scope]}</span>
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h1 className="m-0 font-display text-5xl leading-none font-extrabold tracking-tight sm:text-[56px]">{dayTitle(dayOffset)}</h1>
                <span className="text-[17px] text-muted">{dayDate(dayOffset)}</span>
              </div>
            </div>
            <DayTabs value={dayOffset} onChange={setDayOffset} />
          </header>

          <ActionBar scope={scope} actions={actions} />

          {/* Arriba solo los picks recomendados; debajo, todos los análisis; y lo
              demás (contador, pruebas del modelo, datos) plegado al final. */}
          <section aria-label={`${PICKS_HEADING[scope]} de ${when}`} className="flex min-w-0 flex-col gap-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="m-0 text-xs font-semibold tracking-[0.12em] text-faint uppercase">
                  {PICKS_HEADING[scope]} de {when}
                </h2>
                {scope === 'europa' && <ConfidenceFilter value={minConfidence} onChange={setMinConfidence} />}
              </div>

              {dayOffset === 1 && (
                <p className="m-0 text-[13px] text-faint">
                  {isNba(scope)
                    ? 'Adelanto con las líneas de ahora: se rehacen cada vez que se actualiza la NBA (las lesiones de última hora mueven mucho las líneas).'
                    : 'Adelanto con las cuotas de ahora: se rehacen cuando abras la página mañana, o con «Refrescar cuotas» antes del partido.'}
                </p>
              )}
              {dayOffset === -1 && (
                <p className="m-0 text-[13px] text-faint">
                  Lo que el sistema recomendó ayer y cómo terminó. «Verificar resultados» trae los resultados y los liquida (la API publica las
                  estadísticas ~2-3 h después del inicio).
                </p>
              )}

              {picksState.status === 'loading' && (
                <div className="flex min-h-72 items-center justify-center rounded-3xl border border-ink-600 bg-ink-850 text-sm text-muted">
                  Cargando picks de {when}...
                </div>
              )}

              {picksState.status === 'error' && (
                <div className="flex flex-col gap-2 rounded-3xl border border-loss-line bg-loss/5 p-6 text-sm">
                  <span className="flex items-center gap-2 font-semibold text-loss">
                    <Icon name="alert" />
                    No se pudo conectar con la API
                  </span>
                  <span className="text-fg-2">{picksState.message}</span>
                  <span className="text-muted">
                    Verifica que el backend esté corriendo en <code className="font-mono">http://127.0.0.1:8000</code>.
                  </span>
                </div>
              )}

              {picksState.status === 'empty' && (
                <EmptyPicks
                  title={emptyTitle(scope, dayOffset, confidence ? minConfidence : null)}
                  body={emptyBody(scope, dayOffset, confidence ? minConfidence : null)}
                  actions={emptyActions}
                />
              )}

              {picksState.status === 'ready' && (
                <div className="grid gap-5 xl:grid-cols-2">
                  {picksState.picks.map((pick, i) => (
                    <div key={`${pick.match_id}-${pick.market}-${pick.selection}`} className={i === 0 ? 'xl:col-span-2' : ''}>
                      <PickTicket
                        pick={pick}
                        scope={scope}
                        rank={i + 1}
                        variant={i === 0 ? 'hero' : 'compact'}
                        onOpen={() => openPick(pick)}
                      />
                    </div>
                  ))}
                </div>
              )}
          </section>

          <MatchList matches={matches} scope={scope} when={when} onOpen={openMatch} />
          <CandidatesTable candidates={otherCandidates} when={when} />

          <details className="group rounded-3xl border border-ink-600 bg-ink-900">
            <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-3 px-6 py-4 [&::-webkit-details-marker]:hidden">
              <span className="flex items-center gap-2.5 text-sm font-semibold text-fg-2">
                <Icon name="chevronRight" size={18} className="text-faint transition-transform group-open:rotate-90" />
                Contador y pruebas del modelo
              </span>
              {perf && (
                <span className="text-xs text-faint">
                  {perf.settled_picks} {perf.settled_picks === 1 ? 'pick liquidado' : 'picks liquidados'}
                </span>
              )}
            </summary>
            <div className="flex flex-col gap-5 px-4 pb-6 sm:px-6">
              {isNba(scope) && <NbaSeasonCard status={nbaStatus} players={scope === 'nba_players'} />}
              {perf && perf.settled_picks > 0 ? <KpiStrip performance={perf} /> : <CounterCard scope={scope} performance={perf} />}
              <div className="grid items-start gap-5 lg:grid-cols-2">
                {scope === 'colombia' && <ColombiaCard status={colombiaStatus} />}
                {isNba(scope) && <NbaDataCard status={nbaStatus} players={scope === 'nba_players'} />}
                {modelVsMarket && <ModelVsMarket data={modelVsMarket} />}
              </div>
              {scope === 'nba' && <NbaValidationCard v={nbaStatus?.validation ?? null} />}
              {scope === 'nba_players' && <NbaPropsValidationCard v={nbaStatus?.props_validation ?? null} />}
            </div>
          </details>
        </main>
      </div>

      {selected && <MatchDetail match={selected.match} pick={selected.pick} scope={scope} onClose={() => setSelected(null)} />}
    </div>
  )
}

export default App
