import { useState } from 'react'
import type { ModelVsMarket as ModelVsMarketData, ModelVsMarketFamily } from '../types'

const FAMILY_LABELS: Record<string, string> = {
  SHOTS: 'Tiros',
  SOT: 'Tiros a puerta',
  FOULS: 'Faltas',
  CARDS: 'Tarjetas',
  CORNERS: 'Córners',
  '1X2': 'Ganador (1X2)',
  GOALS_OU: 'Goles más/menos',
  BTTS: 'Ambos marcan',
  AH: 'Hándicap asiático',
  ML: 'Ganador (NBA)',
  SPREAD: 'Hándicap de puntos',
  TOTAL: 'Total de puntos',
}

const VERDICTS: Record<string, { label: string; className: string }> = {
  MODELO: { label: 'El modelo acierta más', className: 'border-win/40 text-win' },
  CASA: { label: 'La casa acierta más', className: 'border-loss-line text-loss' },
  EMPATE: { label: 'Sin diferencia clara', className: 'border-ink-500 text-fg-2' },
  POCA_MUESTRA: { label: 'Poca muestra', className: 'border-ink-600 text-faint' },
}

// Primero lo que ya tiene veredicto: es lo que decide qué mercados siguen.
const ORDER: Record<string, number> = { CASA: 0, MODELO: 1, EMPATE: 2, POCA_MUESTRA: 3 }

function Family({ f }: { f: ModelVsMarketFamily }) {
  const v = VERDICTS[f.verdict] ?? VERDICTS.POCA_MUESTRA
  return (
    <li className="flex flex-col gap-2.5 rounded-2xl bg-ink-800 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[15px] font-semibold">{FAMILY_LABELS[f.family] ?? f.family}</span>
        <span className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${v.className}`}>{v.label}</span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted">
        <span>
          <span className="font-mono text-fg">{f.n_matches}</span> partidos
        </span>
        {f.t_stat !== null && (
          <span>
            t = <span className="font-mono text-fg">{f.t_stat.toFixed(2)}</span>
          </span>
        )}
        <span title="Brier: error cuadrático, menos es mejor">
          error <span className="font-mono text-fg">{f.brier_model.toFixed(3)}</span> vs{' '}
          <span className="font-mono text-fg">{f.brier_market.toFixed(3)}</span>
        </span>
        {f.value_bets > 0 && (
          <span title="Todas las apuestas que el sistema consideraba con ventaja, a 1 unidad">
            "valor": {f.value_bets_won}/{f.value_bets} ·{' '}
            <span className={`font-mono ${f.value_bets_pnl >= 0 ? 'text-win' : 'text-loss'}`}>
              {f.value_bets_pnl >= 0 ? '+' : '−'}
              {Math.abs(f.value_bets_pnl).toFixed(2)} u
            </span>
          </span>
        )}
      </div>
    </li>
  )
}

/** ¿El modelo sabe algo que la casa no sabe? Se mide sobre TODOS los mercados
 *  evaluados y liquidados, no solo los picks. Solo hay veredicto con 30+
 *  partidos y una diferencia de al menos 2 errores estándar. */
export function ModelVsMarket({ data }: { data: ModelVsMarketData }) {
  const [all, setAll] = useState(false)
  if (data.settled === 0) return null
  const families = [...data.families].sort((a, b) => (ORDER[a.verdict] ?? 9) - (ORDER[b.verdict] ?? 9) || b.n_matches - a.n_matches)
  const shown = all ? families : families.slice(0, 3)

  return (
    <section aria-label="Modelo contra la casa" className="flex flex-col gap-5 rounded-3xl border border-ink-600 bg-ink-850 p-6">
      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-semibold tracking-[0.12em] text-faint uppercase">Modelo contra la casa</span>
        <span className="text-sm text-muted">
          {data.settled.toLocaleString('es-CO')} mercados liquidados de {data.evaluated.toLocaleString('es-CO')} evaluados
        </span>
      </div>
      <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
        {shown.map((f) => (
          <Family key={f.family} f={f} />
        ))}
      </ul>
      <p className="m-0 text-[13px] leading-relaxed text-pretty text-faint">
        Error del modelo contra la probabilidad de las casas sin su margen. Hay veredicto con 30 partidos liquidados y una
        diferencia de al menos 2 errores estándar.
      </p>
      {families.length > 3 && (
        <button
          type="button"
          onClick={() => setAll((a) => !a)}
          className="self-start text-sm text-fg-2 underline underline-offset-4 hover:text-fg"
        >
          {all ? 'Ver menos' : `Ver las ${families.length} familias`}
        </button>
      )}
    </section>
  )
}
