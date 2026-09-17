import { useEffect, useState } from 'react'
import type { PickOfTheDay } from '../types'
import { MarketsTab } from './MarketsTab'
import { PickOfTheDayCard } from './PickOfTheDayCard'
import { SimulationTab } from './SimulationTab'

export interface MatchRef {
  match_id: number
  home_team: string
  away_team: string
  league: string
  kickoff_utc: string
}

type Tab = 'markets' | 'general' | 'simulation'

const TABS: { id: Tab; label: string }[] = [
  { id: 'markets', label: 'Todos los mercados' },
  { id: 'general', label: 'Pick' },
  { id: 'simulation', label: 'Simulación Montecarlo' },
]

export function MatchDetail({ match, pick, onClose }: { match: MatchRef; pick?: PickOfTheDay; onClose: () => void }) {
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

  const tabs = pick ? TABS : TABS.filter((t) => t.id !== 'general')

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/60 p-4 pt-10 sm:pt-16" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-3xl rounded-2xl bg-white shadow-xl dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-200 px-5 pt-4 dark:border-slate-700">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-indigo-600 dark:text-indigo-400">{match.league}</p>
              <h2 className="text-xl font-bold text-slate-900 dark:text-slate-100">
                {match.home_team} vs {match.away_team}
              </h2>
              <p className="text-xs capitalize text-slate-500 dark:text-slate-400">
                {new Date(match.kickoff_utc).toLocaleString('es', { weekday: 'long', hour: '2-digit', minute: '2-digit' })}
              </p>
            </div>
            <button
              onClick={onClose}
              aria-label="Cerrar"
              className="rounded-md px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              ✕
            </button>
          </div>
          <div role="tablist" className="flex gap-1 overflow-x-auto">
            {tabs.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`-mb-px whitespace-nowrap border-b-2 px-3 pb-3 text-sm font-medium transition-colors ${
                  tab === t.id
                    ? 'border-indigo-600 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400'
                    : 'border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <div className="p-5">
          {tab === 'markets' && <MarketsTab matchId={match.match_id} />}
          {tab === 'general' && pick && <PickOfTheDayCard pick={pick} />}
          {tab === 'simulation' && <SimulationTab matchId={match.match_id} />}
        </div>
      </div>
    </div>
  )
}
