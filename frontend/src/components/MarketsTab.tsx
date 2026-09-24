import { useEffect, useState } from 'react'
import { ApiError, getMatchMarkets } from '../api'
import { VALIDATION_LABELS, type MarketRow, type MatchMarkets, type Scope, type Validation } from '../types'
import { formatMarket, formatSelection } from './MarketLabel'

const pct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`

const VALIDATION_STYLE: Record<Validation, string> = {
  VALIDADO: 'border-win/40 text-win',
  SIN_VALIDAR: 'border-ink-500 text-fg-2',
  SIN_VENTAJA: 'border-loss-line text-loss',
}

function ValidationBadge({ v }: { v: Validation }) {
  const { label, hint } = VALIDATION_LABELS[v]
  return (
    <span title={hint} className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-semibold ${VALIDATION_STYLE[v]}`}>
      {label}
    </span>
  )
}

function EvCell({ row }: { row: MarketRow }) {
  const positive = row.expected_value > 0
  const color = row.ev_suspicious ? 'text-warn' : positive ? 'text-lime' : 'text-faint'
  return (
    <span
      className={`font-mono font-semibold ${color}`}
      title={row.ev_suspicious ? 'EV demasiado alto para ser real: casi siempre es el modelo equivocándose, no la casa.' : undefined}
    >
      {positive ? '+' : ''}
      {pct(row.expected_value)}
      {row.ev_suspicious && ' (!)'}
    </span>
  )
}

