"""Apartado NBA: calendario, resultados, cuotas, picks y liquidación.

Gratis (nba.com y ESPN, sin cuota):
  1. calendario de la temporada y marcadores (cdn.nba.com);
  2. estadísticas de los partidos jugados de la temporada (stats.nba.com);
  3. líneas de ESPN de los partidos de las próximas horas y de cierre de los
     ya jugados;
  4. picks y liquidación.
Con --apisports, además, cuotas de Betano, Pinnacle, Bet365... de la API de
baloncesto (misma clave que fútbol, cuota APARTE de 100 diarias): 1 consulta
por día + 1 por partido; no repite partidos con cuotas de menos de 2 horas.

Líneas de jugadores (a prueba, contador propio): jobs/nba_props.py, con las
estadísticas de nba.com, las líneas de ESPN y las de la API de baloncesto que
llegan en la misma respuesta de cuotas (sin consultas extra).

La pretemporada no genera picks: los titulares juegan poco y los marcadores
no dicen nada de la temporada.

Uso:
    python -m jobs.nba_update [--apisports] [--horas 36]
"""
import argparse
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import NbaEvaluation, NbaGame, NbaOdds, NbaPrediction
from ml.ingestion import api_usage
from ml.nba import sources
from ml.nba.dataset import MODEL_SEASON_TYPES, load_games, odds_by_game
from ml.nba.import_history import import_espn_odds, import_schedule
from ml.nba.markets import (
    MAX_LINE_GAP, blend, line_gap, market_expectation, market_line, probability, reference_probability,
)
from ml.nba.ratings import Params, replay
from ml.nba.store import ensure_tables, games_by_nicknames, nickname, store_odds, upsert_games
from ml.nba.train import PARAMS_PATH
from ml.value_betting.ev_kelly import evaluate_bet, is_safe_bet_candidate
from ml.value_betting.market import fair_probabilities
from ml.value_betting.picks import merge_with_issued
from ml.value_betting.settlement import UnknownMarketError, bet_won, profit_loss
from jobs import nba_props

settings = get_settings()
DEFAULT_HOURS = 36
APISPORTS_FRESH = timedelta(hours=2)
DAILY_LIMIT = api_usage.DAILY_LIMIT
# Fuera de este rango la normal es poco fiable (colas) y la línea es muy
# alternativa: no se evalúa.
PROB_RANGE = (0.2, 0.8)


def upcoming_games(db: Session, now: datetime, hours: float) -> list[NbaGame]:
    return db.execute(select(NbaGame).where(
        NbaGame.status == "SCHEDULED", NbaGame.season_type.in_(MODEL_SEASON_TYPES),
        NbaGame.tipoff_utc > now, NbaGame.tipoff_utc < now + timedelta(hours=hours),
    ).order_by(NbaGame.tipoff_utc)).scalars().all()


def update_results(db: Session, today: date) -> str:
    """Calendario + estadísticas de la temporada en curso + cierres de ESPN."""
    n_sched = import_schedule(db)
    season = sources.season_of(today)
    missing = db.execute(select(NbaGame.season_type).where(
        NbaGame.season == season, NbaGame.status == "FINISHED", NbaGame.home_fga.is_(None),
        NbaGame.season_type.in_(("REGULAR", "PLAYIN", "PLAYOFFS")),
    ).distinct()).scalars().all()
    labels = {v: k for k, v in sources.SEASON_TYPES.items()}
    n_box = 0
    for stype in missing:
        rows = sources.fetch_league_game_log(season, labels[stype])
        n_box += upsert_games(db, sources.parse_game_log(rows, season))
        db.commit()
    fetched, with_odds = import_espn_odds(db, season)
    return f"calendario {n_sched} partidos; estadísticas de {n_box}; cierres de ESPN de {with_odds} de {fetched}"


