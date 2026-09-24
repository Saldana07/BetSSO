import { DAYS, type DayOffset } from '../format'
import type { ConfidenceLevel } from '../types'

function Segmented<T extends string | number | null>({ label, options, value, onChange, size = 'md' }: {
  label: string
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
  size?: 'md' | 'sm'
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex gap-1 rounded-2xl border border-ink-600 bg-ink-850 p-1">
      {options.map((o) => {
        const active = o.value === value
        return (
          <button
            key={String(o.value)}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            className={`rounded-xl text-sm whitespace-nowrap transition-colors ${size === 'md' ? 'min-h-11 min-w-20 px-4' : 'min-h-10 px-3.5'} ${
              active ? 'bg-fg font-semibold text-ink-950' : 'font-medium text-muted hover:text-fg'
            }`}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

export function DayTabs({ value, onChange }: { value: DayOffset; onChange: (v: DayOffset) => void }) {
  return <Segmented label="Día" options={DAYS} value={value} onChange={onChange} />
}

const CONFIDENCE: { value: ConfidenceLevel | null; label: string }[] = [
  { value: null, label: 'Todas' },
  { value: 'ALTA', label: 'Alta' },
  { value: 'MEDIA', label: 'Media o más' },
  { value: 'BAJA', label: 'Baja o más' },
]

export function ConfidenceFilter({ value, onChange }: { value: ConfidenceLevel | null; onChange: (v: ConfidenceLevel | null) => void }) {
  return <Segmented label="Confianza" options={CONFIDENCE} value={value} onChange={onChange} size="sm" />
}
