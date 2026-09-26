import { isNba } from '../format'
import type { DailyCandidate, MatchSummary, Scope } from '../types'
import { ConfidenceBadge } from './ConfidenceBadge'
import { Icon } from './Icon'
import { formatMarket, formatSelection } from './MarketLabel'

const heading = 'm-0 text-xs font-semibold tracking-[0.12em] text-faint uppercase'

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' })
}

/** Partidos del día: cada uno abre el análisis de todos sus mercados. */
export function MatchList({ matches, scope, when, onOpen }: {
  matches: MatchSummary[]
  scope: Scope
  when: string
  onOpen: (m: MatchSummary) => void
}) {
  return (
    <section aria-label={`Partidos de ${when}`} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h3 className={heading}>{isNba(scope) ? `Partidos de la NBA de ${when}` : `Todos los análisis de ${when}`}</h3>
        <p className="m-0 text-[13px] text-faint">
          {scope === 'nba_players'
            ? 'Ábrelos para ver las líneas de jugadores evaluadas (puntos, rebotes, asistencias, triples y combinados).'
            : scope === 'nba'
            ? 'Todos los del día; los que ya tienen líneas muestran sus mercados evaluados.'
            : 'Con historial suficiente y cuotas. Ábrelos para ver todos sus mercados, aunque no hayan pasado el filtro de picks.'}
        </p>
      </div>
      {matches.length === 0 ? (
        <p className="m-0 rounded-3xl border border-ink-600 bg-ink-850 px-6 py-5 text-sm text-muted">
          {isNba(scope) ? `No hay partidos de la NBA ${when}.` : `No hay partidos con datos suficientes ${when}.`}
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col overflow-hidden rounded-3xl border border-ink-600 bg-ink-850 p-0">
          {matches.map((m) => (
            <li key={m.match_id} className="border-t border-ink-700 first:border-t-0">
              <button
                type="button"
                onClick={() => onOpen(m)}
                className="grid w-full grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-ink-800 sm:px-6"
              >
                <span className="font-mono text-sm text-fg-2">{time(m.kickoff_utc)}</span>
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="truncate text-[15px] font-medium">
                    {m.home_team} <span className="text-faint">vs</span> {m.away_team}
                  </span>
                  <span className="truncate text-xs text-muted">
                    {m.league} · {m.n_markets} {scope === 'nba_players' ? (m.n_markets === 1 ? 'línea' : 'líneas') : m.n_markets === 1 ? 'mercado' : 'mercados'}
                  </span>
                </span>
                <span className="flex items-center gap-3">
                  {m.final_score && <span className="font-mono text-sm font-semibold text-fg">{m.final_score}</span>}
                  {m.has_pick && (
                    <span className="rounded-md bg-lime px-2 py-0.5 font-mono text-[11px] font-semibold text-ink-950">PICK</span>
                  )}
                  <Icon name="chevronRight" size={18} className="text-faint" />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** El resto de candidatos que pasaron el filtro de cuota y EV. */
export function CandidatesTable({ candidates, when }: { candidates: DailyCandidate[]; when: string }) {
  if (candidates.length === 0) return null
  return (
    <section aria-label="Otros candidatos" className="flex flex-col gap-3">
      <h3 className={heading}>Otros candidatos evaluados {when}</h3>
      <div className="overflow-x-auto rounded-3xl border border-ink-600 bg-ink-850 px-6 py-3">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-faint uppercase">
              <th className="py-2.5 pr-4 font-medium">Partido</th>
              <th className="py-2.5 pr-4 font-medium">Apuesta</th>
              <th className="py-2.5 pr-4 text-right font-medium">Cuota</th>
              <th className="py-2.5 pr-4 text-right font-medium">EV</th>
              <th className="py-2.5 font-medium">Confianza</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => (
              <tr key={`${c.match_id}-${c.market}-${c.selection}`} className="border-t border-ink-700">
                <td className="py-3.5 pr-4">
                  <span className="block font-medium">
                    {c.home_team} vs {c.away_team}
                  </span>
                  <span className="text-xs text-muted">{c.league}</span>
                </td>
                <td className="py-3.5 pr-4 text-fg-2">
                  <span className="block">
                    {c.player && `${c.player} · `}
                    {formatSelection(c.market, c.selection, { home: c.home_team, away: c.away_team })}
                  </span>
                  <span className="text-xs text-muted">{formatMarket(c.market)}</span>
                </td>
                <td className="py-3.5 pr-4 text-right">
                  <span className="block font-mono font-semibold">{c.bookmaker_odds.toFixed(2)}</span>
                  {c.bookmaker && <span className="text-xs text-muted">{c.bookmaker}</span>}
                </td>
                <td className="py-3.5 pr-4 text-right font-mono font-semibold text-lime">+{(c.expected_value * 100).toFixed(1)}%</td>
                <td className="py-3.5">
                  <ConfidenceBadge level={c.confidence_label} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
