import { MIN_PICKS_FOR_SIGNIFICANCE, type Performance } from '../types'

export function PerformanceBar({ performance }: { performance: Performance }) {
  const { settled_picks, wins, losses, hit_rate, profit_loss_units, yield_pct, pending_picks } = performance

  if (settled_picks === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        Todavía no hay picks liquidados.
        {pending_picks > 0 && ` ${pending_picks} esperando resultado.`}
      </div>
    )
  }

  const isSignificant = settled_picks >= MIN_PICKS_FOR_SIGNIFICANCE
  const profitColor = profit_loss_units >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-900">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Resultado real
        </span>
        <span className="text-sm text-slate-700 dark:text-slate-300">
          {settled_picks} {settled_picks === 1 ? 'pick' : 'picks'} · {wins}G / {losses}P
        </span>
        <span className="text-sm text-slate-700 dark:text-slate-300">
          Acierto <strong>{((hit_rate ?? 0) * 100).toFixed(0)}%</strong>
        </span>
        <span className={`text-sm font-semibold ${profitColor}`}>
          {profit_loss_units >= 0 ? '+' : ''}
          {profit_loss_units.toFixed(2)} u · yield {yield_pct?.toFixed(1)}%
        </span>
        {pending_picks > 0 && (
          <span className="text-xs text-slate-400 dark:text-slate-500">{pending_picks} pendientes</span>
        )}
      </div>

      {!isSignificant && (
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
          Muestra demasiado pequeña ({settled_picks} de {MIN_PICKS_FOR_SIGNIFICANCE} picks): estos porcentajes
          son ruido estadístico, no evidencia de que el sistema funcione.
        </p>
      )}
    </div>
  )
}
