import type { DailyCandidate } from '../types'
import { ConfidenceBadge } from './ConfidenceBadge'
import { formatMarket, formatSelection } from './MarketLabel'

export function CandidateRow({ candidate }: { candidate: DailyCandidate }) {
  const evPct = (candidate.expected_value * 100).toFixed(1)

  return (
    <tr className="border-b border-slate-100 last:border-0 dark:border-slate-800">
      <td className="py-3 pr-4">
        <p className="font-medium text-slate-900 dark:text-slate-100">
          {candidate.home_team} vs {candidate.away_team}
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">{candidate.league}</p>
      </td>
      <td className="py-3 pr-4 text-sm text-slate-700 dark:text-slate-300">
        {formatMarket(candidate.market)} · {formatSelection(candidate.market, candidate.selection)}
      </td>
      <td className="py-3 pr-4 text-sm font-medium text-slate-900 dark:text-slate-100">
        {candidate.bookmaker_odds.toFixed(2)}
        {candidate.bookmaker && (
          <span className="block text-xs font-normal text-slate-400 dark:text-slate-500">{candidate.bookmaker}</span>
        )}
      </td>
      <td className="py-3 pr-4 text-sm font-medium text-emerald-600 dark:text-emerald-400">+{evPct}%</td>
      <td className="py-3">
        <ConfidenceBadge level={candidate.confidence_label} />
      </td>
    </tr>
  )
}
