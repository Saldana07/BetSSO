import { isNba, resetTime } from '../format'
import type { ActionStatus, Scope } from '../types'
import { Icon, type IconName } from './Icon'

const SECTIONS: { value: Scope; label: string; short: string; icon: IconName }[] = [
  { value: 'europa', label: 'Europa', short: 'Europa', icon: 'globe' },
  { value: 'colombia', label: 'Fútbol colombiano', short: 'Colombia', icon: 'flag' },
  { value: 'nba', label: 'NBA', short: 'NBA', icon: 'ball' },
  { value: 'nba_players', label: 'NBA · jugadores', short: 'Jugadores', icon: 'user' },
]

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div className={`flex items-center justify-center rounded-xl bg-lime text-ink-950 ${compact ? 'size-9' : 'size-10'}`}>
        <Icon name="ticket" size={compact ? 20 : 22} strokeWidth={2} />
      </div>
      <div className="flex flex-col gap-0.5">
        <span className={`font-display font-bold tracking-tight ${compact ? 'text-lg' : 'text-xl'}`}>Apuesta Segura</span>
        {!compact && <span className="text-xs text-faint">Fútbol y NBA · cuota 1.50–2.00 · +EV</span>}
      </div>
    </div>
  )
}

function QuotaMeter({ label, used, limit, tone }: { label: string; used: number; limit: number; tone: string }) {
  const left = limit - used
  const color = left <= 0 ? 'text-loss' : left < 20 ? 'text-warn' : 'text-fg-2'
  const bar = left <= 0 ? 'bg-loss' : left < 20 ? 'bg-warn' : tone
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between text-[13px]">
        <span className="text-fg-2">{label}</span>
        <span className={`font-mono ${color}`}>
          {used}/{limit}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-ink-600">
        <div className={`h-1.5 ${bar}`} style={{ width: `${Math.min(100, (used / limit) * 100)}%` }} />
      </div>
    </div>
  )
}

export interface SectionMeta {
  text: string
  mono?: boolean
}

/** Barra lateral (escritorio): apartados con su contador, consultas de hoy y
 *  el botón de recargar, que solo relee la base de datos. */
export function Sidebar({ scope, onScope, meta, status, onReload }: {
  scope: Scope
  onScope: (s: Scope) => void
  meta: Record<Scope, SectionMeta | null>
  status: ActionStatus | null
  onReload: () => void
}) {
  return (
    <aside className="sticky top-0 hidden h-screen w-66 shrink-0 flex-col gap-8 overflow-y-auto border-r border-ink-700 bg-ink-900 px-5 py-7 lg:flex">
      <Brand />

      <nav aria-label="Apartados" className="flex flex-col gap-1.5">
        <span className="px-3 pb-1.5 text-[11px] font-semibold tracking-[0.12em] text-faint uppercase">Apartados</span>
        {SECTIONS.map((s) => {
          const active = s.value === scope
          const m = meta[s.value]
          return (
            <button
              key={s.value}
              type="button"
              onClick={() => onScope(s.value)}
              aria-current={active ? 'page' : undefined}
              className={`flex min-h-12 items-center gap-3 rounded-xl px-3 text-left transition-colors ${
                active ? 'bg-ink-750 text-white shadow-[inset_0_0_0_1px_var(--color-ink-500)]' : 'text-fg-2 hover:bg-ink-850 hover:text-fg'
              }`}
            >
              <Icon name={s.icon} />
              <span className={`grow text-[15px] ${active ? 'font-semibold' : 'font-medium'}`}>{s.label}</span>
              {active ? (
                <span className={`size-2 rounded-full ${isNba(s.value) ? 'bg-sky' : 'bg-lime'}`} />
              ) : (
                m && <span className={`text-xs text-faint ${m.mono ? 'font-mono' : ''}`}>{m.text}</span>
              )}
            </button>
          )
        })}
      </nav>

      <section aria-label="Consultas de hoy" className="flex flex-col gap-3.5 rounded-2xl border border-ink-600 bg-ink-850 p-4">
        <span className="text-[11px] font-semibold tracking-[0.12em] text-faint uppercase">Consultas de hoy</span>
        {status ? (
          <>
            <QuotaMeter label="Fútbol" used={status.quota.used} limit={status.quota.limit} tone="bg-lime" />
            <QuotaMeter label="Baloncesto" used={status.quota_basketball.used} limit={status.quota_basketball.limit} tone="bg-sky" />
            <span className="flex items-center gap-2 text-xs text-muted">
              <Icon name="clock" size={14} />
              Vuelven a las {resetTime(status)}
            </span>
          </>
        ) : (
          <span className="text-xs text-muted">Sin conexión con el backend.</span>
        )}
      </section>

      <div className="grow" />
      <button
        type="button"
        onClick={onReload}
        className="flex min-h-12 items-center justify-center gap-2.5 rounded-xl border border-ink-500 text-sm font-medium text-fg-2 transition-colors hover:bg-ink-850 hover:text-fg"
      >
        <Icon name="refresh" size={16} />
        Recargar · no gasta consultas
      </button>
    </aside>
  )
}

/** Barra superior (celular y tableta): marca, consultas del apartado y apartados. */
export function MobileTopBar({ scope, onScope, status, onReload }: {
  scope: Scope
  onScope: (s: Scope) => void
  status: ActionStatus | null
  onReload: () => void
}) {
  const q = status ? (isNba(scope) ? status.quota_basketball : status.quota) : null
  const left = q ? q.limit - q.used : 1
  return (
    <div className="flex flex-col gap-4 px-4 pt-5 sm:px-8 lg:hidden">
      <div className="flex items-center justify-between gap-3">
        <Brand compact />
        <div className="flex items-center gap-2">
          {q && (
            <span
              className={`flex min-h-11 items-center gap-2 rounded-xl border border-ink-600 bg-ink-850 px-3 font-mono text-[13px] ${left <= 0 ? 'text-loss' : 'text-fg-2'}`}
              title={`Consultas de ${isNba(scope) ? 'baloncesto' : 'fútbol'} usadas hoy`}
            >
              <span className={`size-2 rounded-full ${left <= 0 ? 'bg-loss' : isNba(scope) ? 'bg-sky' : 'bg-lime'}`} />
              {q.used}/{q.limit}
            </span>
          )}
          <button
            type="button"
            onClick={onReload}
            aria-label="Recargar (no gasta consultas)"
            className="flex size-11 items-center justify-center rounded-xl border border-ink-600 bg-ink-850 text-fg-2"
          >
            <Icon name="refresh" size={18} />
          </button>
        </div>
      </div>
      <nav aria-label="Apartados" className="grid grid-cols-4 gap-1 rounded-2xl border border-ink-600 bg-ink-850 p-1">
        {SECTIONS.map((s) => {
          const active = s.value === scope
          return (
            <button
              key={s.value}
              type="button"
              onClick={() => onScope(s.value)}
              aria-current={active ? 'page' : undefined}
              className={`min-h-11 rounded-xl text-sm transition-colors ${active ? 'bg-fg font-semibold text-ink-950' : 'font-medium text-muted hover:text-fg'}`}
            >
              {s.short}
            </button>
          )
        })}
      </nav>
    </div>
  )
}
