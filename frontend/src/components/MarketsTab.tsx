import { useEffect, useState } from 'react'
import { ApiError, getMatchMarkets } from '../api'
import { VALIDATION_LABELS, type MarketRow, type MatchMarkets, type Validation } from '../types'
import { formatMarket, formatSelection } from './MarketLabel'

const pct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`

const VALIDATION_STYLE: Record<Validation, string> = {
  VALIDADO: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  SIN_VALIDAR: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  SIN_VENTAJA: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
}

function ValidationBadge({ v }: { v: Validation }) {
  const { label, hint } = VALIDATION_LABELS[v]
  return (
    <span title={hint} className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${VALIDATION_STYLE[v]}`}>
      {label}
    </span>
  )
}

function EvCell({ row }: { row: MarketRow }) {
  const positive = row.expected_value > 0
  const color = row.ev_suspicious
    ? 'text-amber-600 dark:text-amber-400'
    : positive
      ? 'text-emerald-600 dark:text-emerald-400'
      : 'text-slate-500 dark:text-slate-400'
  return (
    <span
      className={`font-semibold tabular-nums ${color}`}
      title={row.ev_suspicious ? 'EV demasiado alto para ser real: casi siempre es el modelo equivocándose, no la casa.' : undefined}
    >
      {positive ? '+' : ''}
      {pct(row.expected_value)}
      {row.ev_suspicious && ' ⚠'}
    </span>
  )
}

export function MarketsTab({ matchId }: { matchId: number }) {
  const [state, setState] = useState<
    { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: MatchMarkets }
  >({ status: 'loading' })
  const [onlyRange, setOnlyRange] = useState(true)

  useEffect(() => {
    setState({ status: 'loading' })
    getMatchMarkets(matchId)
      .then((data) => setState({ status: 'ready', data }))
      .catch((err) => setState({ status: 'error', message: err instanceof ApiError ? err.message : 'No se pudieron cargar los mercados' }))
  }, [matchId])

  if (state.status === 'loading') return <p className="py-10 text-center text-sm text-slate-500 dark:text-slate-400">Evaluando mercados...</p>
  if (state.status === 'error') return <p className="py-10 text-center text-sm text-red-600 dark:text-red-400">{state.message}</p>

  const { data } = state
  const rows = onlyRange ? data.rows.filter((r) => r.in_odds_range) : data.rows
  const best = rows[0]

  return (
    <div className="space-y-5">
      {best && (
        <section
          className={`rounded-xl border p-4 ${
            best.validation === 'VALIDADO' && best.expected_value > 0 && !best.ev_suspicious
              ? 'border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40'
              : 'border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/60'
          }`}
        >
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Lo más favorable</p>
          <p className="mt-1 text-lg font-bold text-slate-900 dark:text-slate-100">
            {formatMarket(best.market)} · {formatSelection(best.market, best.selection)}
          </p>
          <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">
            Cuota <strong>{best.odds.toFixed(2)}</strong> en {best.bookmaker} · modelo {pct(best.model_probability)} vs. mercado{' '}
            {pct(best.implied_probability)} · EV <EvCell row={best} /> · <ValidationBadge v={best.validation} />
          </p>
          {best.validation !== 'VALIDADO' && (
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              Es lo más favorable <em>según el modelo</em>, pero este mercado no está validado contra resultados reales:
              trátalo como análisis, no como recomendación.
            </p>
          )}
        </section>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {data.rows.length} mercados con cuota · historial {data.home_history} / {data.away_history} partidos
        </p>
        <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
          <input type="checkbox" checked={onlyRange} onChange={(e) => setOnlyRange(e.target.checked)} />
          Solo cuotas 1.50–2.00
        </label>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-400 dark:text-slate-500">
              <th className="pb-2 font-medium">Mercado</th>
              <th className="pb-2 text-right font-medium">Cuota</th>
              <th className="pb-2 text-right font-medium">Modelo</th>
              <th className="pb-2 text-right font-medium">Mercado</th>
              <th className="pb-2 text-right font-medium">EV</th>
              <th className="pb-2 pl-3 font-medium">Validación</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={`${r.market}-${r.selection}`}
                className={`border-t border-slate-100 dark:border-slate-800 ${r.is_pick ? 'bg-indigo-50/60 dark:bg-indigo-950/30' : ''}`}
              >
                <td className="py-2 pr-3">
                  <p className="text-slate-900 dark:text-slate-100">{formatSelection(r.market, r.selection)}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {formatMarket(r.market)}
                    {r.is_pick && <span className="ml-2 font-semibold text-indigo-600 dark:text-indigo-400">PICK DEL DÍA</span>}
                  </p>
                </td>
                <td className="py-2 text-right tabular-nums">
                  <p className="text-slate-900 dark:text-slate-100">{r.odds.toFixed(2)}</p>
                  <p className="text-xs text-slate-400 dark:text-slate-500">{r.bookmaker}</p>
                </td>
                <td className="py-2 text-right tabular-nums text-slate-700 dark:text-slate-300">{pct(r.model_probability)}</td>
                <td className="py-2 text-right tabular-nums text-slate-700 dark:text-slate-300">{pct(r.implied_probability)}</td>
                <td className="py-2 text-right"><EvCell row={r} /></td>
                <td className="py-2 pl-3">
                  <ValidationBadge v={r.validation} />
                  {r.historical_skill !== null && (
                    <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">
                      {r.historical_skill >= 0 ? '+' : ''}{(r.historical_skill * 100).toFixed(1)} pts · n={r.backtest_n_bets}
                    </p>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs leading-relaxed text-slate-400 dark:text-slate-500">
        <strong>Cómo leer esto.</strong> "Modelo" es la probabilidad que asigna el sistema; "Mercado" la implícita en la
        cuota. El EV es la diferencia convertida a ganancia esperada. Pero un EV alto solo vale algo si el mercado está{' '}
        <em>validado</em>: el backtest con 28,512 partidos reales demostró que en goles y resultado el modelo{' '}
        <em>no</em> le gana a las casas, así que un EV positivo ahí es más probablemente un error del modelo que una
        oportunidad. Los picks del día aplican ese filtro; esta tabla muestra todo para que decidas con la información completa.
      </p>
    </div>
  )
}
