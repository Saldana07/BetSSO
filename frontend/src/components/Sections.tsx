import { useState } from 'react'
import type { ColombiaStatus, NbaBetSim, NbaStatus, NbaValidation } from '../types'
import { Icon } from './Icon'

const eyebrow = 'text-xs font-semibold tracking-[0.12em] text-faint uppercase'
const n = (x: number) => x.toLocaleString('es-CO')

function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? '—' : `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)}%`
}

function dateLabel(iso: string): string {
  return new Date(iso).toLocaleDateString('es-CO', { day: 'numeric', month: 'long' })
}

/** Apartado colombiano: qué historial tiene su modelo propio. */
export function ColombiaCard({ status }: { status: ColombiaStatus | null }) {
  if (!status) return null
  return (
    <section aria-label="Historial colombiano" className="flex flex-col gap-4 rounded-3xl border border-ink-600 bg-ink-850 p-6">
      <span className={eyebrow}>Modelo propio · Colombia</span>
      <p className="m-0 text-sm leading-relaxed text-pretty text-muted">
        Primera A y B, entrenado solo con partidos colombianos. Sin validar todavía: sus picks son una prueba.
      </p>
      <dl className="m-0 grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1 rounded-2xl bg-ink-800 px-4 py-3">
          <dt className="text-xs text-muted">Partidos</dt>
          <dd className="m-0 font-mono text-lg font-semibold">{n(status.matches)}</dd>
        </div>
        <div className="flex flex-col gap-1 rounded-2xl bg-ink-800 px-4 py-3">
          <dt className="text-xs text-muted">Con estadísticas</dt>
          <dd className="m-0 font-mono text-lg font-semibold">{n(status.matches_with_stats)}</dd>
        </div>
        <div className="col-span-2 flex flex-col gap-1 rounded-2xl bg-ink-800 px-4 py-3">
          <dt className="text-xs text-muted">Equipos listos para córners y tarjetas (10+ partidos)</dt>
          <dd className="m-0 font-mono text-lg font-semibold">
            {status.teams_ready} de {status.teams_total}
          </dd>
        </div>
      </dl>
      {status.import_requests_left > 0 && (
        <p className="m-0 text-[13px] text-faint">
          Faltan ~{status.import_requests_left} consultas para completar 2022-2026: con «Completar datos».
        </p>
      )}
    </section>
  )
}

/** Cuándo hay NBA: cuenta atrás hasta la temporada regular y primeros partidos. */
export function NbaSeasonCard({ status }: { status: NbaStatus | null }) {
  const [now] = useState(() => Date.now())
  if (!status?.regular_season_start_utc || status.regular_season_started) return null
  // Días de calendario en tu zona horaria (no horas redondeadas hacia arriba).
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const days = Math.max(0, Math.round((midnight(new Date(status.regular_season_start_utc)) - midnight(new Date(now))) / 86_400_000))
  return (
    <section aria-label="Temporada" className="grid overflow-hidden rounded-3xl border border-ink-600 bg-ink-850 md:grid-cols-[18rem_minmax(0,1fr)]">
      <div className="flex flex-col justify-center gap-1 border-b border-sky-line bg-sky-bg px-8 py-7 md:border-r md:border-b-0">
        <span className="font-mono text-7xl leading-[0.95] font-semibold tracking-tight text-sky md:text-[88px]">{days}</span>
        <span className="text-[15px] text-[#bfe2ff]">{days === 1 ? 'día' : 'días'} para la temporada regular</span>
      </div>
      <div className="grid sm:grid-cols-2">
        {status.next_game_preseason && status.next_game_utc && (
          <div className="flex flex-col gap-2 border-b border-ink-600 px-8 py-7 sm:border-r sm:border-b-0">
            <span className={eyebrow}>Pretemporada · {dateLabel(status.next_game_utc)}</span>
            {status.next_game && <span className="font-display text-2xl font-bold tracking-tight">{status.next_game}</span>}
            <span className="text-sm leading-relaxed text-muted">Sin picks: los titulares juegan poco y el marcador no dice nada de la temporada.</span>
          </div>
        )}
        <div className="flex flex-col gap-2 px-8 py-7">
          <span className={eyebrow}>Temporada regular · {dateLabel(status.regular_season_start_utc)}</span>
          <span className="font-display text-2xl font-bold tracking-tight">Ganador, hándicap y total</span>
          <span className="text-sm leading-relaxed text-muted">Desde ese día hay picks, con su contador aparte de los de fútbol.</span>
        </div>
      </div>
    </section>
  )
}

