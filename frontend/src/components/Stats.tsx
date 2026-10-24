import { useState, type ReactNode } from 'react'
import { isNba } from '../format'
import { MIN_PICKS_FOR_SIGNIFICANCE, type Performance, type Scope } from '../types'
import { Icon } from './Icon'

const eyebrow = 'text-xs font-semibold tracking-[0.12em] text-faint uppercase'

function signed(v: number, digits: number, unit = ''): string {
  return `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(digits)}${unit}`
}

function Kpi({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 rounded-3xl border border-ink-600 bg-ink-850 px-6 py-5">
      <span className={eyebrow}>{label}</span>
      {children}
      {note && <span className="text-[13px] text-muted">{note}</span>}
    </div>
  )
}

/** Resultado REAL de los picks liquidados del apartado (no el backtest). */
export function KpiStrip({ performance }: { performance: Performance }) {
  const [viewMode, setViewMode] = useState<'all' | 'recommended'>('recommended')

  const hasRec = performance.recommended_settled != null && performance.recommended_settled > 0

  const activeSettled = viewMode === 'recommended' && hasRec ? performance.recommended_settled! : performance.settled_picks
  const activeWins = viewMode === 'recommended' && hasRec ? performance.recommended_wins! : performance.wins
  const activeLosses = viewMode === 'recommended' && hasRec ? (performance.recommended_losses ?? (activeSettled - activeWins)) : performance.losses
  const activeHitRate = viewMode === 'recommended' && hasRec ? performance.recommended_hit_rate : performance.hit_rate
  const activePnl = viewMode === 'recommended' && hasRec ? (performance.recommended_profit_loss ?? 0) : performance.profit_loss_units
  const activeYield = viewMode === 'recommended' && hasRec ? performance.recommended_yield_pct : performance.yield_pct

  const big = 'font-mono text-4xl leading-none font-semibold xl:text-[44px]'

  return (
    <section aria-label="Resultado real" className="flex flex-col gap-3">
      {hasRec && (
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <span className="text-xs font-semibold tracking-[0.12em] text-faint uppercase">
            Efectividad y Acierto del Modelo
          </span>
          <div className="flex rounded-xl border border-ink-600 bg-ink-850 p-0.5 text-xs">
            <button
              type="button"
              onClick={() => setViewMode('all')}
              className={`rounded-lg px-3 py-1 font-medium transition-colors ${
                viewMode === 'all' ? 'bg-ink-700 text-fg font-semibold' : 'text-muted hover:text-fg'
              }`}
            >
              Todos los análisis ({performance.settled_picks})
            </button>
            <button
              type="button"
              onClick={() => setViewMode('recommended')}
              className={`rounded-lg px-3 py-1 font-medium transition-colors ${
                viewMode === 'recommended' ? 'bg-lime text-ink-950 font-semibold' : 'text-muted hover:text-fg'
              }`}
            >
              ⭐ Solo más recomendados ({performance.recommended_settled})
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Kpi
          label={viewMode === 'recommended' ? 'Picks recomendados' : 'Análisis liquidados'}
          note={
            viewMode === 'all' && hasRec
              ? `Incluye ${performance.recommended_settled} picks recomendados`
              : 'Verificados con resultados oficiales'
          }
        >
          <div className="flex items-baseline gap-2.5">
            <span className={big}>{activeSettled}</span>
            <span className="text-[15px] text-muted">
              {activeWins} G · {activeLosses} P
            </span>
          </div>
          <div aria-hidden="true" className="flex h-2 gap-0.5 overflow-hidden rounded-full">
            <div className="bg-win" style={{ flexGrow: activeWins }} />
            <div className="bg-loss" style={{ flexGrow: activeLosses }} />
          </div>
        </Kpi>

        <Kpi
          label="Tasa de acierto"
          note={
            viewMode === 'all' && hasRec && performance.recommended_hit_rate != null
              ? `⭐ ${(performance.recommended_hit_rate * 100).toFixed(0)}% en picks recomendados`
              : 'cuotas promedio de mercado'
          }
        >
          <span className={`${big} text-fg`}>
            {activeHitRate != null ? `${(activeHitRate * 100).toFixed(1)}%` : '—'}
          </span>
        </Kpi>

        <Kpi
          label="Ganancia / P&L"
          note={activeYield !== null && activeYield !== undefined ? `yield ${signed(activeYield, 1, '%')} a 1 unidad plana` : undefined}
        >
          <span className={`${big} ${activePnl >= 0 ? 'text-win' : 'text-loss'}`}>
            {signed(activePnl, 2, ' u')}
          </span>
        </Kpi>

        <Kpi
          label="Línea de cierre"
          note={
            performance.clv_picks > 0
              ? `${((performance.positive_clv_rate ?? 0) * 100).toFixed(0)}% batieron el cierre · ${performance.clv_picks} ${performance.clv_picks === 1 ? 'pick' : 'picks'}`
              : 'aún sin cuotas de cierre'
          }
        >
          <span
            className={`${big} ${performance.avg_clv_pct === null ? 'text-faint' : performance.avg_clv_pct >= 0 ? 'text-win' : 'text-loss'}`}
            title="Closing Line Value: cuánto mejor fue la cuota tomada que la de cierre en la misma casa. Es la medida profesional de ventaja."
          >
            {performance.avg_clv_pct === null ? '—' : signed(performance.avg_clv_pct, 1, '%')}
          </span>
        </Kpi>
      </div>

      {activeSettled < MIN_PICKS_FOR_SIGNIFICANCE && (
        <p className="flex items-center gap-3 rounded-2xl border border-warn-line bg-warn-bg px-4 py-3 text-sm text-warn-fg">
          <Icon name="alert" />
          Muestra en crecimiento: {activeSettled} de {MIN_PICKS_FOR_SIGNIFICANCE} análisis liquidados. A medida que se jueguen más partidos aumentará la significancia estadística.
        </p>
      )}
    </section>
  )
}

const SECTION_NAME: Record<Scope, string> = { europa: 'Europa', colombia: 'Colombia', nba: 'NBA', nba_players: 'NBA jugadores' }

/** Contador del apartado cuando todavía no hay picks liquidados. */
export function CounterCard({ scope, performance }: { scope: Scope; performance: Performance | null }) {
  const pending = performance?.pending_picks ?? 0
  return (
    <section aria-label="Contador del apartado" className="flex flex-col gap-4 rounded-3xl border border-ink-600 bg-ink-850 p-6">
      <span className={eyebrow}>Contador · {SECTION_NAME[scope]}</span>
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <span className="font-mono text-4xl leading-none font-semibold">{performance?.settled_picks ?? 0}</span>
          <span className="text-[13px] text-muted">liquidados</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className={`font-mono text-4xl leading-none font-semibold ${pending > 0 ? (isNba(scope) ? 'text-sky' : 'text-lime') : ''}`}>
            {pending}
          </span>
          <span className="text-[13px] text-muted">pendientes</span>
        </div>
      </div>
      <p className="m-0 text-sm leading-relaxed text-pretty text-muted">
        {scope === 'europa'
          ? 'Todavía no hay picks liquidados.'
          : `No suma a los demás contadores. Con ${MIN_PICKS_FOR_SIGNIFICANCE} liquidados habrá una primera lectura; antes, los porcentajes son ruido.`}
      </p>
    </section>
  )
}
