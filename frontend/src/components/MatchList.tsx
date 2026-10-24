import { useState, useMemo } from 'react'
import { isNba } from '../format'
import type { DailyCandidate, MatchSummary, Scope, Validation } from '../types'
import { ConfidenceBadge } from './ConfidenceBadge'
import { Icon } from './Icon'
import { formatMarket, formatSelection } from './MarketLabel'

const heading = 'm-0 text-xs font-semibold tracking-[0.12em] text-faint uppercase'

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' })
}

function marketsLabel(m: MatchSummary, scope: Scope): string {
  if (m.n_evaluated === 0) return m.status === 'SCHEDULED' ? 'sin analizar: faltan cuotas frescas' : 'sin analizar'
  const n = m.n_evaluated ?? m.n_markets
  if (scope === 'nba_players') return `${n} ${n === 1 ? 'línea' : 'líneas'}`
  return `${n} ${n === 1 ? 'mercado' : 'mercados'}${m.n_evaluated != null ? ' analizados' : ''}`
}

const VALIDATION_STYLE: Record<Validation, string> = {
  VALIDADO: 'border-win/40 text-win bg-win/5',
  SIN_VALIDAR: 'border-ink-500 text-fg-2 bg-ink-800',
  SIN_VENTAJA: 'border-loss-line text-loss bg-loss/5',
}

type FilterOption = 'all' | 'recommended' | 'value' | 'settled'

