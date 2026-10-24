"""Apartado NBA: mismas respuestas que el fútbol (app/api/predictions.py) para
que la página use los mismos componentes, sobre las tablas propias nba_*.

Los IDs de nba.com son texto de 10 dígitos ("0022600001"); la API los expone
como número (22600001) y los reconstruye con ceros a la izquierda.
"""
import json
from datetime import date, datetime, timezone

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.predictions import _CONFIDENCE_RANK, _DAY_QUERY, _TZ_QUERY, ConfidenceLevel, _day_bounds
from app.core.config import Settings, get_settings
from app.database import get_db
from app.models import NbaEvaluation, NbaGame, NbaOdds, NbaPlayerGame, NbaPrediction
from app.schemas import (
    DailyCandidateResponse, MarketRow, MatchAnalysisSummary, MatchMarketsResponse, MatchSummary, ModelVsMarketFamily,
    ModelVsMarketResponse, NbaStatus, PerformanceResponse, PickOfTheDayResponse,
)
from ml.nba.dataset import MODEL_SEASON_TYPES
from ml.nba.sources import fetch_espn_injuries, parse_espn_injuries, season_of
from ml.nba.store import nickname
from ml.nba.train import VALIDATION_PATH
from ml.value_betting.confidence import SkillEvidence, confidence_label
from ml.value_betting.ev_kelly import closing_line_value
from ml.value_betting.model_vs_market import EvaluationRow, compare, market_family
from jobs.nba_props import actual_value, parse_props_market, player_names, props_coverage
from ml.nba.props import load_props_model

router = APIRouter(prefix="/api/nba", tags=["nba"])

LEAGUE_LABEL = {"REGULAR": "NBA", "PLAYIN": "NBA · Play-in", "PLAYOFFS": "NBA · Playoffs", "CUP": "NBA · Copa",
                "PRESEASON": "NBA · Pretemporada"}


# kind=team: ganador, hándicap y total del partido. kind=player: líneas de
# jugadores (a prueba), con versión, picks y contador propios.
Kind = Literal["team", "player"]
_KIND = Query(default="team", description="team (mercados del partido) o player (líneas de jugadores, a prueba)")


def _version(kind: str, settings: Settings) -> str:
    return settings.nba_props_model_version if kind == "player" else settings.nba_model_version


def _names(db: Session, markets) -> dict[int, str]:
    return player_names(db, {p[0] for m in markets if (p := parse_props_market(m))})


def _gid(match_id: int) -> str:
    return f"{match_id:010d}"


def _score(g: NbaGame) -> str | None:
    return f"{g.home_pts}-{g.away_pts}" if g.status == "FINISHED" and g.home_pts is not None else None


def _to_response(p: NbaPrediction, cls=PickOfTheDayResponse, names: dict[int, str] | None = None,
                 db: Session | None = None):
    g = p.game
    prop = parse_props_market(p.market) if p.model_version.startswith("nba-props") else None
    actual = None
    if prop and db is not None and _score(g):
        pg = db.get(NbaPlayerGame, (g.id, prop[0]))
        actual = int(actual_value(pg, prop[1])) if pg is not None else None
    elif p.market.startswith("TOTAL_") and _score(g):
        actual = g.home_pts + g.away_pts
    return cls(
        match_id=int(g.id), home_team=g.home_team.name, away_team=g.away_team.name,
        league=LEAGUE_LABEL.get(g.season_type, "NBA"), kickoff_utc=g.tipoff_utc,
        market=p.market, selection=p.selection, bookmaker=p.bookmaker,
        bookmaker_odds=float(p.bookmaker_odds), model_probability=float(p.model_probability),
        implied_probability=float(p.implied_probability), expected_value=float(p.expected_value),
        kelly_fraction=float(p.kelly_fraction) if p.kelly_fraction is not None else None,
        historical_accuracy=None, historical_skill=None, backtest_n_bets=None,
        min_odds=round((1 + get_settings().min_pick_ev) / float(p.model_probability), 2),
        is_settled=p.settled_at is not None, is_win=p.is_win,
        profit_loss=float(p.profit_loss) if p.profit_loss is not None else None,
        final_score=_score(g),
        actual_stat=actual,
        player=(names or {}).get(prop[0]) if prop else None,
        closing_odds=float(p.closing_odds) if p.closing_odds is not None else None,
        # Sin historial de picks en vivo todavía: experimental (su contador es la validación).
        confidence_label=confidence_label(float(p.expected_value), SkillEvidence(None, None, None)),
        is_pick_of_day=p.is_pick_of_day,
    )


