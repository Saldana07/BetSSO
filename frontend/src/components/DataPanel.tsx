import { useCallback, useEffect, useRef, useState } from 'react'
import { getActionEstimates, getActionStatus, launchAction } from '../api'
import type { ActionEstimates, ActionName, ActionResult, ActionStatus, Scope } from '../types'

const POLL_MS = 2500
/** Por encima de esto se pide confirmación antes de gastar. */
const CONFIRM_ABOVE = 15

type Button = { action: ActionName; label: string; help: string }

const FOOTBALL_BUTTONS: Button[] = [
  {
    action: 'verificar',
    label: 'Verificar resultados',
    help: 'Trae el resultado y las estadísticas de los partidos con picks y los liquida.',
  },
  {
    action: 'cuotas',
    label: 'Refrescar cuotas',
    help: 'Cuotas nuevas de los partidos de las próximas 12 h (las de más de 3 h) y rehace sus picks.',
  },
  {
    action: 'actualizar',
    label: 'Actualizar el día',
    help: 'Partidos y cuotas de hoy y mañana y sus picks. Se hace sola la primera vez que abres la página tras renovarse la cuota.',
  },
  {
    action: 'completar',
    label: 'Completar datos',
    help: 'Con lo que sobre de la cuota (deja reserva): resultados para la medición "Modelo vs casa" e historial colombiano.',
  },
]

const NBA_BUTTONS: Button[] = [
  {
    action: 'nba',
    label: 'Actualizar NBA',
    help: 'Calendario, resultados, líneas de ESPN, picks y liquidación. Gratis (nba.com y ESPN): se hace sola al abrir esta pestaña si pasaron 3 horas.',
  },
  {
    action: 'nba_cuotas',
    label: 'Cuotas Betano / Pinnacle',
    help: 'Lo mismo, más las cuotas de Betano, Pinnacle, Bet365... de los partidos de las próximas 36 h. Usa la cuota de la API de baloncesto (100 diarias, aparte de fútbol).',
  },
]

const NBA_ACTIONS: ActionName[] = ['nba', 'nba_cuotas']

function cost(action: ActionName, est: ActionEstimates | null): string {
  if (!est) return ''
  const n = est[action].requests
  if (action === 'completar') return n > 0 ? `hasta ${n}` : 'no sobra'
  if (action === 'nba') return 'gratis'
  return `${action === 'actualizar' ? '≈' : ''}${n} ${n === 1 ? 'consulta' : 'consultas'}`
}

function timeAgo(iso: string): string {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'hace un momento'
  if (min < 60) return `hace ${min} min`
  const h = Math.round(min / 60)
  return h < 24 ? `hace ${h} h` : new Date(iso).toLocaleString('es', { dateStyle: 'short', timeStyle: 'short' })
}

/** Consultas a la API y los botones que las gastan: nada se consulta solo,
 *  salvo la actualización del día la primera vez que se abre la página
 *  tras renovarse la cuota (19:00 en Colombia) y, en la pestaña NBA, su
 *  actualización gratuita si pasaron 3 horas. Cada apartado muestra su cuota:
 *  fútbol y baloncesto tienen 100 diarias cada uno. */
