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
  MODELO: { label: 'El modelo acierta más', className: 'text-emerald-600 dark:text-emerald-400' },
  CASA: { label: 'La casa acierta más', className: 'text-red-600 dark:text-red-400' },
  EMPATE: { label: 'Sin diferencia clara', className: 'text-slate-500 dark:text-slate-400' },
  POCA_MUESTRA: { label: 'Poca muestra', className: 'text-slate-400 dark:text-slate-500' },
}

function Row({ f }: { f: ModelVsMarketFamily }) {
  const v = VERDICTS[f.verdict] ?? VERDICTS.POCA_MUESTRA
  const pnlColor = f.value_bets_pnl >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
  return (
    <tr className="border-t border-slate-100 dark:border-slate-800">
      <td className="py-1.5 pr-3 text-slate-800 dark:text-slate-200">{FAMILY_LABELS[f.family] ?? f.family}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600 dark:text-slate-400">{f.n_matches}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600 dark:text-slate-400">
        {f.brier_model.toFixed(3)} <span className="text-slate-400 dark:text-slate-500">vs</span> {f.brier_market.toFixed(3)}
      </td>
      <td className={`py-1.5 pr-3 text-sm ${v.className}`}>{v.label}</td>
      <td className="py-1.5 text-right tabular-nums">
        {f.value_bets > 0 ? (
          <span className={pnlColor}>
            {f.value_bets_won}/{f.value_bets} · {f.value_bets_pnl >= 0 ? '+' : ''}
            {f.value_bets_pnl.toFixed(2)} u
          </span>
        ) : (
          <span className="text-slate-400 dark:text-slate-500">—</span>
        )}
      </td>
    </tr>
  )
}

/** ¿El modelo sabe algo que la casa no sabe? Se mide sobre TODOS los mercados
 *  evaluados y liquidados, no solo los picks, para tener muestra en semanas y
 *  no en meses. Solo hay veredicto con 30+ partidos y diferencia significativa. */
export function ModelVsMarket({ data }: { data: ModelVsMarketData }) {
  const [open, setOpen] = useState(false)
  if (data.settled === 0) return null

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-900">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-baseline justify-between gap-4 text-left">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Modelo vs casa
        </span>
        <span className="text-xs text-slate-400 dark:text-slate-500">
          {data.settled} mercados liquidados de {data.evaluated} evaluados · {open ? 'ocultar' : 'ver detalle'}
        </span>
      </button>

      {open && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-slate-400 dark:text-slate-500">
                <th className="pb-1 pr-3 font-medium">Mercado</th>
                <th className="pb-1 pr-3 text-right font-medium">Partidos</th>
                <th className="pb-1 pr-3 text-right font-medium">Error modelo vs casa</th>
                <th className="pb-1 pr-3 font-medium">Veredicto</th>
                <th className="pb-1 text-right font-medium">Si apostaras el "valor"</th>
              </tr>
            </thead>
            <tbody>
              {data.families.map((f) => (
                <Row key={f.family} f={f} />
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
            Error = Brier (menor es mejor) del modelo frente a la probabilidad de las casas sin su margen. El veredicto solo
            aparece con 30+ partidos y una diferencia que no sea ruido. "Si apostaras el valor": todas las apuestas que el
            sistema consideraba con ventaja (EV ≥ 2%, cuota 1.50–2.00, mercado validado), a 1 unidad.
          </p>
        </div>
      )}
    </div>
  )
}
