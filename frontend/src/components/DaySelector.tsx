export type DayOffset = -1 | 0 | 1

const OPTIONS: { value: DayOffset; label: string }[] = [
  { value: -1, label: 'Ayer' },
  { value: 0, label: 'Hoy' },
  { value: 1, label: 'Mañana' },
]

export function dayLabel(offset: DayOffset): string {
  return OPTIONS.find((o) => o.value === offset)?.label.toLowerCase() ?? 'hoy'
}

export function DaySelector({ value, onChange }: { value: DayOffset; onChange: (v: DayOffset) => void }) {
  return (
    <div role="tablist" aria-label="Día" className="inline-flex rounded-lg border border-slate-300 p-0.5 dark:border-slate-600">
      {OPTIONS.map((o) => {
        const active = o.value === value
        return (
          <button
            key={o.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={`rounded-md px-3 py-1 text-sm font-medium transition-colors ${
              active
                ? 'bg-indigo-600 text-white'
                : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
            }`}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
