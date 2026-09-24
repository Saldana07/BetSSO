"""Líneas de jugadores de la NBA en vivo: estadísticas, líneas, picks y liquidación.

Lo llama jobs/nba_update.py en cada "Actualizar NBA". Todo gratis salvo las
líneas de la API de baloncesto, que llegan en la misma respuesta de /odds que
ya se pide para los mercados de equipo (sin consultas extra).

Picks a prueba, con versión y contador propios (settings.nba_props_model_version):
  - la probabilidad con la que se decide es la de la casa de referencia
    (DraftKings o Pinnacle sin margen, en esa misma línea) + w · (modelo − esa),
    con w de la validación (models/nba/props.json): w = 0 mientras el modelo
    no le gane a las líneas de cierre reales;
  - nunca un jugador "Fuera" o en duda en el parte de lesiones, ni uno recién
    traspasado (menos de 3 partidos con su equipo actual).
Las evaluaciones guardan la probabilidad del MODELO (no la mezcla): así
"Modelo contra la casa" mide al modelo aunque todavía no se use.
"""
from collections import defaultdict
from datetime import date, datetime

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import NbaEvaluation, NbaGame, NbaPlayerGame, NbaPrediction, NbaPropOdds
from ml.nba import player_sources, sources
from ml.nba.import_history import store_espn_props_for_game
from ml.nba.player_sources import COMBOS, match_player
from ml.nba.props import PropParams, latest_states, load_player_frame, load_props_model, prob, project_player, team_context
from ml.nba.sources import line_key
from ml.nba.store import game_players, nickname, store_props, upsert_player_games
from ml.value_betting.ev_kelly import evaluate_bet, is_safe_bet_candidate
from ml.value_betting.market import fair_probabilities

settings = get_settings()
PROB_RANGE = (0.2, 0.8)


def props_market(player_id: int, stat: str, line: float) -> str:
    return f"P{player_id}_{stat}_{line_key(line)}"


def parse_props_market(market: str) -> tuple[int, str, float] | None:
    """"P1629029_PRA_45.5" -> (1629029, "PRA", 45.5)."""
    if not market.startswith("P"):
        return None
    try:
        pid, stat, line = market[1:].split("_")
        return int(pid), stat, float(line)
    except ValueError:
        return None


# --- Datos -------------------------------------------------------------------------

def update_player_logs(db: Session, today: date) -> int:
    """Estadísticas de los jugadores de la temporada en curso (1 petición por
    tipo de partido), si hay partidos jugados que aún no las tienen."""
    season = sources.season_of(today)
    has_rows = select(NbaPlayerGame.game_id).distinct()
    missing = db.execute(select(NbaGame.season_type).where(
        NbaGame.season == season, NbaGame.status == "FINISHED", NbaGame.id.not_in(has_rows),
        NbaGame.season_type.in_(("REGULAR", "PLAYIN", "PLAYOFFS"))).distinct()).scalars().all()
    labels = {v: k for k, v in sources.SEASON_TYPES.items()}
    n = 0
    for stype in missing:
        n += upsert_player_games(db, player_sources.parse_player_log(player_sources.fetch_player_game_log(season, labels[stype])))
        db.commit()
    return n


def fetch_espn_props_live(db: Session, games: list[NbaGame], now: datetime) -> int:
    n = 0
    for g in games:
        if g.espn_event_id:
            n += store_espn_props_for_game(db, g, "LIVE", now)
    db.commit()
    return n


def store_apisports_props(db: Session, game: NbaGame, response: list[dict], now: datetime) -> tuple[int, int]:
    """Líneas de jugadores de la respuesta de /odds ya pedida. (guardadas, sin entender)."""
    rows, unparsed = player_sources.parse_apisports_props(response)
    if not rows:
        return 0, unparsed
    candidates = game_players(db, game)
    by_book: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        r["player_id"] = match_player(r["player_name"], candidates) if candidates else None
        r["snapshot"] = "LIVE"
        by_book[r["bookmaker"]].append(r)
    for book, book_rows in by_book.items():
        store_props(db, game.id, "APISPORTS", book, book_rows, now)
    return len(rows), unparsed