def fetch_espn_live(db: Session, games: list[NbaGame], now: datetime) -> int:
    wanted = {g.id for g in games}
    n = 0
    for day in sorted({g.game_date for g in games}):
        try:
            events = sources.parse_espn_events(sources.fetch_espn_scoreboard(day))
        except RuntimeError as exc:
            print(f"  [!] ESPN {day}: {exc}")
            continue
        by_teams = games_by_nicknames(db, day)
        for ev in events:
            game = by_teams.get((nickname(ev["home"]), nickname(ev["away"])))
            if game is None or game.id not in wanted:
                continue
            try:
                rows = sources.parse_espn_odds(sources.fetch_espn_odds(ev["event_id"]))
            except RuntimeError as exc:
                print(f"  [!] ESPN cuotas {ev['event_id']}: {exc}")
                continue
            game.espn_event_id = ev["event_id"]
            # Antes del partido, la línea "de arriba" de ESPN es la actual.
            for r in rows:
                if r["snapshot"] == "CLOSE":
                    r["snapshot"] = "LIVE"
            store_odds(db, game.id, "ESPN", rows, now, replace_snapshots=("OPEN",))
            n += bool(rows)
    db.commit()
    return n


def fetch_apisports_live(db: Session, games: list[NbaGame], now: datetime) -> str:
    path = api_usage.BASKETBALL_USAGE_PATH
    budget = DAILY_LIMIT - api_usage.used_today(path)
    if budget <= 0:
        return "sin consultas de baloncesto hoy (se renuevan a las 19:00)"
    fresh = set(db.execute(select(NbaOdds.game_id).where(
        NbaOdds.source == "APISPORTS", NbaOdds.recorded_at > now - APISPORTS_FRESH,
        NbaOdds.game_id.in_([g.id for g in games]))).scalars())
    todo = [g for g in games if g.id not in fresh]
    if not todo:
        return "las cuotas de Betano/Pinnacle tienen menos de 2 horas: no se repiten"
    fetched = props = unparsed = 0
    with sources.BasketballClient() as client:
        need_ids = [g for g in todo if g.apisports_game_id is None]
        for day in sorted({g.tipoff_utc.astimezone(timezone.utc).date() for g in need_ids}):
            if budget <= 0:
                break
            budget -= 1
            try:
                api_games = client.nba_games(day)
            except RuntimeError as exc:
                print(f"  [!] {exc}")
                continue
            by_teams = {(nickname(g["teams"]["home"]["name"]), nickname(g["teams"]["away"]["name"])): g["id"]
                        for g in api_games}
            for g in need_ids:
                key = (nickname(g.home_team.name), nickname(g.away_team.name))
                if g.tipoff_utc.astimezone(timezone.utc).date() == day and key in by_teams:
                    g.apisports_game_id = by_teams[key]
        for g in todo:
            if budget <= 0 or g.apisports_game_id is None:
                continue
            budget -= 1
            try:
                response = client.odds(g.apisports_game_id)
            except RuntimeError as exc:
                print(f"  [!] {exc}")
                continue
            rows = sources.parse_apisports_odds(response)
            store_odds(db, g.id, "APISPORTS", rows, now)
            n_props, n_unparsed = nba_props.store_apisports_props(db, g, response, now)
            props += n_props
            unparsed += n_unparsed
            fetched += bool(rows)
    db.commit()
    note = f"; líneas de jugadores: {props}" + (f" ({unparsed} sin entender)" if unparsed else "")
    return f"cuotas de Betano/Pinnacle/... de {fetched} de {len(todo)} partidos{note}"


