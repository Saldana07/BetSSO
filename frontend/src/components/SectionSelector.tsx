import type { ColombiaStatus, Scope } from '../types'

const SECTIONS: { value: Scope; label: string }[] = [
  { value: 'europa', label: 'Europa' },
  { value: 'colombia', label: 'Fútbol colombiano' },
]

/** Dos apartados con contadores separados: lo de Colombia no suma a Europa. */
export function SectionSelector({ value, onChange }: { value: Scope; onChange: (s: Scope) => void }) {
  return (
    <nav aria-label="Apartado" className="flex gap-1 border-b border-slate-200 dark:border-slate-800">
      {SECTIONS.map((s) => {
        const active = s.value === value
        return (
          <button
            key={s.value}
            onClick={() => onChange(s.value)}
            aria-current={active ? 'page' : undefined}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-semibold transition-colors ${
              active
                ? 'border-indigo-600 text-indigo-700 dark:border-indigo-400 dark:text-indigo-300'
                : 'border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
          >
            {s.label}
          </button>
        )
      })}
    </nav>
  )
}

/** Aviso del apartado colombiano: qué es, por qué es experimental y cuánto
 *  historial hay (sin él no hay predicciones de córners ni tarjetas). */
export function ColombiaBanner({ status }: { status: ColombiaStatus | null }) {
  return (
    <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
      <p className="font-semibold">Apartado experimental — contador propio</p>
      <p className="mt-1">
        Primera A y Primera B con un modelo entrenado solo con partidos colombianos. Sus picks no suman al contador de
        Europa: este contador y la tabla "Modelo vs casa" de aquí abajo son los que dirán si el modelo sirve para la liga
        colombiana. Mientras tanto, trátalos como prueba, no como recomendación.
      </p>
      {status && (
        <p className="mt-2 text-xs opacity-80">
          Historial: {status.matches} partidos, {status.matches_with_stats} con estadísticas · equipos listos para córners y
          tarjetas (10+ partidos): {status.teams_ready} de {status.teams_total}
          {status.import_requests_left > 0 &&
            ` · faltan ~${status.import_requests_left} consultas para completar 2022-2026 (con «Completar datos»)`}
        </p>
      )}
    </div>
  )
}