def latest_lines(db: Session, game_ids: list[str]) -> dict:
    """{partido: {(jugador, stat, línea): {casa: {lado: cuota}}}} con la última
    foto de cada casa (una línea que la casa movió deja de contar)."""
    rows = db.execute(select(NbaPropOdds).where(NbaPropOdds.game_id.in_(game_ids), NbaPropOdds.snapshot == "LIVE",
                                                NbaPropOdds.player_id.isnot(None))).scalars().all()
    last_at: dict = {}
    for o in rows:
        key = (o.game_id, o.source, o.bookmaker)
        last_at[key] = max(last_at.get(key, o.recorded_at), o.recorded_at)
    out: dict = defaultdict(lambda: defaultdict(dict))
    for o in rows:
        if o.recorded_at == last_at[(o.game_id, o.source, o.bookmaker)]:
            key = (o.player_id, o.stat, float(o.line))
            out[o.game_id][key].setdefault(o.bookmaker, {})[o.side] = max(
                float(o.odds_value), out[o.game_id][key].get(o.bookmaker, {}).get(o.side, 0.0))
    return out


def injury_flags(db: Session, games: list[NbaGame]) -> tuple[set[int], set[int]]:
    """(fuera, en duda) según el parte de lesiones de ESPN, ya emparejados con
    los jugadores de cada partido."""
    try:
        report = sources.parse_espn_injuries(sources.fetch_espn_injuries())
    except RuntimeError:
        return set(), set()
    out, doubt = set(), set()
    for g in games:
        candidates = game_players(db, g)
        for team in (g.home_team, g.away_team):
            for inj in report.get(nickname(team.name), []):
                pid = match_player(inj["player"], candidates)
                if pid is not None:
                    (out if inj["out"] else doubt).add(pid)
    return out, doubt


# --- Picks -------------------------------------------------------------------------

def _reference(books: dict[str, dict[str, float]], exclude: str | None) -> dict[str, float] | None:
    """Probabilidad justa de la línea según las DEMÁS casas: Pinnacle si está,
    si no la mediana de las otras con los dos lados."""
    others = {b: s for b, s in books.items() if b != exclude}
    for pool in ({"Pinnacle": others["Pinnacle"]} if "Pinnacle" in others else None, others):
        if pool:
            fair, n = fair_probabilities("TOTAL_0.5", pool)   # dos lados OVER/UNDER, como un total
            if n:
                return fair
    return None


def generate_props(db: Session, games: list[NbaGame], now: datetime, commit: bool = True) -> str:
    model = load_props_model()
    if model is None:
        return "falta entrenar el modelo de jugadores (python -m ml.nba.props)"
    version = settings.nba_props_model_version
    params, alphas, w = PropParams(**model["params"]), model["alphas"], float(model.get("w_live", 0.0))
    ids = [g.id for g in games]
    for table in (NbaPrediction, NbaEvaluation):
        db.execute(delete(table).where(table.game_id.in_(ids), table.model_version == version, table.settled_at.is_(None)))
    lines = latest_lines(db, ids)
    if not lines:
        if commit:
            db.commit()
        return "sin líneas de jugadores para estos partidos"
    states = latest_states(load_player_frame(db), params)
    ctx = team_context(db)
    out_ids, doubt_ids = injury_flags(db, games)
    candidates: list[NbaPrediction] = []
    n_eval = skipped = 0
    for g in games:
        if g.id not in lines or g.id not in ctx.index:
            continue
        ctx_row = ctx.loc[g.id]
        projections: dict[int, dict | None] = {}
        for (pid, stat, line), books in lines[g.id].items():
            if line != int(line) + 0.5 or pid in out_ids:
                continue
            if pid not in projections:
                st = states.loc[pid] if pid in states.index else None
                ok = (st is not None and st["n_games"] >= params.min_games
                      and st["team_id"] in (g.home_team_id, g.away_team_id))
                projections[pid] = (project_player(st, ctx_row, st["team_id"] == g.home_team_id, params), st) if ok else None
            if projections[pid] is None:
                skipped += 1
                continue
            mu, st = projections[pid]
            fair_all, n_books = fair_probabilities("TOTAL_0.5", books)
            best_any: dict[str, tuple[float, str]] = {}
            best_ok: dict[str, tuple[float, str]] = {}
            for book, sels in books.items():
                for side, o in sels.items():
                    if side not in best_any or o > best_any[side][0]:
                        best_any[side] = (o, book)
                    if settings.bookmaker_allowed(book) and (side not in best_ok or o > best_ok[side][0]):
                        best_ok[side] = (o, book)
            market = props_market(pid, stat, line)
            for side, (o_any, book_any) in best_any.items():
                o, book = best_ok.get(side, (o_any, book_any))
                p_model = prob(float(mu[stat]), line, side, alphas[stat])
                if not np.isfinite(p_model) or not PROB_RANGE[0] <= p_model <= PROB_RANGE[1]:
                    continue
                ref = _reference(books, exclude=book)
                p_use = (ref[side] + w * (p_model - ref[side])) if ref else (p_model if w > 0 else None)
                bet = evaluate_bet(p_use if p_use is not None else p_model, o, settings.kelly_fraction_cap)
                db.add(NbaEvaluation(
                    game_id=g.id, model_version=version, market=market, selection=side, model_probability=p_model,
                    market_fair_probability=fair_all.get(side), n_bookmakers=n_books, best_odds=o, best_bookmaker=book,
                    expected_value=bet.expected_value, validation="SIN_VALIDAR", evaluated_at=now,
                ))
                n_eval += 1
                if (side in best_ok and p_use is not None and pid not in doubt_ids and bool(st["same_team_3"])
                        and is_safe_bet_candidate(odds=o, expected_val=bet.expected_value, historical_skill=None,
                                                  odds_min=settings.odds_min, odds_max=settings.odds_max,
                                                  min_ev=settings.min_pick_ev, max_ev=settings.max_plausible_ev,
                                                  require_validation=False)):
                    candidates.append(NbaPrediction(
                        game_id=g.id, model_version=version, market=market, selection=side, model_probability=p_use,
                        bookmaker=book, bookmaker_odds=o, implied_probability=bet.implied_probability,
                        expected_value=bet.expected_value, kelly_fraction=bet.kelly_fraction, is_pick_of_day=False,
                        generated_at=now,
                    ))
    # Como máximo un pick por partido y por jugador.
    chosen, games_used, players_used = [], set(), set()
    for c in sorted(candidates, key=lambda c: -c.expected_value):
        pid = parse_props_market(c.market)[0]
        if c.game_id in games_used or pid in players_used:
            continue
        chosen.append(c)
        games_used.add(c.game_id)
        players_used.add(pid)
        if len(chosen) >= settings.nba_props_top_picks_count:
            break
    for c in chosen:
        c.is_pick_of_day = True
    db.add_all(candidates)
    if commit:
        db.commit()
    else:
        db.flush()
    return (f"{n_eval} líneas de jugadores evaluadas, {len(candidates)} candidatos, {len(chosen)} picks"
            f"{f' ({skipped} sin proyección: pocos partidos o traspasados)' if skipped else ''}")