def load_model() -> dict | None:
    try:
        return json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def generate(db: Session, games: list[NbaGame], now: datetime, snapshot: str = "LIVE", commit: bool = True) -> str:
    """`snapshot`/`commit` existen para probar en seco sobre partidos pasados
    (cuotas CLOSE, sin guardar nada): en vivo siempre LIVE y se guarda."""
    model = load_model()
    if model is None:
        return "falta entrenar el modelo (python -m ml.nba.train)"
    version = settings.nba_model_version
    ids = [g.id for g in games]
    # Idempotente: se rehacen los candidatos y evaluaciones sin liquidar de
    # estos partidos; los picks ya emitidos se quedan (ml/value_betting/picks.py).
    issued = db.execute(select(NbaPrediction).where(
        NbaPrediction.game_id.in_(ids), NbaPrediction.model_version == version,
        NbaPrediction.is_pick_of_day.is_(True))).scalars().all()
    db.execute(delete(NbaPrediction).where(NbaPrediction.game_id.in_(ids), NbaPrediction.model_version == version,
                                           NbaPrediction.settled_at.is_(None), NbaPrediction.is_pick_of_day.is_(False)))
    db.execute(delete(NbaEvaluation).where(NbaEvaluation.game_id.in_(ids), NbaEvaluation.model_version == version,
                                           NbaEvaluation.settled_at.is_(None)))
    preds, _ = replay(load_games(db), Params(**model["params"]))
    pred = preds.set_index("id")
    odds = odds_by_game(db, ids, snapshot)
    sd_m, sd_t = model["sd_margin"], model["sd_total"]
    candidates: list[NbaPrediction] = []
    n_eval = 0
    for g in games:
        markets = odds.get(g.id)
        if not markets or g.id not in pred.index:
            continue
        expect = market_expectation(markets, sd_m, sd_t)
        mu_m = blend(float(pred.at[g.id, "margin"]), expect["margin"], model["w_margin"])
        mu_t = blend(float(pred.at[g.id, "total"]), expect["total"], model["w_total"])
        for market, books in markets.items():
            line = market_line(market)
            if market != "ML" and (line is None or line == int(line)):
                continue   # línea entera: con devolución, no se liquida
            fair, n_books = fair_probabilities(market, books)
            best_any: dict[str, tuple[float, str]] = {}
            best_ok: dict[str, tuple[float, str]] = {}
            for book, sels in books.items():
                for sel, o in sels.items():
                    if sel not in best_any or o > best_any[sel][0]:
                        best_any[sel] = (o, book)
                    if settings.bookmaker_allowed(book) and (sel not in best_ok or o > best_ok[sel][0]):
                        best_ok[sel] = (o, book)
            # Con w = 0 (el modelo no le gana a la línea: ver validation.json) la
            # probabilidad es la justa de las DEMÁS casas en esa misma línea,
            # Pinnacle primero; el pick nace solo si Betano/Bwin pagan más.
            uses_model = (model["w_total"] if market.startswith("TOTAL_") else model["w_margin"]) > 0
            gap = line_gap(market, expect["margin"], expect["total"])
            near = market == "ML" or (gap is not None and gap <= MAX_LINE_GAP)
            for sel, (o_any, book_any) in best_any.items():
                o, book = best_ok.get(sel, (o_any, book_any))
                p = (probability(market, sel, mu_m, mu_t, sd_m, sd_t) if uses_model
                     else reference_probability(market, sel, books, mu_m, mu_t, sd_m, sd_t, exclude=book))
                if p is None or not PROB_RANGE[0] <= p <= PROB_RANGE[1]:
                    continue
                bet = evaluate_bet(p, o, settings.kelly_fraction_cap)
                db.add(NbaEvaluation(
                    game_id=g.id, model_version=version, market=market, selection=sel, model_probability=p,
                    market_fair_probability=fair.get(sel), n_bookmakers=n_books, best_odds=o, best_bookmaker=book,
                    expected_value=bet.expected_value, validation="SIN_VALIDAR", evaluated_at=now,
                ))
                n_eval += 1
                # Solo líneas cercanas a lo que espera el mercado: las apartadas
                # son justo las sospechosas de ser líneas viejas o erróneas.
                if sel in best_ok and near and is_safe_bet_candidate(
                    odds=o, expected_val=bet.expected_value, historical_skill=None,
                    odds_min=settings.odds_min, odds_max=settings.odds_max, min_ev=settings.min_pick_ev,
                    max_ev=settings.max_plausible_ev, require_validation=False,
                ):
                    candidates.append(NbaPrediction(
                        game_id=g.id, model_version=version, market=market, selection=sel, model_probability=p,
                        bookmaker=book, bookmaker_odds=o, implied_probability=bet.implied_probability,
                        expected_value=bet.expected_value, kelly_fraction=bet.kelly_fraction,
                        is_pick_of_day=False, generated_at=now,
                    ))
    candidates, chosen = merge_with_issued(
        candidates, issued, settings.nba_top_picks_count,
        lambda p: (p.game_id, p.market, p.selection), lambda p: [p.game_id])
    db.add_all(candidates)
    if commit:
        db.commit()
    else:
        db.flush()
    kept = f" (+{len(issued)} ya emitidos, se mantienen)" if issued else ""
    return f"{n_eval} mercados evaluados, {len(candidates)} candidatos, {len(chosen)} picks nuevos{kept}"


