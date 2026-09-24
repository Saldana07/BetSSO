import { MARKET_LABELS, SELECTION_LABELS } from '../types'

function line(market: string, prefix: string): string {
  return market.slice(prefix.length)
}

// Mercados de conteo: prefijo -> nombre de la estadística (plural).
const COUNT_STATS: Record<string, string> = {
  CORNERS_OU_: 'córners',
  SHOTS_OU_: 'tiros',
  SOT_OU_: 'tiros a puerta',
  CARDS_OU_: 'tarjetas',
  FOULS_OU_: 'faltas',
}

function countStat(market: string): { prefix: string; name: string } | null {
  for (const [prefix, name] of Object.entries(COUNT_STATS)) {
    if (market.startsWith(prefix)) return { prefix, name }
  }
  return null
}

/** Línea del local de un hándicap vista desde la selección: el visitante la
 *  tiene con el signo invertido (local -5.5 = visitante +5.5). */
function sideLine(homeLine: string, selection: string): string {
  if (selection === 'HOME') return homeLine.startsWith('-') ? homeLine : `+${homeLine}`
  return homeLine.startsWith('-') ? `+${homeLine.slice(1)}` : `-${homeLine}`
}

export function formatMarket(market: string): string {
  if (MARKET_LABELS[market]) return MARKET_LABELS[market]
  // NBA
  if (market.startsWith('SPREAD_')) return `Hándicap de puntos (local ${sideLine(line(market, 'SPREAD_'), 'HOME')})`
  if (market.startsWith('TOTAL_')) return `Más/menos de ${line(market, 'TOTAL_')} puntos`
  const stat = countStat(market)
  if (stat) return `Más/menos de ${line(market, stat.prefix)} ${stat.name}`
  if (market.startsWith('TEAM_OU_HOME_')) return `Goles del local (línea ${line(market, 'TEAM_OU_HOME_')})`
  if (market.startsWith('TEAM_OU_AWAY_')) return `Goles del visitante (línea ${line(market, 'TEAM_OU_AWAY_')})`
  if (market.startsWith('OU_')) return `Más/menos de ${line(market, 'OU_')} goles`
  if (market.startsWith('AH_')) return `Hándicap asiático (local ${line(market, 'AH_')})`
  return market
}

export function formatSelection(market: string, selection: string): string {
  const stat = countStat(market)
  if (stat) return `${SELECTION_LABELS[selection] ?? selection} ${line(market, stat.prefix)} ${stat.name}`
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
  if (market.startsWith('SPREAD_')) return `${SELECTION_LABELS[selection] ?? selection} ${sideLine(line(market, 'SPREAD_'), selection)}`
  if (market.startsWith('TOTAL_')) return `${SELECTION_LABELS[selection] ?? selection} ${line(market, 'TOTAL_')} puntos`
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
