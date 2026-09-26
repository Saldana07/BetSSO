import { kickoffLabel } from '../format'
import type { PickOfTheDay, Scope } from '../types'
import { ConfidenceBadge } from './ConfidenceBadge'
import { Icon } from './Icon'
import { formatMarket, formatSelection } from './MarketLabel'

const pct = (v: number) => `${(v * 100).toFixed(1)}%`

/** Nombre de la estadística de un mercado de conteo, para "Final: 7 córners". */
function statName(market: string): string {
  return formatMarket(market).replace(/^Más\/menos de [\d.]+ /, '')
}

function Badge({ pick, scope }: { pick: PickOfTheDay; scope: Scope }) {
  if (scope !== 'europa') {
    return (
      <span className="rounded-full border border-warn-line px-2.5 py-1 text-[11px] font-semibold tracking-[0.08em] text-warn uppercase">
        Experimental
      </span>
    )
  }
  return <ConfidenceBadge level={pick.confidence_label} />
}

/** Cómo terminó (o que ya terminó y falta liquidar). */
function Result({ pick }: { pick: PickOfTheDay }) {
  if (pick.is_settled && pick.is_win !== null) {
    const won = pick.is_win
    return (
      <div
        className={`flex flex-wrap items-center gap-x-4 gap-y-1 rounded-2xl border px-4 py-3 text-sm font-medium ${
          won ? 'border-win/30 bg-win/10 text-win' : 'border-loss-line bg-loss/10 text-loss'
        }`}
      >
        <span className="flex items-center gap-2">
          <Icon name={won ? 'check' : 'x'} size={16} strokeWidth={2.4} />
          {won ? 'Acertó' : 'Falló'}
        </span>
        {pick.profit_loss !== null && (
          <span className="font-mono">
            {pick.profit_loss >= 0 ? '+' : ''}
            {pick.profit_loss.toFixed(2)} u
          </span>
        )}
        {pick.final_score && <span>Final {pick.final_score}</span>}
        {pick.actual_stat !== null && (
          <span>
            {pick.actual_stat} {statName(pick.market)}
          </span>
        )}
        {pick.closing_odds !== null && <span className="font-normal opacity-80">cierre {pick.closing_odds.toFixed(2)}</span>}
      </div>
    )
  }
  if (pick.is_settled && pick.is_win === null) {
    return (
      <div className="flex items-center gap-2 rounded-2xl border border-ink-600 bg-ink-800 px-4 py-3 text-sm text-fg-2">
        <Icon name="x" size={16} />
        Anulada: el jugador no jugó. Las casas devuelven la apuesta; no cuenta en el contador.
      </div>
    )
  }
  if (pick.final_score) {
    return (
      <div className="flex items-center gap-2 rounded-2xl border border-ink-600 bg-ink-800 px-4 py-3 text-sm text-fg-2">
        <Icon name="clock" size={16} />
        Final {pick.final_score} · pendiente de liquidar: pulsa «Verificar resultados».
      </div>
    )
  }
  return null
}

function Metric({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-2xl bg-ink-800 px-4 py-3.5">
      <span className="text-xs text-muted">{label}</span>
      <span className={`font-mono text-xl font-semibold ${accent ? 'text-lime' : ''}`}>{value}</span>
    </div>
  )
}

/** Perforación de boleto: muescas a los lados y línea discontinua. */
function Perforation({ notch }: { notch: string }) {
  return (
    <div aria-hidden="true" className="relative h-7">
      <div className={`absolute top-0 -left-[15px] size-7 rounded-full border-r border-ink-500 ${notch}`} />
      <div className={`absolute top-0 -right-[15px] size-7 rounded-full border-l border-ink-500 ${notch}`} />
      <div className="absolute inset-x-7 top-[13px] border-t-2 border-dashed border-ink-500" />
    </div>
  )
}

/** Un pick como boleto: partido arriba, apuesta abajo. `hero` para el primero
 *  del día; `compact` para los demás; `detail` dentro del detalle del partido. */