def _closing_odds(db: Session, p: NbaPrediction) -> tuple[float, str] | None:
    """Cuota de cierre del pick: la última de la misma casa antes del salto
    inicial; si no, la mejor de cierre de ESPN."""
    same_book = select(NbaOdds).where(
        NbaOdds.game_id == p.game_id, NbaOdds.market == p.market, NbaOdds.selection == p.selection,
        NbaOdds.bookmaker == p.bookmaker, NbaOdds.snapshot.in_(("LIVE", "CLOSE")))
    if p.game.tipoff_utc is not None:
        same_book = same_book.where(NbaOdds.recorded_at <= p.game.tipoff_utc)
    row = db.execute(same_book.order_by(NbaOdds.recorded_at.desc()).limit(1)).scalars().first()
    if row is None:
        row = db.execute(select(NbaOdds).where(
            NbaOdds.game_id == p.game_id, NbaOdds.market == p.market, NbaOdds.selection == p.selection,
            NbaOdds.snapshot == "CLOSE").order_by(NbaOdds.odds_value.desc()).limit(1)).scalars().first()
    return (float(row.odds_value), row.bookmaker) if row else None


def settle(db: Session, now: datetime) -> str:
    finished = select(NbaGame.id).where(NbaGame.status == "FINISHED", NbaGame.home_pts.isnot(None))
    # Solo los mercados de equipo: los de jugadores se liquidan con su estadística (nba_props).
    team = settings.nba_model_version
    picks = db.execute(select(NbaPrediction).where(NbaPrediction.settled_at.is_(None), NbaPrediction.model_version == team,
                                                    NbaPrediction.game_id.in_(finished))).scalars().all()
    wins = settled = 0
    for p in picks:
        try:
            won = bet_won(p.market, p.selection, p.game.home_pts, p.game.away_pts)
        except UnknownMarketError as exc:
            print(f"  [!] {exc}")
            continue
        p.is_win, p.profit_loss, p.settled_at = won, profit_loss(won, float(p.bookmaker_odds)), now
        closing = _closing_odds(db, p)
        if closing:
            p.closing_odds, p.closing_bookmaker = closing
        settled += 1
        wins += won
    evals = db.execute(select(NbaEvaluation).where(NbaEvaluation.settled_at.is_(None), NbaEvaluation.model_version == team,
                                                    NbaEvaluation.game_id.in_(finished))).scalars().all()
    n_eval = 0
    for e in evals:
        try:
            e.is_win = bet_won(e.market, e.selection, e.game.home_pts, e.game.away_pts)
        except UnknownMarketError:
            continue
        e.settled_at = now
        n_eval += 1
    db.commit()
    return f"{settled} picks liquidados ({wins} ganados), {n_eval} mercados evaluados liquidados"


def next_game(db: Session, now: datetime) -> NbaGame | None:
    return db.execute(select(NbaGame).where(NbaGame.status == "SCHEDULED", NbaGame.tipoff_utc > now,
                                            NbaGame.season_type.in_(MODEL_SEASON_TYPES))
                      .order_by(NbaGame.tipoff_utc).limit(1)).scalars().first()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apisports", action="store_true",
                        help="Además, cuotas de Betano/Pinnacle/... (cuota de la API de baloncesto)")
    parser.add_argument("--horas", type=float, default=DEFAULT_HOURS, help="Partidos que empiezan dentro de estas horas")
    args = parser.parse_args()
    ensure_tables()
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        print("Resultados:", update_results(db, now.date()))
        print("Estadísticas de jugadores:", nba_props.update_player_logs(db, now.date()), "filas nuevas")
        print("Liquidación:", settle(db, now))
        print("Liquidación de jugadores:", nba_props.settle_props(db, now))
        games = upcoming_games(db, now, args.horas)
        if not games:
            nxt = next_game(db, now)
            when = f" El próximo es el {nxt.tipoff_utc:%d/%m %H:%M} UTC." if nxt else ""
            print(f"Ningún partido de temporada en las próximas {args.horas:g} h.{when}")
            return
        print(f"Partidos en las próximas {args.horas:g} h: {len(games)}")
        print("Líneas de ESPN:", fetch_espn_live(db, games, now), "partidos")
        print("Líneas de jugadores de ESPN:", nba_props.fetch_espn_props_live(db, games, now))
        if args.apisports:
            print("API de baloncesto:", fetch_apisports_live(db, games, now))
        print("Picks:", generate(db, games, now))
        print("Picks de jugadores:", nba_props.generate_props(db, games, now))
    finally:
        db.close()


if __name__ == "__main__":
    main()