def _day_picks(db: Session, day, tz, only_picks: bool, settings: Settings, kind: str = "team") -> list[NbaPrediction]:
    start, end = _day_bounds(day, tz)
    q = (select(NbaPrediction).join(NbaGame, NbaPrediction.game_id == NbaGame.id)
         .where(NbaPrediction.model_version == _version(kind, settings),
                NbaGame.tipoff_utc >= start, NbaGame.tipoff_utc < end)
         .order_by(NbaPrediction.expected_value.desc()))
    if only_picks:
        q = q.where(NbaPrediction.is_pick_of_day.is_(True))
    else:
        q = q.where(NbaPrediction.bookmaker_odds >= settings.odds_min, NbaPrediction.bookmaker_odds <= settings.odds_max,
                    NbaPrediction.expected_value >= settings.min_pick_ev)
    return db.execute(q).scalars().all()


def _filter(responses, min_confidence):
    if min_confidence is None:
        return responses
    rank = _CONFIDENCE_RANK[min_confidence]
    return [r for r in responses if _CONFIDENCE_RANK[r.confidence_label] >= rank]


@router.get("/top-picks", response_model=list[PickOfTheDayResponse])
def top_picks(min_confidence: ConfidenceLevel | None = None, day: date | None = _DAY_QUERY,
              tz: str | None = _TZ_QUERY, kind: Kind = _KIND, db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings)):
    picks = _day_picks(db, day, tz, True, settings, kind)
    names = _names(db, [p.market for p in picks])
    responses = _filter([_to_response(p, names=names, db=db) for p in picks], min_confidence)
    if not responses:
        raise HTTPException(404, "No hay picks de la NBA ese día.")
    return responses


@router.get("/daily-candidates", response_model=list[DailyCandidateResponse])
def daily_candidates(min_confidence: ConfidenceLevel | None = None, day: date | None = _DAY_QUERY,
                     tz: str | None = _TZ_QUERY, kind: Kind = _KIND, db: Session = Depends(get_db),
                     settings: Settings = Depends(get_settings)):
    picks = _day_picks(db, day, tz, False, settings, kind)
    names = _names(db, [p.market for p in picks])
    return _filter([_to_response(p, DailyCandidateResponse, names, db) for p in picks], min_confidence)


