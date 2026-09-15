import { MARKET_LABELS, SELECTION_LABELS } from '../types'

export function formatSelection(market: string, selection: string): string {
  if (market.startsWith('CORNERS_OU_')) {
    const line = market.replace('CORNERS_OU_', '')
    return `${SELECTION_LABELS[selection] ?? selection} ${line} córners`
  }
  if (market.startsWith('OU_')) {
    const line = market.replace('OU_', '')
    return `${SELECTION_LABELS[selection] ?? selection} ${line} goles`
  }
  return SELECTION_LABELS[selection] ?? selection
}

export function formatMarket(market: string): string {
  return MARKET_LABELS[market] ?? market
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
