"""Historial gratuito de la NBA: partidos, estadísticas y cuotas de cierre.

  1. stats.nba.com: temporadas 2014-15 a la última terminada (temporada
     regular, play-in y playoffs), 1 petición por temporada y tipo.
  2. cdn.nba.com: calendario de la temporada en curso (partidos por jugar).
  3. ESPN: cuotas de apertura/cierre de cada partido desde 2021-22, 1 petición
     por partido + 1 por día. Es la vara para medir el modelo contra el mercado.

Nada de esto gasta cuota de API-Sports. Es reanudable: lo ya traído no se
vuelve a pedir (las temporadas cerradas quedan anotadas en
data/nba/import_state.json; las cuotas, en nba_games.espn_odds_fetched_at).

Uso:
    python -m ml.nba.import_history [--solo partidos|calendario|cuotas] [--max-cuotas 500]
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import groupby
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import NbaGame
from ml.nba import sources
from ml.nba.store import ensure_tables, games_by_nicknames, nickname, store_odds, upsert_games, utcnow

STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "nba" / "import_state.json"
FIRST_SEASON = 2014
ODDS_FIRST_SEASON = 2021
ESPN_PAUSE = 0.3          # segundos entre tandas: sin prisa, es un servicio ajeno
ESPN_WORKERS = 8          # peticiones a la vez (una a una eran más de 2 horas)
ESPN_DAYS_PER_BATCH = 5
STATS_PAUSE = 2.0


def _state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seasons_done": []}


def _save(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def last_finished_season(today: date) -> str:
    """La última temporada con playoffs terminados (terminan en junio)."""
    start = today.year - 1 if today.month >= 7 else today.year - 2
    return sources.season_label(start)


def import_games(db, seasons: list[str]) -> int:
    """Temporadas por registro de partidos. Las cerradas no se repiten."""
    state = _state()
    closed = last_finished_season(date.today())
    total = 0
    for season in seasons:
        if season in state["seasons_done"]:
            continue
        n = 0
        for label in sources.SEASON_TYPES:
            rows = sources.fetch_league_game_log(season, label)
            n += upsert_games(db, sources.parse_game_log(rows, season))
            db.commit()
            time.sleep(STATS_PAUSE)
        print(f"  {season}: {n} partidos")
        total += n
        if season <= closed:
            state["seasons_done"].append(season)
            _save(state)
    return total


def import_schedule(db) -> int:
    games = sources.parse_schedule(sources.fetch_schedule())
    n = upsert_games(db, games)
    db.commit()
    return n


def import_espn_odds(db, first_season: str, max_games: int | None = None) -> tuple[int, int]:
    """Cuotas de cierre de ESPN para partidos jugados que aún no las tienen."""
    todo = db.execute(
        select(NbaGame).where(NbaGame.status == "FINISHED", NbaGame.season >= first_season,
                              NbaGame.season_type.in_(("REGULAR", "PLAYIN", "PLAYOFFS")),
                              NbaGame.espn_odds_fetched_at.is_(None))
        .order_by(NbaGame.game_date)
    ).scalars().all()
    if max_games is not None:
        todo = todo[:max_games]
    fetched = with_odds = 0

    def odds_of(event_id: str) -> list[dict] | None:
        try:
            return sources.parse_espn_odds(sources.fetch_espn_odds(event_id))
        except RuntimeError as exc:
            print(f"  [!] ESPN cuotas {event_id}: {exc}")
            return None

    def events_of(day) -> list[dict] | None:
        try:
            return sources.parse_espn_events(sources.fetch_espn_scoreboard(day))
        except RuntimeError as exc:
            print(f"  [!] ESPN {day}: {exc}")
            return None

    days = [(day, list(gs)) for day, gs in groupby(todo, key=lambda g: g.game_date)]
    with ThreadPoolExecutor(ESPN_WORKERS) as pool:
        for i in range(0, len(days), ESPN_DAYS_PER_BATCH):
            batch = days[i:i + ESPN_DAYS_PER_BATCH]
            matched, failed = [], set()
            for (day, day_games), events in zip(batch, pool.map(events_of, [d for d, _ in batch])):
                if events is None:
                    failed |= {g.id for g in day_games}
                    continue
                by_teams = games_by_nicknames(db, day)
                wanted = {g.id for g in day_games}
                for ev in events:
                    game = by_teams.get((nickname(ev["home"]), nickname(ev["away"])))
                    if game is not None and game.id in wanted:
                        matched.append((ev, game))
            for (ev, game), rows in zip(matched, pool.map(odds_of, [ev["event_id"] for ev, _ in matched])):
                if rows is None:
                    failed.add(game.id)   # error de red: se reintenta en la próxima corrida
                    continue
                game.espn_event_id = ev["event_id"]
                if game.tipoff_utc is None:
                    game.tipoff_utc = ev["start"]
                store_odds(db, game.id, "ESPN", rows, ev["start"], replace_snapshots=("OPEN", "CLOSE"))
                with_odds += bool(rows)
            now = utcnow()
            for _, day_games in batch:   # también los que ESPN no tenía: no se vuelven a pedir
                for g in day_games:
                    if g.id not in failed:
                        g.espn_odds_fetched_at = now
                fetched += len(day_games)
            db.commit()
            if (i // ESPN_DAYS_PER_BATCH) % 10 == 0:
                print(f"  ... {fetched} de {len(todo)} partidos ({with_odds} con cuotas)", flush=True)
            time.sleep(ESPN_PAUSE)
    return fetched, with_odds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solo", choices=["partidos", "calendario", "cuotas"], default=None)
    parser.add_argument("--max-cuotas", type=int, default=None, help="Tope de partidos a los que pedir cuotas en esta corrida")
    args = parser.parse_args()
    ensure_tables()
    db = SessionLocal()
    try:
        if args.solo in (None, "partidos"):
            last = int(last_finished_season(date.today())[:4])
            seasons = [sources.season_label(y) for y in range(FIRST_SEASON, last + 1)]
            n = import_games(db, seasons)
            print(f"NBA historial: {n} partidos nuevos/actualizados ({seasons[0]} a {seasons[-1]}).")
        if args.solo in (None, "calendario"):
            print(f"NBA calendario de la temporada en curso: {import_schedule(db)} partidos.")
        if args.solo in (None, "cuotas"):
            fetched, with_odds = import_espn_odds(db, sources.season_label(ODDS_FIRST_SEASON), args.max_cuotas)
            print(f"NBA cuotas históricas (ESPN): {fetched} partidos revisados, {with_odds} con cuotas.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