@router.get("/games/today", response_model=list[MatchSummary])
def games_of_day(day: date | None = _DAY_QUERY, tz: str | None = _TZ_QUERY, kind: Kind = _KIND,
                 db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Todos los partidos del día (en la NBA todos los equipos tienen historial)."""
    start, end = _day_bounds(day, tz)
    games = db.execute(select(NbaGame).where(NbaGame.tipoff_utc >= start, NbaGame.tipoff_utc < end)
                       .order_by(NbaGame.tipoff_utc)).scalars().all()
    ids = [g.id for g in games]
    n_markets = dict(db.execute(
        select(NbaEvaluation.game_id, func.count()).where(NbaEvaluation.game_id.in_(ids),
                                                          NbaEvaluation.model_version == _version(kind, settings))
        .group_by(NbaEvaluation.game_id)).all())
    picked = set(db.execute(select(NbaPrediction.game_id).where(
        NbaPrediction.game_id.in_(ids), NbaPrediction.is_pick_of_day.is_(True),
        NbaPrediction.model_version == _version(kind, settings))).scalars())
    version = _version(kind, settings)
    preds_stmt = (
        select(NbaPrediction)
        .where(NbaPrediction.game_id.in_(ids), NbaPrediction.model_version == version)
        .order_by(NbaPrediction.is_pick_of_day.desc(), NbaPrediction.expected_value.desc())
    )
    preds = db.execute(preds_stmt).scalars().all() if ids else []
    top_pred_by_game: dict[str, NbaPrediction] = {}
    for p in preds:
        top_pred_by_game.setdefault(p.game_id, p)

    missing_ids = [gid for gid in ids if gid not in top_pred_by_game]
    top_eval_by_game: dict[str, NbaEvaluation] = {}
    if missing_ids:
        evals = db.execute(
            select(NbaEvaluation)
            .where(NbaEvaluation.game_id.in_(missing_ids), NbaEvaluation.model_version == version)
        ).scalars().all()
        for e in evals:
            cur = top_eval_by_game.get(e.game_id)
            if cur is None:
                top_eval_by_game[e.game_id] = e
            else:
                e_key = (float(e.expected_value) < settings.min_pick_ev or float(e.expected_value) > settings.max_plausible_ev, -float(e.expected_value))
                cur_key = (float(cur.expected_value) < settings.min_pick_ev or float(cur.expected_value) > settings.max_plausible_ev, -float(cur.expected_value))
                if e_key < cur_key:
                    top_eval_by_game[e.game_id] = e

    all_markets = [p.market for p in top_pred_by_game.values()] + [e.market for e in top_eval_by_game.values()]
    player_map = _names(db, all_markets)

    def build_analysis(gid: str) -> MatchAnalysisSummary | None:
        p = top_pred_by_game.get(gid)
        if p is not None:
            prop = parse_props_market(p.market) if kind == "player" else None
            p_name = player_map.get(prop[0]) if prop else None
            odds_val = float(p.bookmaker_odds)
            return MatchAnalysisSummary(
                market=p.market,
                selection=p.selection,
                bookmaker=p.bookmaker,
                odds=odds_val,
                model_probability=float(p.model_probability),
                implied_probability=float(p.implied_probability),
                expected_value=float(p.expected_value),
                validation="VALIDADO",
                is_recommended=bool(p.is_pick_of_day),
                confidence_label=confidence_label(float(p.expected_value), SkillEvidence(None, None, None)),
                is_settled=p.settled_at is not None,
                is_win=p.is_win,
                profit_loss=float(p.profit_loss) if p.profit_loss is not None else None,
                player=p_name,
            )
        e = top_eval_by_game.get(gid)
        if e is not None:
            prop = parse_props_market(e.market) if kind == "player" else None
            p_name = player_map.get(prop[0]) if prop else None
            odds_val = float(e.best_odds)
            return MatchAnalysisSummary(
                market=e.market,
                selection=e.selection,
                bookmaker=e.best_bookmaker,
                odds=odds_val,
                model_probability=float(e.model_probability),
                implied_probability=(1.0 / odds_val) if odds_val > 0 else 0.0,
                expected_value=float(e.expected_value),
                validation="SIN_VALIDAR",
                is_recommended=False,
                confidence_label=confidence_label(float(e.expected_value), SkillEvidence(None, None, None)),
                is_settled=e.settled_at is not None,
                is_win=e.is_win,
                profit_loss=((odds_val - 1.0) if e.is_win else -1.0) if e.is_win is not None else None,
                player=p_name,
            )
        return None

    return [MatchSummary(match_id=int(g.id), home_team=g.home_team.name, away_team=g.away_team.name,
                         league=LEAGUE_LABEL.get(g.season_type, "NBA"), kickoff_utc=g.tipoff_utc,
                         n_markets=n_markets.get(g.id, 0), has_pick=g.id in picked, status=g.status,
                         final_score=_score(g),
                         top_analysis=build_analysis(g.id))
            for g in games]


@router.get("/games/{match_id}/markets", response_model=MatchMarketsResponse)
def game_markets(match_id: int, kind: Kind = _KIND, db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    """Mercados evaluados del partido (los guarda jobs/nba_update), ordenados
    con el mismo criterio que el fútbol: primero lo que tiene EV real."""
    g = db.get(NbaGame, _gid(match_id))
    if g is None:
        raise HTTPException(404, "Partido no encontrado.")
    version = _version(kind, settings)
    evals = db.execute(select(NbaEvaluation).where(NbaEvaluation.game_id == g.id,
                                                    NbaEvaluation.model_version == version)).scalars().all()
    if not evals:
        raise HTTPException(404, "Este partido aún no tiene " + ("líneas de jugadores evaluadas." if kind == "player"
                                                                 else "cuotas evaluadas."))
    picked = set(db.execute(select(NbaPrediction.market, NbaPrediction.selection).where(
        NbaPrediction.game_id == g.id, NbaPrediction.is_pick_of_day.is_(True),
        NbaPrediction.model_version == version)).all())
    names = _names(db, [e.market for e in evals])
    rows = [MarketRow(
        market=e.market, selection=e.selection, bookmaker=e.best_bookmaker or "", odds=float(e.best_odds),
        model_probability=float(e.model_probability), implied_probability=1 / float(e.best_odds),
        expected_value=float(e.expected_value), historical_skill=None, backtest_n_bets=None, validation="SIN_VALIDAR",
        in_odds_range=settings.odds_min <= float(e.best_odds) <= settings.odds_max,
        ev_suspicious=float(e.expected_value) > settings.max_plausible_ev,
        is_pick=(e.market, e.selection) in picked,
        player=names.get(p[0]) if (p := parse_props_market(e.market)) and kind == "player" else None,
    ) for e in evals]
    rows.sort(key=lambda r: (r.expected_value < settings.min_pick_ev or r.ev_suspicious, -r.expected_value))
    history = lambda team_id: db.execute(select(func.count(NbaGame.id)).where(  # noqa: E731
        NbaGame.status == "FINISHED", (NbaGame.home_team_id == team_id) | (NbaGame.away_team_id == team_id))).scalar_one()
    return MatchMarketsResponse(match_id=match_id, home_team=g.home_team.name, away_team=g.away_team.name,
                                league=LEAGUE_LABEL.get(g.season_type, "NBA"), kickoff_utc=g.tipoff_utc,
                                home_history=history(g.home_team_id), away_history=history(g.away_team_id), rows=rows)


_injuries_cache: dict = {"at": None, "data": {}}
INJURIES_TTL_SECONDS = 1800


@router.get("/games/{match_id}/injuries")
def game_injuries(match_id: int, db: Session = Depends(get_db)):
    """Parte de lesiones de ESPN (gratis) de los dos equipos. En la NBA una
    baja de última hora mueve mucho la línea: si un titular sale como "Fuera"
    después de tomar la cuota, el precio ya no es el mismo."""
    g = db.get(NbaGame, _gid(match_id))
    if g is None:
        raise HTTPException(404, "Partido no encontrado.")
    now = datetime.now(timezone.utc)
    if _injuries_cache["at"] is None or (now - _injuries_cache["at"]).total_seconds() > INJURIES_TTL_SECONDS:
        try:
            _injuries_cache["data"] = parse_espn_injuries(fetch_espn_injuries())
            _injuries_cache["at"] = now
        except RuntimeError:
            if _injuries_cache["at"] is None:
                raise HTTPException(502, "No se pudo leer el parte de lesiones de ESPN.")
    data = _injuries_cache["data"]
    return {"updated_at": _injuries_cache["at"], "home_team": g.home_team.name, "away_team": g.away_team.name,
            "home": data.get(nickname(g.home_team.name), []), "away": data.get(nickname(g.away_team.name), [])}


@router.get("/performance", response_model=PerformanceResponse)
def performance(kind: Kind = _KIND, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    version = _version(kind, settings)
    all_preds = db.execute(select(NbaPrediction).where(
        NbaPrediction.model_version == version)
    ).scalars().all()
    # Anuladas (el jugador no jugó): ni ganadas ni perdidas, fuera de la cuenta.
    settled = [p for p in all_preds if p.settled_at is not None and p.is_win is not None]
    valid_preds = [p for p in all_preds if not (p.settled_at is not None and p.is_win is None)]
    wins = sum(1 for p in settled if p.is_win)
    pnl = sum(float(p.profit_loss or 0) for p in settled)
    clvs = [closing_line_value(float(p.bookmaker_odds), float(p.closing_odds)) for p in valid_preds if p.closing_odds]

    rec_settled = [p for p in settled if p.is_pick_of_day]
    rec_wins = sum(1 for p in rec_settled if p.is_win)
    rec_pnl = sum(float(p.profit_loss or 0) for p in rec_settled)

    return PerformanceResponse(
        settled_picks=len(settled), wins=wins, losses=len(settled) - wins,
        hit_rate=wins / len(settled) if settled else None, profit_loss_units=round(pnl, 3),
        yield_pct=pnl / len(settled) * 100 if settled else None, pending_picks=len(valid_preds) - len(settled),
        recommended_settled=len(rec_settled),
        recommended_wins=rec_wins,
        recommended_losses=len(rec_settled) - rec_wins,
        recommended_hit_rate=rec_wins / len(rec_settled) if rec_settled else None,
        recommended_profit_loss=round(rec_pnl, 3),
        recommended_yield_pct=rec_pnl / len(rec_settled) * 100 if rec_settled else None,
        clv_picks=len(clvs), avg_clv_pct=sum(clvs) / len(clvs) * 100 if clvs else None,
        positive_clv_rate=sum(c > 0 for c in clvs) / len(clvs) if clvs else None,
    )


@router.get("/model-vs-market", response_model=ModelVsMarketResponse)
def model_vs_market(kind: Kind = _KIND, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    version = _version(kind, settings)

    def family_market(market: str) -> str:
        p = parse_props_market(market) if kind == "player" else None
        return f"PROP_{p[1]}" if p else market

    evaluated = db.execute(select(func.count(NbaEvaluation.id)).where(NbaEvaluation.model_version == version)).scalar_one()
    settled = db.execute(select(NbaEvaluation).where(NbaEvaluation.is_win.isnot(None),
                                                      NbaEvaluation.model_version == version)).scalars().all()
    comparisons = compare([EvaluationRow(e.game_id, family_market(e.market), float(e.model_probability),
                                         float(e.market_fair_probability), bool(e.is_win))
                           for e in settled if e.market_fair_probability is not None])
    value: dict[str, list[float]] = {}
    for e in settled:
        ev, odds = float(e.expected_value), float(e.best_odds)
        if settings.min_pick_ev <= ev <= settings.max_plausible_ev and settings.odds_min <= odds <= settings.odds_max:
            value.setdefault(market_family(family_market(e.market)), []).append(odds - 1 if e.is_win else -1.0)
    return ModelVsMarketResponse(evaluated=evaluated, settled=len(settled), families=[
        ModelVsMarketFamily(
            family=c.family, n_matches=c.n_matches, n_markets=c.n_markets, brier_model=round(c.brier_model, 4),
            brier_market=round(c.brier_market, 4), t_stat=round(c.t_stat, 2) if c.t_stat is not None else None,
            verdict=c.verdict, value_bets=len(value.get(c.family, [])),
            value_bets_won=sum(1 for x in value.get(c.family, []) if x > 0),
            value_bets_pnl=round(sum(value.get(c.family, [])), 2),
        ) for c in comparisons])


@router.get("/status", response_model=NbaStatus)
def status(db: Session = Depends(get_db)):
    """Qué datos hay, cuándo empieza la temporada y qué dijo la validación."""
    now = datetime.now(timezone.utc)
    finished = db.execute(select(func.count(NbaGame.id), func.min(NbaGame.season), func.max(NbaGame.season)).where(
        NbaGame.status == "FINISHED", NbaGame.season_type.in_(MODEL_SEASON_TYPES))).one()
    with_odds = db.execute(select(func.count(func.distinct(NbaOdds.game_id))).where(
        NbaOdds.source == "ESPN", NbaOdds.snapshot == "CLOSE")).scalar_one()
    nxt = db.execute(select(NbaGame).where(NbaGame.status == "SCHEDULED", NbaGame.tipoff_utc > now)
                     .order_by(NbaGame.tipoff_utc).limit(1)).scalars().first()
    season_start = db.execute(select(func.min(NbaGame.tipoff_utc)).where(
        NbaGame.season_type == "REGULAR", NbaGame.season == season_of(now.date()))).scalar_one_or_none()
    try:
        validation = json.loads(VALIDATION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        validation = None
    return NbaStatus(
        games=finished[0], first_season=finished[1], last_season=finished[2], games_with_closing_odds=with_odds,
        next_game_utc=nxt.tipoff_utc if nxt else None,
        next_game=f"{nxt.away_team.name} en {nxt.home_team.name}" if nxt else None,
        next_game_preseason=nxt.season_type == "PRESEASON" if nxt else False,
        regular_season_start_utc=season_start,
        regular_season_started=season_start is not None and season_start <= now, validation=validation,
        props_validation=props_summary(), props_coverage=props_coverage(db),
    )


def props_summary() -> dict | None:
    """Lo que la página muestra de la validación del modelo de jugadores."""
    model = load_props_model()
    if model is None:
        return None
    v = model.get("validation", {})
    return {"w_live": model.get("w_live", 0.0), "trained_at": model.get("trained_at"),
            "test_seasons": v.get("test_seasons"), "outcomes": v.get("outcomes"),
            "market": v.get("market")}
