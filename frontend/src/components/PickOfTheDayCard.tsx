import type { PickOfTheDay } from '../types'
import { ConfidenceBadge } from './ConfidenceBadge'
import { MarketLabel, formatMarket } from './MarketLabel'

/** Nombre de la estadística de un mercado de conteo, para "Final: 7 córners". */
function statName(market: string): string {
  const label = formatMarket(market)
  return label.replace(/^Más\/menos de [\d.]+ /, '')
}

function ResultBanner({ pick }: { pick: PickOfTheDay }) {
  if (pick.is_settled && pick.is_win !== null) {
    const won = pick.is_win
    return (
      <div
        className={`mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl px-4 py-2 text-sm font-medium ${
          won
            ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300'
            : 'bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-300'
        }`}
      >
        <span>{won ? '✓ Acertó' : '✗ Falló'}</span>
        {pick.profit_loss !== null && <span>{pick.profit_loss >= 0 ? '+' : ''}{pick.profit_loss.toFixed(2)} u</span>}
        {pick.final_score && <span>Final {pick.final_score}</span>}
        {pick.actual_stat !== null && <span>{pick.actual_stat} {statName(pick.market)}</span>}
        {pick.closing_odds !== null && (
          <span className="font-normal opacity-80">cierre {pick.closing_odds.toFixed(2)}</span>
        )}
      </div>
    )
  }
  if (pick.final_score) {
    return (
      <div className="mt-4 rounded-xl bg-slate-100 px-4 py-2 text-sm text-slate-600 dark:bg-slate-800 dark:text-slate-300">
        Final {pick.final_score} · pendiente de liquidar (esperando las estadísticas del partido)
      </div>
    )
  }
  return null
}

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString('es', {
    weekday: 'long',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function PickOfTheDayCard({
  pick,
  rank,
  onOpen,
}: {
  pick: PickOfTheDay
  rank?: number
  onOpen?: () => void
}) {
  const evPct = (pick.expected_value * 100).toFixed(1)
  const modelPct = (pick.model_probability * 100).toFixed(1)
  const impliedPct = (pick.implied_probability * 100).toFixed(1)

  return (
    <div
      onClick={onOpen}
      role={onOpen ? 'button' : undefined}
      tabIndex={onOpen ? 0 : undefined}
      onKeyDown={onOpen ? (e) => (e.key === 'Enter' || e.key === ' ') && onOpen() : undefined}
      className={`rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-700 dark:bg-slate-900 ${
        onOpen ? 'cursor-pointer transition-colors hover:border-indigo-300 dark:hover:border-indigo-600' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-indigo-600 dark:text-indigo-400">
            {rank ? `Pick #${rank}` : 'Pick del día'} · {pick.league}
          </p>
          <h2 className="mt-1 text-2xl font-bold text-slate-900 dark:text-slate-100">
            {pick.home_team} vs {pick.away_team}
          </h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400 capitalize">
            {formatKickoff(pick.kickoff_utc)}
          </p>
        </div>
        <ConfidenceBadge level={pick.confidence_label} />
      </div>

      <div className="mt-6 grid grid-cols-2 gap-6 sm:grid-cols-4">
        <MarketLabel market={pick.market} selection={pick.selection} />

        <div>
          <p className="text-sm text-slate-500 dark:text-slate-400">Cuota</p>
          <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            {pick.bookmaker_odds.toFixed(2)}
          </p>
          {pick.bookmaker && (
            <p className="text-xs text-slate-400 dark:text-slate-500">en {pick.bookmaker}</p>
          )}
        </div>

        <div>
          <p className="text-sm text-slate-500 dark:text-slate-400">Valor esperado (EV)</p>
          <p className="text-lg font-semibold text-emerald-600 dark:text-emerald-400">+{evPct}%</p>
        </div>

        <div>
          <p className="text-sm text-slate-500 dark:text-slate-400">Prob. modelo vs. cuota</p>
          <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            {modelPct}%{' '}
            <span className="text-sm font-normal text-slate-400 dark:text-slate-500">vs {impliedPct}%</span>
          </p>
        </div>
      </div>

      <ResultBanner pick={pick} />

      <div className="mt-4 space-y-1 text-xs text-slate-400 dark:text-slate-500">
        {pick.kelly_fraction !== null && (
          <p>Sugerencia de stake (Kelly fraccional 1/4): {(pick.kelly_fraction * 100).toFixed(1)}% del bankroll</p>
        )}
        <p>
          {pick.historical_skill === null
            ? 'Mercado sin backtest: el modelo no tiene habilidad demostrada aquí.'
            : `Habilidad del modelo en este mercado: ${pick.historical_skill >= 0 ? '+' : ''}${(pick.historical_skill * 100).toFixed(1)} pts sobre la tasa base (${pick.backtest_n_bets} apuestas de backtest)`}
        </p>
        {onOpen && <p className="text-indigo-500 dark:text-indigo-400">Ver detalle y simulación Montecarlo →</p>}
      </div>
    </div>
  )
}
