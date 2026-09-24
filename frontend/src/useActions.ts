import { useCallback, useEffect, useRef, useState } from 'react'
import { getActionEstimates, getActionStatus, launchAction } from './api'
import { isNba as isNbaScope } from './format'
import type { ActionEstimates, ActionName, ActionResult, ActionStatus, Scope } from './types'

const POLL_MS = 2500
/** Por encima de esto se pide confirmación antes de gastar. */
const CONFIRM_ABOVE = 15

export const NBA_ACTIONS: ActionName[] = ['nba', 'nba_cuotas']

export interface Actions {
  status: ActionStatus | null
  estimates: ActionEstimates | null
  error: string | null
  autoStarted: boolean
  /** La última acción terminada de ESTE apartado (fútbol o NBA). */
  latest: [ActionName, ActionResult] | undefined
  run: (action: ActionName) => void
}

/** Consultas a la API y las acciones que las gastan. Nada se consulta solo,
 *  salvo la actualización del día la primera vez que se abre la página cada
 *  mañana (desde las 6:00, AUTO_UPDATE_HOUR en backend/.env) y, en la NBA, su
 *  actualización gratuita si pasaron 3 horas. Solo se sondea el estado
 *  mientras algo corre. */
export function useActions(scope: Scope, onFinished: () => void): Actions {
  const isNba = isNbaScope(scope)
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

  useEffect(() => {
    refresh().then((s) => {
      if (!s || s.running) return
      if (isNba ? s.nba_update_due : s.update_due) launch(isNba ? 'nba' : 'actualizar', true)
    })
    getActionEstimates().then(setEstimates).catch(() => undefined)
  }, [refresh, launch, isNba])

  const running = status?.running ?? null
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [running, refresh])

  const run = (action: ActionName) => {
    const n = estimates?.[action].requests ?? 0
    if (status && action !== 'completar' && n > CONFIRM_ABOVE) {
      const q = NBA_ACTIONS.includes(action) ? status.quota_basketball : status.quota
      const left = Math.max(0, q.limit - q.used)
      if (!window.confirm(`Esto gastará unas ${n} de las ${left} consultas que te quedan hoy. ¿Seguir?`)) return
    }
    launch(action)
  }

  const latest = status
    ? (Object.entries(status.last) as [ActionName, ActionResult][])
        .filter(([a]) => NBA_ACTIONS.includes(a) === isNba)
        .sort((a, b) => b[1].finished_at.localeCompare(a[1].finished_at))[0]
    : undefined

  return { status, estimates, error, autoStarted, latest, run }
}