function ErrorBars({ title, model, market, scale }: { title: string; model: number; market: number; scale: number }) {
  return (
    <div className="flex flex-col gap-3">
      <span className="text-[13px] text-fg-2">{title} (menos es mejor)</span>
      {[
        { label: 'Modelo', value: model, bar: 'bg-[#6b7686]' },
        { label: 'Casa', value: market, bar: 'bg-sky' },
      ].map((r) => (
        <div key={r.label} className="grid grid-cols-[3.5rem_minmax(0,1fr)_3.25rem] items-center gap-3 text-[13px]">
          <span className="text-muted">{r.label}</span>
          <div className="h-3 overflow-hidden rounded-full bg-ink-600">
            <div className={`h-3 rounded-full ${r.bar}`} style={{ width: `${Math.min(100, (r.value / scale) * 100)}%` }} />
          </div>
          <span className="text-right font-mono">{r.value.toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}

function Sim({ label, sim, note, struck = false }: { label: string; sim: NbaBetSim | undefined; note: string; struck?: boolean }) {
  return (
    <div className={`flex flex-col gap-1.5 rounded-2xl bg-ink-800 px-4 py-4 ${struck ? 'border border-dashed border-ink-400' : ''}`}>
      <span className="text-xs text-muted">{label}</span>
      <span className={`font-mono text-2xl font-semibold ${struck ? 'text-faint line-through' : (sim?.roi ?? 0) >= 0 ? 'text-win' : 'text-loss'}`}>
        {pct(sim?.roi)}
      </span>
      <span className="text-xs text-faint">{note}</span>
    </div>
  )
}

/** La prueba contra las líneas de cierre reales, en palabras llanas. */
export function NbaValidationCard({ v }: { v: NbaValidation | null }) {
  if (!v) {
    return (
      <section className="rounded-3xl border border-ink-600 bg-ink-850 p-6 text-sm text-muted">
        El modelo de la NBA aún no se ha validado contra las líneas de cierre: «Actualizar NBA» lo entrena la primera vez.
      </section>
    )
  }
  const adds = v.weights_live.margin > 0 || v.weights_live.total > 0
  const seasons = `${v.test_seasons[0]} a ${v.test_seasons[v.test_seasons.length - 1]}`
  return (
    <section aria-label="Prueba contra la casa" className="flex flex-col gap-6 rounded-3xl border border-ink-600 bg-ink-850 p-6 sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h2 className="m-0 font-display text-[28px] font-extrabold tracking-tight">Prueba contra la casa</h2>
          <span className="text-sm text-muted">
            {n(v.n_fit + v.n_test)} partidos de {v.fit_seasons[0]} a {v.test_seasons[v.test_seasons.length - 1]}, contra las líneas de
            cierre reales
          </span>
        </div>
        <span className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${adds ? 'border-win/40 text-win' : 'border-loss-line text-loss'}`}>
          {adds ? 'El modelo aporta algo sobre la línea' : 'El modelo no le gana a la casa'}
        </span>
      </div>

      <div className="grid gap-7 md:grid-cols-2">
        <ErrorBars title="Error medio en el margen" model={v.margin.mae_model} market={v.margin.mae_market} scale={12} />
        <ErrorBars title="Error medio en el total" model={v.total.mae_model} market={v.total.mae_market} scale={16} />
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Sim label="Modelo solo · a cierre" sim={v.betting.close.modelo_solo} note={`${n(v.betting.close.modelo_solo.bets)} apuestas, ${seasons}`} />
        <Sim label="Modelo solo · a apertura" sim={v.betting.open.modelo_solo} note="misma prueba, cuota de apertura" />
        {v.price_shopping && (
          <Sim label="Comprar precio · no fiable" sim={v.price_shopping.near} note="líneas viejas en los datos de ESPN" struck />
        )}
      </div>

      {v.price_shopping && (
        <p className="m-0 text-[13px] leading-relaxed text-pretty text-faint">
          Comprar el precio de una casa que se aparta de las demás daría {pct(v.price_shopping.near.roi)} en {v.price_shopping.near.bets}{' '}
          apuestas, pero con líneas más apartadas "gana" aún más ({pct(v.price_shopping.far.roi)}): la marca de líneas viejas. {v.price_shopping.reason}
        </p>
      )}

      <div className="flex items-start gap-3.5 rounded-2xl border border-sky-line bg-sky-bg px-5 py-4 text-[15px] leading-relaxed text-sky-fg">
        <Icon name="trend" size={20} className="mt-0.5" />
        <span className="text-pretty">
          {adds
            ? 'Los picks combinan la línea del mercado con lo que el modelo sabe de más, en la medida en que se confirmó en las temporadas de prueba.'
            : 'Por eso un pick de la NBA solo aparece cuando Betano o Bwin pagan más que la cuota justa de Pinnacle en esa misma línea. En la NBA pasa pocas veces.'}
        </span>
      </div>
    </section>
  )
}

/** De dónde salen los datos de la NBA. */
export function NbaDataCard({ status }: { status: NbaStatus | null }) {
  if (!status) return null
  const rows: [string, string, boolean][] = [
    [`Partidos ${status.first_season?.slice(0, 4) ?? ''}-${status.last_season?.slice(-2) ?? ''} · nba.com`, n(status.games), true],
    ['Con línea de cierre · ESPN', n(status.games_with_closing_odds), true],
    ['Parte de lesiones · ESPN', 'en cada partido', false],
    ['Betano, Pinnacle, Bet365', '100/día aparte', false],
  ]
  return (
    <section aria-label="Datos" className="flex flex-col gap-3.5 rounded-3xl border border-ink-600 bg-ink-850 p-6">
      <span className={eyebrow}>De dónde salen los datos</span>
      {rows.map(([label, value, mono]) => (
        <div key={label} className="flex justify-between gap-3 text-sm">
          <span className="text-fg-2">{label}</span>
          <span className={mono ? 'font-mono' : 'text-muted'}>{value}</span>
        </div>
      ))}
    </section>
  )
}
