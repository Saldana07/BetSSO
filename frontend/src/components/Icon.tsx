/** Íconos de trazo (sin emoji): el color es el del texto que los rodea. */
const PATHS: Record<string, string[]> = {
  ticket: ['M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4z', 'M9 12l2 2 4-4'],
  ticketOff: ['M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4z', 'M9 10l6 4', 'M15 10l-6 4'],
  globe: ['M3 12h18', 'M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18z', 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z'],
  flag: ['M5 21V4', 'M5 4h11l-2 4 2 4H5'],
  ball: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M3 12h18', 'M12 3v18', 'M5.6 5.6c3 3 3 9.8 0 12.8', 'M18.4 5.6c-3 3-3 9.8 0 12.8'],
  clock: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M12 7v5l3 2'],
  refresh: ['M20 11a8 8 0 0 0-14.9-3', 'M4 4v4h4', 'M4 13a8 8 0 0 0 14.9 3', 'M20 20v-4h-4'],
  shield: ['M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z', 'M9 12l2 2 4-4'],
  trend: ['M3 17l6-6 4 4 8-8', 'M15 7h6v6'],
  download: ['M12 3v12', 'M7 10l5 5 5-5', 'M5 21h14'],
  layers: ['M12 3l9 5-9 5-9-5z', 'M3 13l9 5 9-5'],
  alert: ['M12 9v4', 'M12 17h.01', 'M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'],
  check: ['M5 12l4 4 10-10'],
  x: ['M6 6l12 12', 'M18 6L6 18'],
  chevronLeft: ['M15 6l-6 6 6 6'],
  chevronRight: ['M9 6l6 6-6 6'],
  arrowRight: ['M5 12h14', 'M13 6l6 6-6 6'],
  info: ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M12 11v5', 'M12 8h.01'],
  user: ['M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8z', 'M4 21a8 8 0 0 1 16 0'],
  bandage: ['M8 8l8 8', 'M4.5 12.5l8-8a3.5 3.5 0 0 1 5 5l-8 8a3.5 3.5 0 0 1-5-5z'],
}

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 18, className = '', strokeWidth = 1.8 }: {
  name: IconName
  size?: number
  className?: string
  strokeWidth?: number
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={`shrink-0 ${className}`}
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  )
}
