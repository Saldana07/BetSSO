import type { ReactNode } from 'react'
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
  const { settled_picks, wins, losses, hit_rate, profit_loss_units, yield_pct, clv_picks, avg_clv_pct, positive_clv_rate } = performance
  const big = 'font-mono text-4xl leading-none font-semibold xl:text-[44px]'
  return (
    <section aria-label="Resultado real" className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Kpi label="Picks liquidados">
          <div className="flex items-baseline gap-2.5">
            <span className={big}>{settled_picks}</span>
            <span className="text-[15px] text-muted">
              {wins} G · {losses} P
            </span>
          </div>
          <div aria-hidden="true" className="flex h-2 gap-0.5 overflow-hidden rounded-full">
            <div className="bg-win" style={{ flexGrow: wins }} />
            <div className="bg-loss" style={{ flexGrow: losses }} />
          </div>
        </Kpi>
        <Kpi label="Acierto" note="con cuotas entre 1.50 y 2.00">
          <span className={big}>{((hit_rate ?? 0) * 100).toFixed(0)}%</span>
        </Kpi>
        <Kpi label="Ganancia" note={yield_pct !== null ? `yield ${signed(yield_pct, 1, '%')} a 1 unidad por pick` : undefined}>
          <span className={`${big} ${profit_loss_units >= 0 ? 'text-win' : 'text-loss'}`}>{signed(profit_loss_units, 2, ' u')}</span>
        </Kpi>
        <Kpi
          label="Línea de cierre"
          note={clv_picks > 0 ? `${((positive_clv_rate ?? 0) * 100).toFixed(0)}% batieron el cierre · ${clv_picks} ${clv_picks === 1 ? 'pick' : 'picks'}` : 'aún sin cuotas de cierre'}
        >
          <span
            className={`${big} ${avg_clv_pct === null ? 'text-faint' : avg_clv_pct >= 0 ? 'text-win' : 'text-loss'}`}
            title="Closing Line Value: cuánto mejor fue la cuota tomada que la de cierre en la misma casa. Es la medida profesional de ventaja: converge mucho antes que acertar o fallar partidos."
          >
            {avg_clv_pct === null ? '—' : signed(avg_clv_pct, 1, '%')}
          </span>
        </Kpi>
      </div>
      {settled_picks < MIN_PICKS_FOR_SIGNIFICANCE && (
        <p className="flex items-center gap-3 rounded-2xl border border-warn-line bg-warn-bg px-4 py-3 text-sm text-warn-fg">
          <Icon name="alert" />
          Muestra demasiado pequeña: {settled_picks} de {MIN_PICKS_FOR_SIGNIFICANCE} picks. Estos porcentajes todavía son ruido, no
          evidencia.
        </p>
      )}
    </section>
  )
}

const SECTION_NAME: Record<Scope, string> = { europa: 'Europa', colombia: 'Colombia', nba: 'NBA' }

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
          <span className={`font-mono text-4xl leading-none font-semibold ${pending > 0 ? (scope === 'nba' ? 'text-sky' : 'text-lime') : ''}`}>
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
