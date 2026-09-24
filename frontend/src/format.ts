import type { ActionStatus } from './types'

export type DayOffset = -1 | 0 | 1

export const DAYS: { value: DayOffset; label: string }[] = [
  { value: -1, label: 'Ayer' },
  { value: 0, label: 'Hoy' },
  { value: 1, label: 'Mañana' },
]

export function dayLabel(offset: DayOffset): string {
  return DAYS.find((o) => o.value === offset)?.label.toLowerCase() ?? 'hoy'
}

export function dayTitle(offset: DayOffset): string {
  return DAYS.find((o) => o.value === offset)?.label ?? 'Hoy'
}

/** Fecha larga del día elegido: "jueves 24 de septiembre". */
export function dayDate(offset: DayOffset): string {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  return d.toLocaleDateString('es-CO', { weekday: 'long', day: 'numeric', month: 'long' })
}

/** Primera letra en mayúscula (sin tocar "p. m.", que la clase CSS
 *  `capitalize` volvería "P. M."). */
export function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** "Jue, 7:30 p. m." */
export function kickoffLabel(iso: string): string {
  return cap(new Date(iso).toLocaleString('es-CO', { weekday: 'short', hour: 'numeric', minute: '2-digit' }))
}

/** "Jueves, 7:30 p. m." */
export function kickoffLong(iso: string): string {
  return cap(new Date(iso).toLocaleString('es-CO', { weekday: 'long', hour: 'numeric', minute: '2-digit' }))
}

/** Hora local a la que se renuevan las consultas (00:00 UTC). */
export function resetTime(status: ActionStatus | null): string {
  if (!status) return '7:00 p. m.'
  return new Date(status.quota.resets_at).toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' })
}
