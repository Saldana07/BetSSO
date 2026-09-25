import { isNba as isNbaScope, resetTime } from '../format'
import type { ActionEstimates, ActionName, ActionStatus, Scope } from '../types'
import type { Actions } from '../useActions'
import { Icon, type IconName } from './Icon'

type Button = { action: ActionName; label: string; icon: IconName; help: string; primary?: boolean }

const FOOTBALL: Button[] = [
  { action: 'verificar', label: 'Verificar resultados', icon: 'shield', help: 'Trae el resultado y las estadísticas de los partidos con picks y los liquida.' },
  { action: 'cuotas', label: 'Refrescar cuotas', icon: 'trend', help: 'Cuotas nuevas de los partidos de las próximas 12 h (las de más de 3 h) y rehace sus picks.' },
  { action: 'actualizar', label: 'Actualizar el día', icon: 'download', help: 'Todo de una vez: resultados de los picks pendientes, partidos y cuotas de hoy y mañana, sus picks y la NBA. Se hace sola una vez al día: la primera vez que abres la página desde las 6:00 a. m.' },
  { action: 'completar', label: 'Completar datos', icon: 'layers', help: 'Con lo que sobre de la cuota (deja reserva): resultados para "Modelo contra la casa" e historial colombiano.' },
]

const NBA: Button[] = [
  { action: 'nba', label: 'Actualizar NBA', icon: 'refresh', help: 'Calendario, resultados, líneas de ESPN, picks y liquidación. Gratis (nba.com y ESPN). Ya va dentro de la actualización del día; esto es para repetirla a mano.' },
  { action: 'nba_cuotas', label: 'Cuotas Betano / Pinnacle', icon: 'trend', primary: true, help: 'Lo mismo, más las cuotas de Betano, Pinnacle, Bet365... de los partidos de las próximas 36 h. Usa la cuota de la API de baloncesto (100 diarias, aparte de fútbol).' },
]

function cost(action: ActionName, est: ActionEstimates | null): string {
  if (!est) return ' '
  const n = est[action].requests
  if (action === 'completar') return n > 0 ? `hasta ${n} consultas` : 'hoy no sobra cuota'
  if (action === 'nba') return 'gratis'
  if (action === 'nba_cuotas') return `API de baloncesto · ${n} ${n === 1 ? 'consulta' : 'consultas'}`
  const prefix = action === 'cuotas' ? 'próximas 12 h · ' : action === 'actualizar' ? '≈' : ''
  return `${prefix}${n} ${n === 1 ? 'consulta' : 'consultas'}`
}

function timeAgo(iso: string): string {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'hace un momento'
  if (min < 60) return `hace ${min} min`
  const h = Math.round(min / 60)
  return h < 24 ? `hace ${h} h` : new Date(iso).toLocaleString('es-CO', { dateStyle: 'short', timeStyle: 'short' })
}

function localTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' })
}

/** Qué se hace solo y cuándo: nada más que esto gasta consultas sin un clic. */
function AutoNote({ status }: { status: ActionStatus }) {
  const hour = new Date(2000, 0, 1, status.auto_update_hour).toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' })
  const daily = status.update_due
    ? `La actualización del día se hace sola al abrir la página (una vez al día, desde las ${hour}).`
    : 'La actualización del día ya se hizo: hasta la renovación de la cuota solo se relee lo guardado.'
  const verify = !status.auto_verify_at
    ? ''
    : status.auto_verify_done
      ? ' La verificación automática de hoy ya se miró.'
      : ` A las ${localTime(status.auto_verify_at)}, si quedan consultas y hay partidos jugados sin resultado, se verifican solos (con el backend encendido).`
  return <p className="m-0 text-xs text-faint text-pretty">{daily + verify}</p>
}

