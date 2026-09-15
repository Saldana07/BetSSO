const STYLES: Record<string, string> = {
  ALTA: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  MEDIA: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  BAJA: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
}

const LABELS: Record<string, string> = {
  ALTA: 'Confianza alta',
  MEDIA: 'Confianza media',
  BAJA: 'Confianza baja',
}

export function ConfidenceBadge({ level }: { level: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ${STYLES[level] ?? STYLES.BAJA}`}
    >
      {LABELS[level] ?? level}
    </span>
  )
}
