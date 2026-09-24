import { useEffect, useState } from 'react'
import { ApiError, getSimulation } from '../api'
import { OUTCOME_LABELS, type OutcomeProbs, type Simulation } from '../types'

// Una sola serie -> una sola tonalidad: el acento del tablero.
const BAR = 'bg-lime'
const BAR_SOFT = 'bg-lime/20'

const pct = (v: number, d = 0) => `${(v * 100).toFixed(d)}%`

function OutcomeBars({ sim }: { sim: Simulation }) {
  const rows: { key: keyof OutcomeProbs; ci: [number, number] }[] = [
    { key: 'HOME', ci: sim.ci_home },
    { key: 'DRAW', ci: sim.ci_draw },
    { key: 'AWAY', ci: sim.ci_away },
  ]
  return (
    <div className="space-y-3">
      {rows.map(({ key, ci }) => {
        const p = sim.monte_carlo[key]
        return (
          <div key={key} className="grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-3 text-sm">
            <span className="text-fg-2">{OUTCOME_LABELS[key]}</span>
            <div
              className="relative h-3 rounded bg-ink-700"
              title={`Intervalo 90%: ${pct(ci[0])} – ${pct(ci[1])}`}
            >
              {/* rango de incertidumbre (IC 90%) */}
              <div
                className={`absolute inset-y-0 rounded ${BAR_SOFT}`}
                style={{ left: pct(ci[0]), width: pct(ci[1] - ci[0]) }}
              />
              {/* estimación puntual: marca fina con extremo redondeado */}
              <div className={`absolute inset-y-0 left-0 rounded ${BAR}`} style={{ width: pct(p) }} />
            </div>
            <span className="text-right font-mono font-semibold">
              {pct(p, 1)}
            </span>
          </div>
        )
      })}
      <p className="text-xs text-faint">
        La franja clara es el intervalo al 90%: cuánto podría moverse cada porcentaje por lo poco o mucho
        que sabemos de los equipos ({sim.home_history} y {sim.away_history} partidos de historial).
      </p>
    </div>
  )
}