/** Partidos del día con su análisis principal y recomendaciones señaladas. */
export function MatchList({
  matches,
  scope,
  when,
  onOpen,
}: {
  matches: MatchSummary[]
  scope: Scope
  when: string
  onOpen: (m: MatchSummary) => void
}) {
  const [filter, setFilter] = useState<FilterOption>('all')
  const [search, setSearch] = useState('')

  const getFavorable = (m: MatchSummary) => m.mercadoFavorable ?? m.top_analysis ?? m.mercado_favorable

  const recommendedCount = useMemo(
    () => matches.filter((m) => m.has_pick || getFavorable(m)?.is_recommended).length,
    [matches],
  )
  const settledCount = useMemo(
    () => matches.filter((m) => getFavorable(m)?.is_settled || m.status === 'FINISHED').length,
    [matches],
  )
  const valueCount = useMemo(
    () => matches.filter((m) => (getFavorable(m)?.expected_value ?? 0) > 0).length,
    [matches],
  )

  const filteredMatches = useMemo(() => {
    const list = matches.filter((m) => {
      // Búsqueda por texto
      if (search.trim()) {
        const q = search.toLowerCase()
        const matchText = `${m.home_team} ${m.away_team} ${m.league}`.toLowerCase()
        if (!matchText.includes(q)) return false
      }

      const fav = getFavorable(m)

      if (filter === 'recommended') {
        return m.has_pick || fav?.is_recommended
      }
      if (filter === 'value') {
        return (fav?.expected_value ?? 0) > 0
      }
      if (filter === 'settled') {
        return fav?.is_settled || m.status === 'FINISHED'
      }
      return true
    })

    // Acomodar: primero los recomendados (ordenados por mayor EV), luego los demás por EV y hora
    return [...list].sort((a, b) => {
      const favA = getFavorable(a)
      const favB = getFavorable(b)
      const recA = Boolean(a.has_pick || favA?.is_recommended)
      const recB = Boolean(b.has_pick || favB?.is_recommended)
      if (recA !== recB) return recA ? -1 : 1
      const evA = favA?.expected_value ?? -1
      const evB = favB?.expected_value ?? -1
      if (Math.abs(evA - evB) > 0.001) return evB - evA
      return new Date(a.kickoff_utc).getTime() - new Date(b.kickoff_utc).getTime()
    })
  }, [matches, filter, search])

  return (
    <section aria-label={`Partidos de ${when}`} className="flex flex-col gap-4">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <h3 className={heading}>
              {isNba(scope) ? `Partidos de la NBA de ${when}` : `Todos los análisis de ${when}`}
            </h3>
            <span className="rounded-full bg-ink-700 px-2.5 py-0.5 font-mono text-[11px] font-semibold text-fg-2">
              {matches.length}
            </span>
          </div>
          <p className="m-0 text-[13px] text-faint">
            Cada partido muestra su mejor análisis calculado por el modelo. Los más recomendados aparecen destacados.
          </p>
        </div>

        {/* Filtros rápidos y buscador */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <input
              type="text"
              placeholder="Buscar equipo..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-9 w-36 sm:w-44 rounded-xl border border-ink-600 bg-ink-850 px-3 text-xs text-fg placeholder:text-muted focus:border-lime focus:outline-none transition-all"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch('')}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted hover:text-fg text-xs"
              >
                ✕
              </button>
            )}
          </div>

          <div className="flex rounded-xl border border-ink-600 bg-ink-850 p-0.5">
            <button
              type="button"
              onClick={() => setFilter('all')}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                filter === 'all' ? 'bg-ink-700 text-fg font-semibold' : 'text-muted hover:text-fg'
              }`}
            >
              Todos ({matches.length})
            </button>
            <button
              type="button"
              onClick={() => setFilter('recommended')}
              className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                filter === 'recommended' ? 'bg-lime text-ink-950 font-semibold' : 'text-muted hover:text-fg'
              }`}
            >
              <span>⭐</span> Recomendados ({recommendedCount})
            </button>
            {valueCount > 0 && (
              <button
                type="button"
                onClick={() => setFilter('value')}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                  filter === 'value' ? 'bg-ink-700 text-lime font-semibold' : 'text-muted hover:text-fg'
                }`}
              >
                +EV ({valueCount})
              </button>
            )}
            {settledCount > 0 && (
              <button
                type="button"
                onClick={() => setFilter('settled')}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                  filter === 'settled' ? 'bg-ink-700 text-fg font-semibold' : 'text-muted hover:text-fg'
                }`}
              >
                Liquidados ({settledCount})
              </button>
            )}
          </div>
        </div>
      </div>

      {filteredMatches.length === 0 ? (
        <div className="rounded-3xl border border-ink-600 bg-ink-850 px-6 py-8 text-center text-sm text-muted">
          {search ? (
            <p className="m-0">No se encontraron partidos que coincidan con «{search}».</p>
          ) : filter === 'recommended' ? (
            <p className="m-0">No hay picks marcados con recomendación estricta hoy. Cambia a «Todos» para ver los demás análisis.</p>
          ) : (
            <p className="m-0">{isNba(scope) ? `No hay partidos de la NBA ${when}.` : `No hay partidos con datos suficientes ${when}.`}</p>
          )}
        </div>
      ) : (
        <div className="grid gap-3.5 sm:gap-4">
          {filteredMatches.map((m) => {
            const ana = m.mercadoFavorable ?? m.top_analysis ?? m.mercado_favorable
            const isRec = Boolean(ana?.is_recommended || m.has_pick)
            const teams = { home: m.home_team, away: m.away_team }
            const isSettled = ana?.is_settled || (m.status === 'FINISHED' && m.final_score !== null)
            const won = ana?.is_win

            // Valores normalizados para cuota, EV y porcentajes
            const odds = ana?.odds ?? (ana as any)?.cuota ?? 1.0
            const ev = ana?.expected_value ?? (ana as any)?.ev ?? (ana as any)?.valor_esperado ?? 0
            const modelProb = ana?.model_probability ?? (ana as any)?.probabilidad_modelo ?? (ana as any)?.probabilidadModelo ?? 0
            const impliedProb = ana?.implied_probability ?? (ana as any)?.probabilidad_mercado ?? (ana as any)?.probabilidadMercado ?? (odds > 0 ? 1 / odds : 0)
            const bookmaker = ana?.bookmaker ?? (ana as any)?.casa ?? null
            const market = ana?.market ?? (ana as any)?.mercado ?? ''
            const selection = ana?.selection ?? (ana as any)?.seleccion ?? ''
            const validation = ana?.validation ?? 'SIN_VALIDAR'
            const evaluatedCount = m.n_evaluated ?? 0
            const hasEvaluations = evaluatedCount > 0

            return (
              <div
                key={m.match_id}
                className={`relative flex flex-col gap-3 rounded-2xl sm:rounded-3xl border p-4 sm:p-5 transition-all duration-200 ${
                  isRec
                    ? 'border-lime/40 bg-gradient-to-br from-lime/[0.04] to-ink-900 shadow-[0_4px_24px_-10px_rgba(163,230,53,0.15)] hover:border-lime/60'
                    : 'border-ink-600 bg-ink-850 hover:border-ink-500'
                }`}
              >
                {/* Cabecera del partido: Hora, Liga, Estado, Equipos */}
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-700/60 pb-3">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold text-fg-2 flex items-center gap-1.5 bg-ink-800 px-2 py-0.5 rounded-md">
                      <Icon name="clock" size={13} className="text-muted" />
                      {time(m.kickoff_utc)}
                    </span>
                    <span className="text-xs font-medium text-muted">{m.league}</span>
                  </div>

                  <div className="flex items-center gap-2">
                    {/* Badge destacado: MÁS RECOMENDADO o ANÁLISIS */}
                    {isRec ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-lime px-2.5 py-0.5 text-[11px] font-bold text-ink-950 tracking-wide uppercase shadow-sm">
                        <span>★</span> MÁS RECOMENDADO
                      </span>
                    ) : ana ? (
                      <span className="inline-flex items-center rounded-md bg-ink-700/70 border border-ink-600 px-2 py-0.5 text-[11px] font-medium text-fg-2">
                        Análisis del modelo
                      </span>
                    ) : null}

                    {/* Marcador final si terminó */}
                    {m.final_score && (
                      <span className="rounded-md bg-ink-800 px-2 py-0.5 font-mono text-xs font-bold text-fg border border-ink-600">
                        FT: {m.final_score}
                      </span>
                    )}
                  </div>
                </div>

                {/* Equipos */}
                <div className="flex flex-col sm:flex-row sm:items-baseline justify-between gap-1">
                  <span className="text-lg sm:text-xl font-bold font-display text-fg leading-snug">
                    {m.home_team} <span className="text-faint font-normal text-sm">vs</span> {m.away_team}
                  </span>
                  <span className="text-xs text-muted">
                    {marketsLabel(m, scope)}
                  </span>
                </div>

                {/* BLOQUE LO MÁS FAVORABLE */}
                {ana ? (
                  <div className={`flex flex-col gap-2.5 rounded-2xl p-4 sm:p-5 transition-all ${
                    isRec
                      ? 'border border-lime/40 bg-lime/[0.06] shadow-[0_4px_20px_-10px_rgba(163,230,53,0.15)]'
                      : 'border border-ink-600 bg-ink-800/80'
                  }`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[11px] font-bold tracking-[0.14em] text-lime uppercase flex items-center gap-1.5">
                        <span className="inline-block size-2 rounded-full bg-lime animate-pulse" />
                        Lo más favorable
                      </span>

                      {/* Estado de liquidación si ya terminó */}
                      {isSettled && (
                        <div>
                          {won === true && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-win/20 border border-win/40 px-2.5 py-0.5 text-xs font-bold text-win">
                              <Icon name="check" size={13} />
                              Acertado (+{(odds - 1).toFixed(2)}u)
                            </span>
                          )}
                          {won === false && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-loss/20 border border-loss/40 px-2.5 py-0.5 text-xs font-bold text-loss">
                              <Icon name="x" size={13} />
                              Fallado (-1.00u)
                            </span>
                          )}
                          {won === null && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-ink-700 px-2.5 py-0.5 text-xs font-medium text-muted">
                              Liquidando...
                            </span>
                          )}
                        </div>
                      )}
                    </div>

                    {/* Pronóstico principal */}
                    <div className="flex flex-col gap-1">
                      <p className="m-0 font-display text-lg sm:text-xl font-extrabold text-fg">
                        {ana.player ? `${ana.player} · ` : `${formatMarket(market)} · `}
                        <span className={isRec ? 'text-lime' : 'text-fg'}>
                          {formatSelection(market, selection, teams)}
                        </span>
                      </p>

                      {/* Línea de métricas y datos clave: Cuota, EV y porcentajes */}
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-fg-2 pt-1">
                        <span>
                          Cuota <strong className="font-mono text-fg font-extrabold">{odds.toFixed(2)}</strong>
                          {bookmaker && <span className="text-muted text-xs ml-1">en {bookmaker}</span>}
                        </span>
                        <span className="text-faint">·</span>
                        <span>
                          Ventaja EV <strong className={`font-mono font-extrabold ${ev > 0 ? 'text-lime' : 'text-fg-2'}`}>
                            {ev > 0 ? `+${(ev * 100).toFixed(1)}%` : `${(ev * 100).toFixed(1)}%`}
                          </strong>
                        </span>
                        <span className="text-faint hidden sm:inline">·</span>
                        <span className="text-xs text-muted hidden sm:inline">
                          modelo {(modelProb * 100).toFixed(0)}% vs. mercado {(impliedProb * 100).toFixed(0)}%
                        </span>
                        <span className={`inline-block rounded-full border px-2 py-0.5 text-[10px] font-semibold ${VALIDATION_STYLE[validation] ?? VALIDATION_STYLE.SIN_VALIDAR}`}>
                          {validation === 'VALIDADO' ? 'Validado' : validation === 'SIN_VENTAJA' ? 'Sin ventaja' : 'Sin validar'}
                        </span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-3 rounded-2xl border border-dashed border-ink-600 bg-ink-800/40 p-4 text-xs text-muted">
                    <div className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-ink-700/60 text-faint">
                      <Icon name="clock" size={15} />
                    </div>
                    <div className="flex flex-col gap-0.5">
                      <span className="font-semibold text-fg-2 uppercase tracking-wider text-[11px]">
                        {hasEvaluations ? 'Estado: Sin mercado favorable' : 'Estado: Análisis pendiente'}
                      </span>
                      <span>
                        {hasEvaluations
                          ? `Se evaluaron ${evaluatedCount} mercados, pero ninguno cumple los filtros para destacarlo (cuota 1.50–2.00 y EV 2%–20%). Abre todos los mercados para ver el análisis completo.`
                          : m.status === 'SCHEDULED'
                            ? 'Todavía no hay evaluaciones de cuotas frescas para este partido. Actualiza las cuotas para que el modelo pueda analizarlo.'
                            : 'No hay evaluaciones guardadas para este partido.'}
                      </span>
                    </div>
                  </div>
                )}

                {/* Botón inferior para abrir el resto de los mercados */}
                <div className="flex items-center justify-end pt-1">
                  <button
                    type="button"
                    onClick={() => onOpen(m)}
                    className="flex items-center gap-2 rounded-xl border border-ink-600 bg-ink-800 px-4 py-2.5 text-xs font-semibold text-fg hover:bg-ink-700 hover:border-ink-500 hover:text-white transition-all shadow-sm"
                  >
                    <span>Ver el resto del análisis ({m.n_evaluated ?? m.n_markets} mercados)</span>
                    <Icon name="chevronRight" size={15} className="text-muted" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

/** El resto de candidatos que pasaron el filtro de cuota y EV. */
export function CandidatesTable({ candidates, when }: { candidates: DailyCandidate[]; when: string }) {
  if (candidates.length === 0) return null
  return (
    <details className="group rounded-3xl border border-ink-600 bg-ink-850">
      <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 px-6 py-4 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2.5 text-xs font-semibold tracking-[0.12em] text-faint uppercase">
          <Icon name="chevronRight" size={16} className="text-faint transition-transform group-open:rotate-90" />
          Tabla de candidatos evaluados de {when} ({candidates.length})
        </span>
      </summary>
      <div className="overflow-x-auto px-6 pb-4">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-faint uppercase border-b border-ink-700">
              <th className="py-2.5 pr-4 font-medium">Partido</th>
              <th className="py-2.5 pr-4 font-medium">Apuesta</th>
              <th className="py-2.5 pr-4 text-right font-medium">Cuota</th>
              <th className="py-2.5 pr-4 text-right font-medium">EV</th>
              <th className="py-2.5 font-medium">Confianza</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => (
              <tr key={`${c.match_id}-${c.market}-${c.selection}`} className="border-t border-ink-700">
                <td className="py-3 pr-4">
                  <span className="block font-medium">
                    {c.home_team} vs {c.away_team}
                  </span>
                  <span className="text-xs text-muted">{c.league}</span>
                </td>
                <td className="py-3 pr-4 text-fg-2">
                  <span className="block font-medium">
                    {c.player && `${c.player} · `}
                    {formatSelection(c.market, c.selection, { home: c.home_team, away: c.away_team })}
                  </span>
                  <span className="text-xs text-muted">{formatMarket(c.market)}</span>
                </td>
                <td className="py-3 pr-4 text-right">
                  <span className="block font-mono font-semibold">{c.bookmaker_odds.toFixed(2)}</span>
                  {c.bookmaker && <span className="text-xs text-muted">{c.bookmaker}</span>}
                </td>
                <td className="py-3 pr-4 text-right font-mono font-semibold text-lime">+{(c.expected_value * 100).toFixed(1)}%</td>
                <td className="py-3">
                  <ConfidenceBadge level={c.confidence_label} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
