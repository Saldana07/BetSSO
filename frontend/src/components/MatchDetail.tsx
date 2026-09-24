import { useEffect, useState } from 'react'
import { kickoffLong } from '../format'
import type { PickOfTheDay, Scope } from '../types'
import { Icon } from './Icon'
import { MarketsTab } from './MarketsTab'
import { NbaInjuries } from './NbaInjuries'
import { PickTicket } from './PickTicket'
import { SimulationTab } from './SimulationTab'

export interface MatchRef {
  match_id: number
  home_team: string
  away_team: string
  league: string
  kickoff_utc: string
}

type Tab = 'markets' | 'general' | 'simulation' | 'injuries'

const TABS: { id: Tab; label: string }[] = [
  { id: 'markets', label: 'Todos los mercados' },
  { id: 'general', label: 'Pick' },
  { id: 'simulation', label: 'Simulación Montecarlo' },
  { id: 'injuries', label: 'Lesiones' },
]

export function MatchDetail({ match, pick, scope = 'europa', onClose }: { match: MatchRef; pick?: PickOfTheDay; scope?: Scope; onClose: () => void }) {
  // Si el partido tiene un pick estricto, se abre en él; si no, en el análisis de mercados.
  const [tab, setTab] = useState<Tab>(pick ? 'general' : 'markets')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  // Las lesiones (parte de ESPN) solo en la NBA; la simulación es del modelo de goles.
  const nba = scope === 'nba'
  const tabs = TABS.filter((t) => (pick || t.id !== 'general') && (nba ? t.id !== 'simulation' : t.id !== 'injuries'))

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-3 pt-8 backdrop-blur-sm sm:p-6 sm:pt-14" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${match.home_team} vs ${match.away_team}`}
        className="w-full max-w-3xl rounded-3xl border border-ink-500 bg-ink-900 shadow-[0_40px_80px_-30px_rgba(0,0,0,0.9)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-ink-600 px-6 pt-6">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div className="flex flex-col gap-1">
              <span className={`text-xs font-semibold tracking-[0.12em] uppercase ${nba ? 'text-sky' : 'text-lime'}`}>{match.league}</span>
              <h2 className="m-0 font-display text-2xl font-extrabold tracking-tight sm:text-[28px]">
                {match.home_team} <span className="text-faint">vs</span> {match.away_team}
              </h2>
              <span className="text-[13px] text-muted">{kickoffLong(match.kickoff_utc)}</span>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Cerrar"
              className="flex size-11 items-center justify-center rounded-xl text-muted transition-colors hover:bg-ink-800 hover:text-fg"
            >
              <Icon name="x" size={20} />
            </button>
          </div>
          <div role="tablist" className="flex gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {tabs.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`-mb-px min-h-11 border-b-2 px-3 text-sm font-medium whitespace-nowrap transition-colors ${
                  tab === t.id ? 'border-lime text-fg' : 'border-transparent text-muted hover:text-fg'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="p-5 sm:p-6">
          {tab === 'markets' && <MarketsTab matchId={match.match_id} scope={scope} />}
          {tab === 'general' && pick && <PickTicket pick={pick} scope={scope} variant="detail" />}
          {tab === 'simulation' && <SimulationTab matchId={match.match_id} />}
          {tab === 'injuries' && <NbaInjuries matchId={match.match_id} />}
        </div>
      </div>
    </div>
  )
}