export function DataPanel({ scope, onFinished }: { scope: Scope; onFinished: () => void }) {
  const isNba = scope === 'nba'
  const [status, setStatus] = useState<ActionStatus | null>(null)
  const [estimates, setEstimates] = useState<ActionEstimates | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [autoStarted, setAutoStarted] = useState(false)
  const wasRunning = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const s = await getActionStatus()
      setStatus(s)
      setError(null)
      if (wasRunning.current && !s.running) {
        onFinished()
        getActionEstimates().then(setEstimates).catch(() => undefined)
      }
      wasRunning.current = !!s.running
      return s
    } catch {
      setError('No se pudo leer el estado de las consultas (¿backend apagado?).')
      return null
    }
  }, [onFinished])

  const launch = useCallback(
    async (action: ActionName, auto = false) => {
      try {
        const res = await launchAction(action, auto)
        if (res.started) {
          wasRunning.current = true
          if (auto) setAutoStarted(true)
        }
      } catch (err) {
        // La automática puede chocar con otra pestaña que ya la lanzó: no es un error.
        if (!auto) setError(err instanceof Error ? err.message : 'No se pudo lanzar la acción.')
      }
      await refresh()
    },
    [refresh],
  )

  // Al abrir la página (o la pestaña NBA): estado, estimados y, si toca, la
  // actualización automática de ese apartado.
  useEffect(() => {
    refresh().then((s) => {
      if (!s || s.running) return
      if (isNba ? s.nba_update_due : s.update_due) launch(isNba ? 'nba' : 'actualizar', true)
    })
    getActionEstimates().then(setEstimates).catch(() => undefined)
  }, [refresh, launch, isNba])

  // Solo se consulta el estado mientras algo corre.
  const running = status?.running ?? null
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [running, refresh])

  const onClick = (action: ActionName) => {
    const n = estimates?.[action].requests ?? 0
    if (status && action !== 'completar' && n > CONFIRM_ABOVE) {
      const q = NBA_ACTIONS.includes(action) ? status.quota_basketball : status.quota
      const left = Math.max(0, q.limit - q.used)
      if (!window.confirm(`Esto gastará unas ${n} de las ${left} consultas que te quedan hoy. ¿Seguir?`)) return
    }
    launch(action)
  }

  if (!status) {
    return error ? (
      <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        {error}
      </div>
    ) : null
  }

  const { resets_at } = status.quota
  const { used, limit, breakdown } = isNba ? status.quota_basketball : status.quota
  const buttons = isNba ? NBA_BUTTONS : FOOTBALL_BUTTONS
  const left = Math.max(0, limit - used)
  const resets = new Date(resets_at).toLocaleTimeString('es', { hour: 'numeric', minute: '2-digit' })
  const pct = Math.min(100, (used / limit) * 100)
  const barColor = left === 0 ? 'bg-red-500' : left < 20 ? 'bg-amber-500' : 'bg-emerald-500'

  // La última acción de ESTE apartado (fútbol o NBA).
  const latest = (Object.entries(status.last) as [ActionName, ActionResult][])
    .filter(([a]) => NBA_ACTIONS.includes(a) === isNba)
    .sort((a, b) => b[1].finished_at.localeCompare(a[1].finished_at))[0]

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-900">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          {isNba ? 'Consultas a la API de baloncesto' : 'Consultas a la API'}
        </span>
        <span className="text-xs text-slate-400 dark:text-slate-500">se renuevan a las {resets}</span>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
          <div className={`h-full ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
        <span className="shrink-0 whitespace-nowrap text-sm tabular-nums text-slate-700 dark:text-slate-300">
          {used} / {limit} usadas hoy
        </span>
      </div>

      <div className={`mt-3 grid grid-cols-2 gap-2 ${isNba ? '' : 'sm:grid-cols-4'}`}>
        {buttons.map(({ action, label, help }) => {
          const noBudget = action === 'completar' && estimates?.completar.requests === 0
          const isThis = running?.action === action
          return (
            <button
              key={action}
              onClick={() => onClick(action)}
              disabled={!!running || noBudget}
              title={help}
              className={`rounded-lg border px-3 py-2 text-left transition-colors disabled:cursor-not-allowed ${
                isThis
                  ? 'border-indigo-400 bg-indigo-50 dark:border-indigo-500 dark:bg-indigo-950/40'
                  : 'border-slate-300 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-800'
              }`}
            >
              <span className="block text-sm font-medium text-slate-800 dark:text-slate-100">{label}</span>
              <span className="block text-xs text-slate-500 dark:text-slate-400">{cost(action, estimates)}</span>
            </button>
          )
        })}
      </div>

      {running && (
        <p className="mt-3 flex items-center gap-2 text-sm text-indigo-700 dark:text-indigo-300" role="status">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-indigo-500" />
          {autoStarted && running.action === 'actualizar' ? 'Primera visita desde que se renovó la cuota: ' : ''}
          {autoStarted && running.action === 'nba' ? 'Actualización automática (gratis): ' : ''}
          {running.title}
          {running.step_index > 0 && ` · paso ${running.step_index} de ${running.steps}: ${running.step}`}
        </p>
      )}

      {!running && latest && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-slate-600 dark:text-slate-300">
            <span className={latest[1].ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
              {latest[1].ok ? '✓' : '✗'}
            </span>{' '}
            {status.titles[latest[0]]} · {timeAgo(latest[1].finished_at)}
            {latest[1].spent !== null && ` · gastó ${latest[1].spent} ${latest[1].spent === 1 ? 'consulta' : 'consultas'}`}
          </summary>
          <ul className="mt-2 space-y-1 text-xs text-slate-500 dark:text-slate-400">
            {latest[1].steps.map((s) => (
              <li key={s.label}>
                <span className={s.ok ? '' : 'text-red-600 dark:text-red-400'}>{s.ok ? '✓' : '✗'}</span> {s.label}
                {s.summary && <span className="text-slate-400 dark:text-slate-500"> — {s.summary}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {left === 0 && !running && (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
          {isNba
            ? `Sin consultas de baloncesto hasta las ${resets}. "Actualizar NBA" es gratis y sigue funcionando.`
            : `Sin consultas hasta las ${resets}. Lo que ya está en la base de datos se sigue viendo con normalidad.`}
        </p>
      )}
      {Object.keys(breakdown).length > 0 && (
        <details className="mt-2 text-xs text-slate-400 dark:text-slate-500">
          <summary className="cursor-pointer">¿En qué se gastaron?</summary>
          <ul className="mt-1 space-y-0.5">
            {Object.entries(breakdown).map(([k, n]) => (
              <li key={k}>
                {n} · {k}
              </li>
            ))}
          </ul>
        </details>
      )}
      {error && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  )
}
