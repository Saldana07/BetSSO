import type { NbaStatus, NbaValidation } from '../types'

function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`
}

function dateLabel(iso: string): string {
  return new Date(iso).toLocaleDateString('es', { day: 'numeric', month: 'long' })
}

/** Qué dijo la prueba contra las líneas de cierre reales, en palabras llanas. */
function ValidationSummary({ v }: { v: NbaValidation }) {
  const adds = v.weights_live.margin > 0 || v.weights_live.total > 0
  const close = v.betting.close
  return (
    <div className="mt-2 space-y-1 text-xs opacity-90">
      <p>
        <strong>Prueba contra la casa</strong> ({v.n_fit + v.n_test} partidos de {v.fit_seasons[0]} a{' '}
        {v.test_seasons[v.test_seasons.length - 1]}, con las líneas de cierre reales): error medio del modelo{' '}
        {v.margin.mae_model} puntos en el margen y {v.total.mae_model} en el total; el de la casa, {v.margin.mae_market}{' '}
        y {v.total.mae_market}.
      </p>
      <p>
        {adds ? (
          <>
            El modelo aporta algo que la línea no tenía
            {v.weights_live.margin > 0 && ` en el margen (${Math.round(v.weights_live.margin * 100)}% de su diferencia)`}
            {v.weights_live.total > 0 && ` en el total (${Math.round(v.weights_live.total * 100)}% de su diferencia)`}, y se
            confirmó en {v.test_seasons.join(' y ')}.
          </>
        ) : (
          <>
            El modelo <strong>no le gana a la casa</strong>: la línea de cierre ya sabe todo lo que él sabe. Por eso los picks
            no se basan en su opinión, sino en <strong>diferencias de precio</strong>: Betano o Bwin pagando más de lo que el
            resto del mercado (sobre todo Pinnacle) considera justo.
          </>
        )}
      </p>
      <p>
        Apostando con el modelo solo en {v.test_seasons.join(' y ')}: {close.modelo_solo.bets} apuestas, ROI{' '}
        {pct(close.modelo_solo.roi)} a cuotas de cierre y {pct(v.betting.open.modelo_solo.roi)} a cuotas de apertura.
      </p>
      {v.price_shopping && (
        <p>
          Comprar el precio de una casa que se aparta de las demás daría {pct(v.price_shopping.near.roi)} en{' '}
          {v.price_shopping.near.bets} apuestas ({v.price_shopping.seasons.join(', ')}), pero{' '}
          <strong>no es creíble</strong>: con líneas más apartadas "gana" aún más ({pct(v.price_shopping.far.roi)}), la marca
          de líneas viejas en los datos de ESPN. {v.price_shopping.reason}
        </p>
      )}
    </div>
  )
}

/** Aviso del apartado NBA: qué es, cuándo hay partidos y qué tan confiable es. */
export function NbaBanner({ status }: { status: NbaStatus | null }) {
  return (
    <div className="mb-6 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-950 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-100">
      <p className="font-semibold">Apartado NBA — experimental, contador propio</p>
      <p className="mt-1">
        Ganador, hándicap de puntos y total de puntos. El modelo sigue el ataque, la defensa y el ritmo de cada equipo,
        la ventaja de local y el cansancio (jugar dos días seguidos), y se combina con lo que dicen las casas. Sus picks no
        suman a los de fútbol.
      </p>
      {status?.regular_season_start_utc && !status.regular_season_started && (
        <p className="mt-2 font-medium">
          La temporada regular empieza el {dateLabel(status.regular_season_start_utc)}
          {status.next_game_preseason && status.next_game_utc && ` (pretemporada desde el ${dateLabel(status.next_game_utc)}, sin picks: los titulares juegan poco)`}.
          Hasta entonces no hay picks.
        </p>
      )}
      {status?.validation ? (
        <ValidationSummary v={status.validation} />
      ) : (
        status && <p className="mt-2 text-xs opacity-80">El modelo aún no se ha validado contra las líneas de cierre.</p>
      )}
      {status && (
        <p className="mt-2 text-xs opacity-70">
          Datos gratuitos: {status.games.toLocaleString('es')} partidos de {status.first_season} a {status.last_season}{' '}
          (nba.com), {status.games_with_closing_odds.toLocaleString('es')} con líneas de cierre (ESPN). Cuotas de Betano y
          Pinnacle: API de baloncesto, con tu misma clave y 100 consultas diarias aparte de las de fútbol.
        </p>
      )}
    </div>
  )
}