export function MarketsTab({ matchId, scope = 'europa' }: { matchId: number; scope?: Scope }) {
  const [state, setState] = useState<
    { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: MatchMarkets }
  >({ status: 'loading' })
  const [onlyRange, setOnlyRange] = useState(true)

  useEffect(() => {
    setState({ status: 'loading' })
    getMatchMarkets(matchId, scope)
      .then((data) => setState({ status: 'ready', data }))
      .catch((err) => setState({ status: 'error', message: err instanceof ApiError ? err.message : 'No se pudieron cargar los mercados' }))
  }, [matchId, scope])

  if (state.status === 'loading') return <p className="py-10 text-center text-sm text-muted">Evaluando mercados...</p>
  if (state.status === 'error') return <p className="py-10 text-center text-sm text-loss">{state.message}</p>

  const { data } = state
  const teams = { home: data.home_team, away: data.away_team }
  const rows = onlyRange ? data.rows.filter((r) => r.in_odds_range) : data.rows
  // El backend ya ordena: EV real primero, luego credibilidad. Si lo primero no
  // llega al piso de EV, no hay nada favorable — y hay que decirlo, no destacar
  // una apuesta perdedora por ser "la menos mala".
  const MIN_EV = 0.02
  const best = rows[0] && rows[0].expected_value >= MIN_EV && !rows[0].ev_suspicious ? rows[0] : undefined

  return (
    <div className="flex flex-col gap-5">
      {!best && rows.length > 0 && (
        <section className="rounded-2xl border border-ink-600 bg-ink-850 p-5">
          <span className="text-xs font-semibold tracking-[0.12em] text-faint uppercase">Lo más favorable</span>
          <p className="mt-1.5 mb-0 font-display text-xl font-bold">Nada, hoy no.</p>
          <p className="mt-1.5 mb-0 text-sm leading-relaxed text-muted">
            Ningún mercado en rango tiene un EV de al menos {(MIN_EV * 100).toFixed(0)}%: el modelo y las casas están de acuerdo en
            este partido. El mejor era {(rows[0].expected_value * 100).toFixed(1)}%. No apostar también es una decisión.
          </p>
        </section>
      )}
      {best && (
        <section
          className={`rounded-2xl border p-5 ${
            best.validation === 'VALIDADO' && best.expected_value > 0 && !best.ev_suspicious ? 'border-lime/40 bg-lime/5' : 'border-ink-600 bg-ink-850'
          }`}
        >
          <span className="text-xs font-semibold tracking-[0.12em] text-faint uppercase">Lo más favorable</span>
          <p className="mt-1.5 mb-0 font-display text-xl font-bold">
            {best.player ? `${best.player} · ` : `${formatMarket(best.market)} · `}
            {formatSelection(best.market, best.selection, teams)}
          </p>
          <p className="mt-1.5 mb-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-fg-2">
            <span>
              Cuota <strong className="font-mono">{best.odds.toFixed(2)}</strong> en {best.bookmaker}
            </span>
            <span className="text-faint">·</span>
            <span>
              modelo {pct(best.model_probability)} vs. mercado {pct(best.implied_probability)}
            </span>
            <span className="text-faint">·</span>
            <span>
              EV <EvCell row={best} />
            </span>
            <ValidationBadge v={best.validation} />
          </p>
          {best.validation !== 'VALIDADO' && (
            <p className="mt-2 mb-0 text-xs text-faint">
              Es lo más favorable <em>según el modelo</em>, pero este mercado no está validado contra resultados reales: trátalo como
              análisis, no como recomendación.
            </p>
          )}
        </section>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="m-0 text-xs text-muted">
          {data.rows.length} mercados con cuota · historial {data.home_history} / {data.away_history} partidos
        </p>
        <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-fg-2">
          <input type="checkbox" checked={onlyRange} onChange={(e) => setOnlyRange(e.target.checked)} className="size-4 accent-lime" />
          Solo cuotas 1.50–2.00
        </label>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-faint uppercase">
              <th className="pb-2 font-medium">Mercado</th>
              <th className="pb-2 pl-3 text-right font-medium">Cuota</th>
              <th className="pb-2 pl-3 text-right font-medium">Modelo</th>
              <th className="pb-2 pl-3 text-right font-medium">Mercado</th>
              <th className="pb-2 pl-3 text-right font-medium">EV</th>
              <th className="pb-2 pl-3 font-medium">Validación</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.market}-${r.selection}`} className={`border-t border-ink-700 ${r.is_pick ? 'bg-lime/5' : ''}`}>
                <td className="py-2.5 pr-3">
                  <span className="block">
                    {r.player && <span className="font-semibold">{r.player} · </span>}
                    {formatSelection(r.market, r.selection, teams)}
                  </span>
                  <span className="text-xs text-muted">
                    {formatMarket(r.market)}
                    {r.is_pick && <span className="ml-2 font-mono font-semibold text-lime">PICK</span>}
                  </span>
                </td>
                <td className="py-2.5 pl-3 text-right">
                  <span className="block font-mono">{r.odds.toFixed(2)}</span>
                  <span className="text-xs text-faint">{r.bookmaker}</span>
                </td>
                <td className="py-2.5 pl-3 text-right font-mono text-fg-2">{pct(r.model_probability)}</td>
                <td className="py-2.5 pl-3 text-right font-mono text-fg-2">{pct(r.implied_probability)}</td>
                <td className="py-2.5 pl-3 text-right">
                  <EvCell row={r} />
                </td>
                <td className="py-2.5 pl-3">
                  <ValidationBadge v={r.validation} />
                  {r.historical_skill !== null && (
                    <span className="mt-0.5 block text-[11px] text-faint">
                      {r.historical_skill >= 0 ? '+' : ''}
                      {(r.historical_skill * 100).toFixed(1)} pts · n={r.backtest_n_bets}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="m-0 text-xs leading-relaxed text-faint">
        <strong className="text-muted">Cómo leer esto.</strong> "Modelo" es la probabilidad que asigna el sistema; "Mercado" la implícita en
        la cuota. El EV es la diferencia convertida a ganancia esperada. Pero un EV alto solo vale algo si el mercado está{' '}
        <em>validado</em>: donde el modelo no le gana a las casas, un EV positivo es más probablemente un error del modelo que una
        oportunidad. Los picks del día aplican ese filtro; esta tabla muestra todo para que decidas con la información completa.
      </p>
    </div>
  )
}
