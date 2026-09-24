import { useEffect, useState } from 'react'
import { getNbaInjuries, type NbaInjuries as Injuries, type NbaInjury } from '../api'
import { Icon } from './Icon'

function TeamList({ team, rows }: { team: string; rows: NbaInjury[] }) {
  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-ink-600 bg-ink-850 p-5">
      <span className="font-display text-lg font-bold">{team}</span>
      {rows.length === 0 ? (
        <span className="text-sm text-muted">Sin bajas reportadas.</span>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {rows.map((r) => (
            <li key={r.player} className="flex flex-col gap-1 text-sm">
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{r.player}</span>
                <span
                  className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${r.out ? 'border-loss-line text-loss' : 'border-warn-line text-warn'}`}
                >
                  {r.status}
                </span>
              </span>
              {r.comment && <span className="text-xs leading-relaxed text-muted">{r.comment}</span>}
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

  if (state.status === 'loading') return <p className="py-10 text-center text-sm text-muted">Leyendo el parte de lesiones...</p>
  if (state.status === 'error') return <p className="py-10 text-center text-sm text-loss">No se pudo leer el parte de lesiones de ESPN.</p>

  const { data } = state
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 flex items-start gap-3 rounded-2xl border border-sky-line bg-sky-bg px-4 py-3 text-[13px] leading-relaxed text-sky-fg">
        <Icon name="bandage" size={18} className="mt-0.5" />
        <span>
          En la NBA una baja de última hora mueve mucho la línea: si un titular sale como "Fuera" después de tomar la cuota, ese precio
          ya no vale lo mismo. Fuente: ESPN (comentarios en inglés)
          {data.updated_at && `, leído a las ${new Date(data.updated_at).toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' })}`}.
        </span>
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <TeamList team={data.home_team} rows={data.home} />
        <TeamList team={data.away_team} rows={data.away} />
      </div>
    </div>
  )
}
