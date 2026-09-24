import { kickoffLabel } from '../format'
import type { PickOfTheDay } from '../types'
import { Icon } from './Icon'
import { formatSelection } from './MarketLabel'

/** "Hoy no hay pick": un estado honesto, con salida a los otros días. */
export function EmptyPicks({ title, body, actions }: {
  title: string
  body: string
  actions: { label: string; onClick: () => void; primary?: boolean }[]
}) {
  return (
    <section className="flex min-h-96 flex-col justify-center gap-6 rounded-3xl border border-dashed border-ink-500 bg-ink-850 p-8 sm:p-10">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-ink-800 text-muted">
        <Icon name="ticketOff" size={26} />
      </div>
      <div className="flex max-w-xl flex-col gap-2.5">
        <h2 className="m-0 font-display text-3xl leading-tight font-extrabold tracking-tight sm:text-[34px]">{title}</h2>
        <p className="m-0 text-base leading-relaxed text-pretty text-muted">{body}</p>
      </div>
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {actions.map((a) => (
            <button
              key={a.label}
              type="button"
              onClick={a.onClick}
              className={`min-h-11 rounded-xl px-4.5 text-sm transition-colors ${
                a.primary ? 'bg-fg font-semibold text-ink-950 hover:bg-white' : 'border border-ink-500 font-medium text-fg-2 hover:text-fg'
              }`}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

/** Adelanto del primer pick de mañana (en la columna lateral de "Hoy"). */
export function TomorrowCard({ pick, onSeeDay }: { pick: PickOfTheDay; onSeeDay: () => void }) {
  return (
    <section aria-label="Mañana" className="flex flex-col gap-4 rounded-3xl border border-ink-600 bg-ink-850 p-6">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold tracking-[0.12em] text-faint uppercase">Mañana</span>
        <button type="button" onClick={onSeeDay} className="text-[13px] text-fg-2 underline underline-offset-4 hover:text-fg">
          Ver el día
        </button>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-[13px] text-muted">{kickoffLabel(pick.kickoff_utc)}</span>
        <span className="font-display text-[22px] leading-tight font-bold tracking-tight">
          {pick.home_team} vs {pick.away_team}
        </span>
      </div>
      <div className="flex items-center justify-between gap-3 rounded-2xl bg-ink-800 px-4 py-3.5">
        <span className="text-[15px] font-semibold">{formatSelection(pick.market, pick.selection, { home: pick.home_team, away: pick.away_team })}</span>
        <span className="flex flex-col items-end gap-0.5">
          <span className="font-mono text-2xl font-semibold">{pick.bookmaker_odds.toFixed(2)}</span>
          <span className="font-mono text-xs text-lime">EV +{(pick.expected_value * 100).toFixed(0)}%</span>
        </span>
      </div>
      <p className="m-0 text-[13px] text-faint">Adelanto: se rehace con cuotas frescas al actualizar mañana.</p>
    </section>
  )
}
