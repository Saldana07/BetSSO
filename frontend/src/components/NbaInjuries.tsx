import { useEffect, useState } from 'react'
import { getNbaInjuries, type NbaInjuries as Injuries, type NbaInjury } from '../api'

function TeamList({ team, rows }: { team: string; rows: NbaInjury[] }) {
  return (
    <div>
      <p className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-100">{team}</p>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">Sin bajas reportadas.</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.player} className="text-sm">
              <span className="font-medium text-slate-800 dark:text-slate-200">{r.player}</span>{' '}
              <span className={r.out ? 'font-semibold text-red-600 dark:text-red-400' : 'text-amber-700 dark:text-amber-400'}>
                · {r.status}
              </span>
              {r.comment && <p className="text-xs text-slate-500 dark:text-slate-400">{r.comment}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Parte de lesiones de ESPN (gratis) de los dos equipos. */
export function NbaInjuries({ matchId }: { matchId: number }) {
  const [state, setState] = useState<{ status: 'loading' } | { status: 'error' } | { status: 'ready'; data: Injuries }>({
    status: 'loading',
  })

  useEffect(() => {
    getNbaInjuries(matchId)
      .then((data) => setState({ status: 'ready', data }))
      .catch(() => setState({ status: 'error' }))
  }, [matchId])

  if (state.status === 'loading') return <p className="py-10 text-center text-sm text-slate-500 dark:text-slate-400">Leyendo el parte de lesiones...</p>
  if (state.status === 'error') return <p className="py-10 text-center text-sm text-red-600 dark:text-red-400">No se pudo leer el parte de lesiones de ESPN.</p>

  const { data } = state
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        En la NBA una baja de última hora mueve mucho la línea: si un titular sale como "Fuera" después de tomar la cuota,
        ese precio ya no vale lo mismo. Fuente: ESPN (comentarios en inglés)
        {data.updated_at && `, leído a las ${new Date(data.updated_at).toLocaleTimeString('es', { hour: 'numeric', minute: '2-digit' })}`}.
      </p>
      <div className="grid gap-6 sm:grid-cols-2">
        <TeamList team={data.home_team} rows={data.home} />
        <TeamList team={data.away_team} rows={data.away} />
      </div>
    </div>
  )
}
