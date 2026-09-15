import { useEffect, useState } from 'react'
import type { PickOfTheDay } from '../types'
import { PickOfTheDayCard } from './PickOfTheDayCard'
import { SimulationTab } from './SimulationTab'

type Tab = 'general' | 'simulation'

const TABS: { id: Tab; label: string }[] = [
  { id: 'general', label: 'General' },
  { id: 'simulation', label: 'Simulación Montecarlo' },
]

export function MatchDetail({ pick, onClose }: { pick: PickOfTheDay; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('general')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/60 p-4 pt-10 sm:pt-16" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-3xl rounded-2xl bg-white shadow-xl dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 pt-4 dark:border-slate-700">
          <div role="tablist" className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`-mb-px border-b-2 px-3 pb-3 text-sm font-medium transition-colors ${
                  tab === t.id
                    ? 'border-indigo-600 text-indigo-600 dark:border-indigo-400 dark:text-indigo-400'
                    : 'border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <button
            onClick={onClose}
            aria-label="Cerrar"
            className="mb-3 rounded-md px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            ✕
          </button>
        </div>

        <div className="p-5">
          {tab === 'general' ? <PickOfTheDayCard pick={pick} /> : <SimulationTab matchId={pick.match_id} />}
        </div>
      </div>
    </div>
  )
}
