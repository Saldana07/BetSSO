import type { ConfidenceLevel } from '../types'

const OPTIONS: { value: ConfidenceLevel | null; label: string }[] = [
  { value: null, label: 'Todas' },
  { value: 'ALTA', label: 'Alta' },
  { value: 'MEDIA', label: 'Media o más' },
  { value: 'BAJA', label: 'Baja o más' },
]

export function ConfidenceFilter({
  value,
  onChange,
}: {
  value: ConfidenceLevel | null
  onChange: (value: ConfidenceLevel | null) => void
}) {
  return (
    <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1 dark:border-slate-700 dark:bg-slate-900">
      {OPTIONS.map((opt) => (
        <button
          key={opt.label}
          onClick={() => onChange(opt.value)}
          className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
            value === opt.value
              ? 'bg-indigo-600 text-white'
              : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
