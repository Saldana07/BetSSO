import { MARKET_LABELS, SELECTION_LABELS } from '../types'

function line(market: string, prefix: string): string {
  return market.slice(prefix.length)
}

export function formatMarket(market: string): string {
  if (MARKET_LABELS[market]) return MARKET_LABELS[market]
  if (market.startsWith('CORNERS_OU_')) return `Más/menos de ${line(market, 'CORNERS_OU_')} córners`
  if (market.startsWith('TEAM_OU_HOME_')) return `Goles del local (línea ${line(market, 'TEAM_OU_HOME_')})`
  if (market.startsWith('TEAM_OU_AWAY_')) return `Goles del visitante (línea ${line(market, 'TEAM_OU_AWAY_')})`
  if (market.startsWith('OU_')) return `Más/menos de ${line(market, 'OU_')} goles`
  if (market.startsWith('AH_')) return `Hándicap asiático (local ${line(market, 'AH_')})`
  return market
}

export function formatSelection(market: string, selection: string): string {
  if (market.startsWith('CORNERS_OU_')) return `${SELECTION_LABELS[selection] ?? selection} ${line(market, 'CORNERS_OU_')} córners`
  if (market.startsWith('TEAM_OU_')) {
    const l = market.replace(/^TEAM_OU_(HOME|AWAY)_/, '')
    return `${SELECTION_LABELS[selection] ?? selection} ${l} goles`
  }
  if (market.startsWith('OU_')) return `${SELECTION_LABELS[selection] ?? selection} ${line(market, 'OU_')} goles`
  if (market.startsWith('AH_')) {
    const l = line(market, 'AH_')
    // La línea es del local; para el visitante se muestra con el signo invertido.
    const shown = selection === 'HOME' ? l : l.startsWith('-') ? `+${l.slice(1)}` : `-${l.slice(1)}`
    return `${SELECTION_LABELS[selection] ?? selection} ${shown}`
  }
  if (market === 'EXACT_SCORE') return selection.replace('-', ' – ')
  return SELECTION_LABELS[selection] ?? selection
}

export function MarketLabel({ market, selection }: { market: string; selection: string }) {
  return (
    <div>
      <p className="text-sm text-slate-500 dark:text-slate-400">{formatMarket(market)}</p>
      <p className="text-lg font-semibold text-slate-900 dark:text-slate-100">
        {formatSelection(market, selection)}
      </p>
    </div>
  )
}