/** Los botones que gastan consultas, cada uno con lo que costaría ahora. */
export function ActionBar({ scope, actions }: { scope: Scope; actions: Actions }) {
  const { status, estimates, error, autoStarted, latest, run } = actions
  const isNba = isNbaScope(scope)
  const buttons = isNba ? NBA : FOOTBALL
  const running = status?.running ?? null
  const q = status ? (isNba ? status.quota_basketball : status.quota) : null
  const exhausted = q !== null && q.used >= q.limit

  if (!status) {
    return error ? (
      <div className="flex items-center gap-3 rounded-2xl border border-ink-600 bg-ink-850 px-5 py-4 text-sm text-muted">
        <Icon name="alert" />
        {error}
      </div>
    ) : null
  }

  return (
    <section aria-label="Acciones" className="flex flex-col gap-3">
      <div className={`grid grid-cols-2 gap-3 ${isNba ? 'lg:max-w-2xl' : 'xl:grid-cols-4'}`}>
        {buttons.map(({ action, label, icon, help, primary }) => {
          const noBudget = action === 'completar' && estimates?.completar.requests === 0
          const isThis = running?.action === action
          const disabled = !!running || noBudget
          return (
            <button
              key={action}
              type="button"
              onClick={() => run(action)}
              disabled={disabled}
              title={help}
              className={`flex min-h-15 items-center gap-3 rounded-2xl px-4 py-3 text-left transition-colors disabled:cursor-not-allowed ${
                isThis
                  ? 'border border-lime/60 bg-lime/10 text-fg'
                  : noBudget
                    ? 'border border-dashed border-ink-500 bg-ink-900 text-faint'
                    : primary
                      ? 'border border-sky bg-sky text-ink-950 hover:brightness-110 disabled:opacity-50'
                      : 'border border-ink-600 bg-ink-850 text-fg hover:border-ink-400 hover:bg-ink-800 disabled:opacity-50'
              }`}
            >
              <Icon name={isThis ? 'refresh' : icon} size={20} className={isThis ? 'animate-spin text-lime' : ''} />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="text-sm font-semibold">{label}</span>
                <span className={`text-xs ${primary && !isThis && !noBudget ? 'text-[#1c3448]' : noBudget ? '' : 'text-muted'}`}>
                  {cost(action, estimates)}
                </span>
              </span>
            </button>
          )
        })}
      </div>

      {running && (
        <p role="status" className="flex items-center gap-2.5 text-sm text-lime">
          <span className="size-2 animate-pulse rounded-full bg-lime" />
          {autoStarted && running.action === 'actualizar' && 'Automática, una vez al día: '}
          {running.title}
          {running.step_index > 0 && ` · paso ${running.step_index} de ${running.steps}: ${running.step}`}
        </p>
      )}

      {!running && latest && (
        <details className="text-sm">
          <summary className="cursor-pointer text-fg-2 marker:text-faint">
            <span className={latest[1].ok ? 'text-win' : 'text-loss'}>{latest[1].ok ? '✓' : '✗'}</span>{' '}
            {status.titles[latest[0]]} · {timeAgo(latest[1].finished_at)}
            {latest[1].spent !== null && ` · gastó ${latest[1].spent} ${latest[1].spent === 1 ? 'consulta' : 'consultas'}`}
          </summary>
          <ul className="mt-2 flex flex-col gap-1 border-l border-ink-600 pl-4 text-xs text-muted">
            {latest[1].steps.map((s) => (
              <li key={s.label}>
                <span className={s.ok ? 'text-win' : 'text-loss'}>{s.ok ? '✓' : '✗'}</span> {s.label}
                {s.summary && <span className="text-faint"> — {s.summary}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {!running && <AutoNote status={status} />}

      {exhausted && !running && (
        <p className="text-xs text-warn-fg">
          {isNba
            ? `Sin consultas de baloncesto hasta las ${resetTime(status)}: «Actualizar NBA» es gratis y sigue funcionando.`
            : `Sin consultas hasta las ${resetTime(status)}: lo que ya está en la base de datos se sigue viendo con normalidad.`}
        </p>
      )}
      {error && <p className="text-xs text-loss">{error}</p>}
    </section>
  )
}