function GoalsChart({ dist }: { dist: Record<string, number> }) {
  const entries = Object.entries(dist).map(([g, p]) => [Number(g), p] as const)
  const max = Math.max(...entries.map(([, p]) => p))
  const top = entries.reduce((a, b) => (b[1] > a[1] ? b : a))
  return (
    <div>
      {/* Las columnas deben tener altura definida (h-full dentro de h-36) para que
          la altura en % de cada barra tenga contra qué resolverse. */}
      <div className="flex h-36 items-stretch gap-0.5">
        {entries.map(([g, p]) => (
          <div key={g} className="group flex h-full flex-1 flex-col items-center justify-end" title={`${g === 7 ? '7+' : g} goles: ${pct(p, 1)}`}>
            {/* etiqueta directa solo en el valor más alto; el resto por hover */}
            <span
              className={`mb-1 text-[11px] font-mono text-muted ${g === top[0] ? '' : 'invisible group-hover:visible'}`}
            >
              {pct(p)}
            </span>
            <div className={`w-full rounded-t ${BAR}`} style={{ height: `${(p / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-0.5 border-t border-ink-600 pt-1">
        {entries.map(([g]) => (
          <span key={g} className="flex-1 text-center text-[11px] text-muted">
            {g === 7 ? '7+' : g}
          </span>
        ))}
      </div>
      <p className="mt-1 text-center text-xs text-faint">Goles totales en el partido</p>
    </div>
  )
}

function ComparisonTable({ sim }: { sim: Simulation }) {
  const keys: (keyof OutcomeProbs)[] = ['HOME', 'DRAW', 'AWAY']
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-faint">
            <th className="pb-2 font-medium">Resultado</th>
            <th className="pb-2 text-right font-medium">Montecarlo</th>
            <th className="pb-2 text-right font-medium">Clasificador</th>
            <th className="pb-2 text-right font-medium">Mercado</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const isValue = sim.value_candidates.some((c) => c.outcome === k)
            return (
              <tr key={k} className="border-t border-ink-700">
                <td className="py-2 text-fg-2">
                  {OUTCOME_LABELS[k]}
                  {isValue && (
                    <span className="ml-2 rounded-md bg-lime px-1.5 py-0.5 font-mono text-[10px] font-semibold text-ink-950">
                      VALOR
                    </span>
                  )}
                </td>
                <td className="py-2 text-right font-mono font-semibold">{pct(sim.monte_carlo[k], 1)}</td>
                <td className="py-2 text-right font-mono text-fg-2">{pct(sim.classifier[k], 1)}</td>
                <td className="py-2 text-right font-mono text-fg-2">
                  {sim.market_fair ? pct(sim.market_fair[k], 1) : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-faint">
        Mercado = probabilidad implícita de la mejor cuota, sin el margen de la casa. Desacuerdo entre
        modelos: {pct(sim.model_disagreement, 1)}.
      </p>
    </div>
  )
}

export function SimulationTab({ matchId }: { matchId: number }) {
  const [state, setState] = useState<{ status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; sim: Simulation }>({
    status: 'loading',
  })

  useEffect(() => {
    setState({ status: 'loading' })
    getSimulation(matchId)
      .then((sim) => setState({ status: 'ready', sim }))
      .catch((err) => setState({ status: 'error', message: err instanceof ApiError ? err.message : 'No se pudo cargar la simulación' }))
  }, [matchId])

  if (state.status === 'loading') {
    return <p className="py-10 text-center text-sm text-muted">Simulando 10,000 partidos...</p>
  }
  if (state.status === 'error') {
    return <p className="py-10 text-center text-sm text-loss">{state.message}</p>
  }

  const { sim } = state
  const hasValue = sim.value_candidates.length > 0
  const disagree = sim.model_disagreement > 0.08

  return (
    <div className="space-y-8">
      <section>
        <h4 className="mb-3 text-xs font-semibold tracking-[0.12em] text-faint uppercase">
          Resultado de {sim.n_simulations.toLocaleString('es-CO')} simulaciones
        </h4>
        <OutcomeBars sim={sim} />
      </section>

      <section className="grid gap-8 sm:grid-cols-2">
        <div>
          <h4 className="mb-3 text-xs font-semibold tracking-[0.12em] text-faint uppercase">
            Distribución de goles
          </h4>
          <GoalsChart dist={sim.total_goals_distribution} />
        </div>
        <div>
          <h4 className="mb-3 text-xs font-semibold tracking-[0.12em] text-faint uppercase">
            Marcadores más probables
          </h4>
          <ul className="space-y-1.5 text-sm">
            {sim.top_scorelines.map(([score, p]) => (
              <li key={score} className="flex items-center justify-between">
                <span className="font-mono font-medium">{score}</span>
                <span className="font-mono text-muted">{pct(p, 1)}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-faint">
            Goles esperados {sim.expected_home_goals.toFixed(2)} – {sim.expected_away_goals.toFixed(2)} · Más de 2.5:{' '}
            {pct(sim.p_over_2_5)} · Ambos marcan: {pct(sim.p_btts)}
          </p>
        </div>
      </section>

      <section>
        <h4 className="mb-3 text-xs font-semibold tracking-[0.12em] text-faint uppercase">
          Montecarlo vs. modelo base vs. mercado
        </h4>
        <ComparisonTable sim={sim} />
      </section>

      <section
        className={`rounded-2xl border p-4 text-sm leading-relaxed ${
          hasValue
            ? 'border-lime/40 bg-lime/5 text-fg'
            : disagree
              ? 'border-warn-line bg-warn-bg text-warn-fg'
              : 'border-ink-600 bg-ink-850 text-fg-2'
        }`}
      >
        <p className="mb-1 text-xs font-semibold uppercase tracking-wide opacity-70">Conclusión</p>
        {sim.conclusion}
      </section>
    </div>
  )
}
