const STYLES: Record<string, string> = {
  ALTA: 'border-win/40 text-win',
  MEDIA: 'border-warn-line text-warn',
  BAJA: 'border-ink-500 text-fg-2',
}

const LABELS: Record<string, string> = {
  ALTA: 'Confianza alta',
  MEDIA: 'Confianza media',
  BAJA: 'Confianza baja',
}

export function ConfidenceBadge({ level }: { level: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${STYLES[level] ?? STYLES.BAJA}`}>
      {LABELS[level] ?? level}
    </span>
  )
}