export function PickTicket({ pick, scope, rank, variant = 'hero', onOpen }: {
  pick: PickOfTheDay
  scope: Scope
  rank?: number
  variant?: 'hero' | 'compact' | 'detail'
  onOpen?: () => void
}) {
  const big = variant === 'hero'
  const ev = `${pick.expected_value >= 0 ? '+' : ''}${pct(pick.expected_value)}`
  const lastMetric =
    pick.is_settled && pick.closing_odds !== null
      ? { label: 'Cuota de cierre', value: pick.closing_odds.toFixed(2) }
      : pick.min_odds !== null
        ? { label: 'Vale la pena desde', value: pick.min_odds.toFixed(2) }
        : null

  return (
    <article
      aria-label={`Pick: ${pick.home_team} vs ${pick.away_team}`}
      className={`relative rounded-3xl border border-ink-500 bg-ink-850 ${big ? 'shadow-[0_30px_60px_-30px_rgba(0,0,0,0.8)]' : ''}`}
    >
      <div className={`flex flex-col gap-5 ${big ? 'px-6 pt-7 pb-6 sm:px-9' : 'px-5 pt-5 pb-4 sm:px-7'}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2.5 text-sm text-fg-2">
            {rank !== undefined && (
              <span className="rounded-lg bg-lime px-2.5 py-1 font-mono text-[13px] font-semibold text-ink-950">#{rank}</span>
            )}
            <span>{pick.league}</span>
            <span className="text-faint">·</span>
            <span>{kickoffLabel(pick.kickoff_utc)}</span>
          </div>
          <Badge pick={pick} scope={scope} />
        </div>
        <div className="flex flex-col gap-1">
          <span className={`font-display font-extrabold tracking-tight ${big ? 'text-3xl leading-[1.05] sm:text-[44px]' : 'text-2xl leading-tight'}`}>
            {pick.home_team}
          </span>
          <span className="flex items-baseline gap-3">
            <span className="text-sm font-semibold text-faint">vs</span>
            <span className={`font-display font-extrabold tracking-tight text-fg-2 ${big ? 'text-3xl leading-[1.05] sm:text-[44px]' : 'text-2xl leading-tight'}`}>
              {pick.away_team}
            </span>
          </span>
        </div>
      </div>

      {/* Las muescas toman el color de lo que hay detrás del boleto. */}
      <Perforation notch={variant === 'detail' ? 'bg-ink-900' : 'bg-ink-950'} />

      <div className={`flex flex-col gap-6 ${big ? 'px-6 pt-5 pb-7 sm:px-9' : 'px-5 pt-4 pb-5 sm:px-7'}`}>
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <div className="flex min-w-0 flex-col gap-1.5">
            <span className="text-xs font-semibold tracking-[0.1em] text-faint uppercase">{formatMarket(pick.market)}</span>
            {pick.player && <span className={`font-display font-extrabold tracking-tight ${big ? 'text-2xl' : 'text-lg'}`}>{pick.player}</span>}
            <span className={`font-display font-bold tracking-tight ${big ? 'text-3xl' : 'text-xl'}`}>
              {formatSelection(pick.market, pick.selection, { home: pick.home_team, away: pick.away_team })}
            </span>
          </div>
          <div className="flex flex-col items-end gap-0.5">
            <span className={`font-mono leading-none font-semibold tracking-tight text-lime ${big ? 'text-6xl' : 'text-4xl'}`}>
              {pick.bookmaker_odds.toFixed(2)}
            </span>
            {pick.bookmaker && <span className="text-sm text-muted">en {pick.bookmaker}</span>}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Metric label="Ventaja (EV)" value={ev} accent />
          <Metric label="Prob. estimada" value={pct(pick.model_probability)} />
          <Metric label="Prob. de la cuota" value={pct(pick.implied_probability)} />
          {lastMetric && <Metric label={lastMetric.label} value={lastMetric.value} />}
        </div>

        {big && (
          <div className="flex flex-col gap-2.5">
            {[
              { label: 'Estimada', value: pick.model_probability, bar: 'bg-lime' },
              { label: pick.bookmaker ? `Cuota de ${pick.bookmaker}` : 'Cuota', value: pick.implied_probability, bar: 'bg-[#6b7686]' },
            ].map((r) => (
              <div key={r.label} className="grid grid-cols-[8.5rem_minmax(0,1fr)_3.5rem] items-center gap-3.5 text-[13px]">
                <span className="truncate text-fg-2">{r.label}</span>
                <div className="h-2.5 overflow-hidden rounded-full bg-ink-600">
                  <div className={`h-2.5 rounded-full ${r.bar}`} style={{ width: pct(r.value) }} />
                </div>
                <span className="text-right font-mono">{pct(r.value)}</span>
              </div>
            ))}
          </div>
        )}

        <Result pick={pick} />

        {big && scope !== 'europa' && (
          <div className="flex items-start gap-3 rounded-2xl border border-warn-line bg-warn-bg px-4 py-3.5 text-sm leading-relaxed text-warn-fg">
            <Icon name="alert" className="mt-0.5" />
            <span className="text-pretty">
              {scope === 'nba_players'
                ? 'Línea de jugador, a prueba: se mide contra lo que las demás casas consideran justo en esa misma línea. Su contador aparte dirá si sirve.'
                : scope === 'nba'
                ? 'Pick de la NBA: nace de que esta casa pague más que la cuota justa de Pinnacle, no de la opinión del modelo. Su contador aparte dirá si sirve.'
                : 'Modelo propio del fútbol colombiano, aún sin validar: este pick es su opinión, sin mezclar con la casa, y es una prueba. Su contador aparte dirá si sirve.'}
            </span>
          </div>
        )}

        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1 text-xs text-faint">
            {pick.kelly_fraction !== null && pick.kelly_fraction > 0 && (
              <span>Stake sugerido (¼ de Kelly): {(pick.kelly_fraction * 100).toFixed(1)}% del bank</span>
            )}
            {scope === 'europa' && (
              <span>Estimada = lo que pagan justo las demás casas, corrida hacia el modelo solo lo que los resultados le respaldan.</span>
            )}
            {scope === 'europa' && (
              <span>
                {pick.historical_skill === null
                  ? 'Mercado sin backtest: el modelo no tiene habilidad demostrada aquí.'
                  : `Habilidad en este mercado: ${pick.historical_skill >= 0 ? '+' : ''}${(pick.historical_skill * 100).toFixed(1)} pts sobre la tasa base (${pick.backtest_n_bets} apuestas de backtest)`}
              </span>
            )}
          </div>
          {onOpen && (
            <button
              type="button"
              onClick={onOpen}
              className="flex min-h-11 items-center gap-2 rounded-xl border border-ink-500 px-4 text-sm font-medium text-fg-2 transition-colors hover:border-ink-400 hover:text-fg"
            >
              Ver todos los mercados
              <Icon name="arrowRight" size={16} />
            </button>
          )}
        </div>
      </div>
    </article>
  )
}