# --- Liquidación -----------------------------------------------------------------

def actual_value(row: NbaPlayerGame, stat: str) -> float:
    return float(sum(getattr(row, s.lower()) for s in COMBOS.get(stat, (stat,))))


def settle_props(db: Session, now: datetime) -> str:
    """Con la estadística del jugador ya cargada. Si no jugó, la apuesta se
    anula (como en las casas): se marca liquidada sin ganar ni perder."""
    version = settings.nba_props_model_version
    with_stats = select(NbaPlayerGame.game_id).distinct()
    finished = select(NbaGame.id).where(NbaGame.status == "FINISHED", NbaGame.id.in_(with_stats))
    settled = voids = wins = 0
    for table in (NbaPrediction, NbaEvaluation):
        rows = db.execute(select(table).where(table.model_version == version, table.settled_at.is_(None),
                                              table.game_id.in_(finished))).scalars().all()
        for r in rows:
            parsed = parse_props_market(r.market)
            if parsed is None:
                continue
            pid, stat, line = parsed
            pg = db.get(NbaPlayerGame, (r.game_id, pid))
            r.settled_at = now
            if pg is None or float(pg.minutes or 0) <= 0:
                r.is_win = None
                if table is NbaPrediction:
                    r.profit_loss = 0.0
                    voids += 1
                continue
            value = actual_value(pg, stat)
            r.is_win = value > line if r.selection == "OVER" else value < line
            if table is NbaPrediction:
                r.profit_loss = (float(r.bookmaker_odds) - 1) if r.is_win else -1.0
                settled += 1
                wins += bool(r.is_win)
    db.commit()
    return f"{settled} picks de jugadores liquidados ({wins} ganados), {voids} anulados (no jugó)"


def player_names(db: Session, ids: set[int]) -> dict[int, str]:
    from app.models import NbaPlayer
    if not ids:
        return {}
    return dict(db.execute(select(NbaPlayer.id, NbaPlayer.name).where(NbaPlayer.id.in_(ids))).all())


def props_coverage(db: Session) -> dict:
    """Para la página: cuántas líneas históricas hay y de cuántos partidos."""
    n, g = db.execute(select(func.count(NbaPropOdds.id), func.count(func.distinct(NbaPropOdds.game_id))).where(
        NbaPropOdds.snapshot == "CLOSE")).one()
    since = db.execute(select(func.min(NbaGame.game_date)).join(NbaPropOdds, NbaPropOdds.game_id == NbaGame.id)
                       .where(NbaPropOdds.snapshot == "CLOSE")).scalar_one_or_none()
    return {"lines": n, "games": g, "since": since.isoformat() if since else None}

