from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.database import get_db
from app.models import Match, Prediction
from app.models import BacktestResult, Odds
from app.schemas import (
    DailyCandidateResponse,
    MarketRow,
    MatchMarketsResponse,
    MatchSummary,
    OutcomeProbs,
    PerformanceResponse,
    PickOfTheDayResponse,
    SimulationResponse,
    ValueCandidate,
)
from ml.serving.predict import get_models, match_context, predict_match_probabilities
from ml.value_betting.ev_kelly import expected_value as ev_of
from ml.simulation.monte_carlo import compare_with, simulate_match
from ml.value_betting.confidence import SkillEvidence, confidence_label
from ml.value_betting.ev_kelly import closing_line_value
from ml.value_betting.settlement import COUNT_MARKET_STATS

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

ConfidenceLevel = Literal["ALTA", "MEDIA", "BAJA"]
_CONFIDENCE_RANK = {"BAJA": 0, "MEDIA": 1, "ALTA": 2}


def _day_bounds(day: date | None = None):
    """[inicio, fin) en UTC del día pedido (hoy por defecto). La API y la BD
    trabajan en UTC; el frontend manda la fecha que el usuario eligió
    (ayer / hoy / mañana) ya en UTC."""
    if day is None:
        day = datetime.now(timezone.utc).date()
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)

_DAY_QUERY = Query(default=None, description="Día (UTC) a consultar, YYYY-MM-DD. Por defecto hoy.")


def _actual_stat(market: str, match: Match) -> int | None:
    """Total real de la estadística del mercado de conteo (córners, tiros...)
    si ya está cargado; None para mercados de goles o si aún no llegó."""
    prefix, _, _ = market.rpartition("_")
    stat = COUNT_MARKET_STATS.get(prefix)
    if stat is None:
        return None
    value = match.stat_totals().get(stat)
    return int(value) if value is not None else None


def _to_response(pred: Prediction, cls=PickOfTheDayResponse):
    match = pred.match
    return cls(
        match_id=match.id,
        home_team=match.home_team.name,
        away_team=match.away_team.name,
        league=match.league.name,
        kickoff_utc=match.kickoff_utc,
        market=pred.market,
        selection=pred.selection,
        bookmaker=pred.bookmaker,
        bookmaker_odds=float(pred.bookmaker_odds),
        model_probability=float(pred.model_probability),
        implied_probability=float(pred.implied_probability),
        expected_value=float(pred.expected_value),
        kelly_fraction=float(pred.kelly_fraction) if pred.kelly_fraction is not None else None,
        historical_accuracy=float(pred.historical_accuracy) if pred.historical_accuracy is not None else None,
        historical_skill=float(pred.historical_skill) if pred.historical_skill is not None else None,
        backtest_n_bets=pred.backtest_n_bets,
        is_settled=pred.settled_at is not None,
        is_win=pred.is_win,
        profit_loss=float(pred.profit_loss) if pred.profit_loss is not None else None,
        final_score=(
            f"{match.home_goals}-{match.away_goals}"
            if match.status == "FINISHED" and match.home_goals is not None else None
        ),
        actual_stat=_actual_stat(pred.market, match),
        closing_odds=float(pred.closing_odds) if pred.closing_odds is not None else None,
        confidence_label=confidence_label(
            float(pred.expected_value),
            # Se reconstruye la evidencia desde la foto guardada en el pick:
            # skill = accuracy - base, así que base = accuracy - skill.
            SkillEvidence(
                accuracy=float(pred.historical_accuracy) if pred.historical_accuracy is not None else None,
                base_accuracy=(
                    float(pred.historical_accuracy) - float(pred.historical_skill)
                    if pred.historical_accuracy is not None and pred.historical_skill is not None
                    else None
                ),
                n_bets=pred.backtest_n_bets,
            ),
        ),
        is_pick_of_day=pred.is_pick_of_day,
    )


@router.get("/top-picks", response_model=list[PickOfTheDayResponse])
def get_top_picks(
    min_confidence: ConfidenceLevel | None = Query(
        default=None, description="Filtra dejando solo picks con esta confianza o mayor (BAJA < MEDIA < ALTA)."
    ),
    day: date | None = _DAY_QUERY,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Devuelve el top N de 'apuestas seguras' del día (mayor EV dentro del
    rango de cuota [odds_min, odds_max], como máximo un pick por partido),
    ordenadas por EV descendente.

    El job diario (jobs/generate_daily_pick.py) es responsable de poblar
    `predictions` con `is_pick_of_day=True` para el top N; este endpoint
    solo lee ese resultado precomputado (lectura rápida, sin recalcular el
    modelo en el request).
    """
    today_start, today_end = _day_bounds(day)

    stmt = (
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Prediction.is_pick_of_day.is_(True),
            Match.kickoff_utc >= today_start,
            Match.kickoff_utc < today_end,
        )
        .order_by(Prediction.expected_value.desc())
    )
    picks = db.execute(stmt).scalars().all()
    responses = [_to_response(p) for p in picks]

    if min_confidence is not None:
        min_rank = _CONFIDENCE_RANK[min_confidence]
        responses = [r for r in responses if _CONFIDENCE_RANK[r.confidence_label] >= min_rank]

    if not responses:
        when = "hoy" if day is None else f"el {day.isoformat()}"
        detail = (
            f"No hay picks con confianza {min_confidence} o mayor {when}."
            if min_confidence
            else f"No hay ninguna apuesta segura {when} que cumpla los criterios (cuota 1.50-2.00, +EV)."
        )
        raise HTTPException(status_code=404, detail=detail)

    return responses


def _best_1x2_odds(db: Session, match_id: int, settings: Settings) -> dict[str, float] | None:
    """Mejor cuota por selección del snapshot 1X2 más reciente (y no vencido)."""
    latest = db.execute(
        select(func.max(Odds.recorded_at)).where(
            Odds.match_id == match_id, Odds.market == "1X2", Odds.snapshot_type == "LIVE"
        )
    ).scalar_one_or_none()
    if latest is None:
        return None
    if (datetime.now(timezone.utc) - latest).total_seconds() / 3600 > settings.max_odds_age_hours:
        return None

    rows = db.execute(
        select(Odds.selection, func.max(Odds.odds_value))
        .where(Odds.match_id == match_id, Odds.market == "1X2", Odds.recorded_at == latest)
        .group_by(Odds.selection)
    ).all()
    odds = {selection: float(value) for selection, value in rows}
    return odds if {"HOME", "DRAW", "AWAY"} <= odds.keys() else None


@router.get("/{match_id}/simulation", response_model=SimulationResponse)
def get_simulation(
    match_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """10,000 simulaciones de Montecarlo del partido con incertidumbre sobre la
    fuerza de los equipos, comparadas con el clasificador y con el mercado.

    La semilla es el match_id: el resultado es reproducible entre recargas
    (si no, los % bailarían ±0.5% cada vez y parecería que algo cambió).
    """
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado.")

    ctx = match_context(db, match, get_models(model_version=settings.model_version))
    sim = simulate_match(
        ctx.lambda_home, ctx.lambda_away, ctx.home_history, ctx.away_history, seed=match_id
    )
    comparison = compare_with(sim, ctx.classifier, _best_1x2_odds(db, match_id, settings))

    return SimulationResponse(
        match_id=match.id,
        home_team=match.home_team.name,
        away_team=match.away_team.name,
        n_simulations=sim.n_simulations,
        home_history=ctx.home_history,
        away_history=ctx.away_history,
        expected_home_goals=sim.expected_home_goals,
        expected_away_goals=sim.expected_away_goals,
        monte_carlo=OutcomeProbs(**comparison.monte_carlo),
        ci_home=sim.ci_home,
        ci_draw=sim.ci_draw,
        ci_away=sim.ci_away,
        total_goals_distribution=sim.total_goals_distribution,
        top_scorelines=sim.top_scorelines,
        p_over_2_5=sim.p_over_2_5,
        p_btts=sim.p_btts,
        classifier=OutcomeProbs(**comparison.classifier),
        market_fair=OutcomeProbs(**comparison.market_fair) if comparison.market_fair else None,
        model_disagreement=comparison.model_disagreement,
        value_candidates=[ValueCandidate(**c) for c in comparison.value_candidates],
        conclusion=comparison.conclusion,
    )



@router.get("/matches/today", response_model=list[MatchSummary])
def get_todays_matches(day: date | None = _DAY_QUERY, db: Session = Depends(get_db)):
    """Partidos del día (hoy por defecto) que tienen cuotas sincronizadas —
    es decir, los que pasaron el filtro de historial suficiente (sync_odds
    solo pide cuotas de esos). Son los partidos que se pueden analizar
    mercado a mercado, haya o no picks estrictos."""
    start, end = _day_bounds(day)
    rows = db.execute(
        select(
            Match,
            func.count(func.distinct(Odds.market + "/" + Odds.selection)).label("n_markets"),
        )
        .join(Odds, Odds.match_id == Match.id)
        .where(Match.kickoff_utc >= start, Match.kickoff_utc < end, Odds.snapshot_type == "LIVE")
        .group_by(Match.id)
        .order_by(Match.kickoff_utc)
    ).all()

    picked = set(
        db.execute(
            select(Prediction.match_id).where(Prediction.is_pick_of_day.is_(True))
            .join(Match, Prediction.match_id == Match.id)
            .where(Match.kickoff_utc >= start, Match.kickoff_utc < end)
        ).scalars().all()
    )
    return [
        MatchSummary(
            match_id=m.id, home_team=m.home_team.name, away_team=m.away_team.name,
            league=m.league.name, kickoff_utc=m.kickoff_utc, n_markets=n, has_pick=m.id in picked,
            status=m.status,
            final_score=f"{m.home_goals}-{m.away_goals}" if m.status == "FINISHED" and m.home_goals is not None else None,
        )
        for m, n in rows
    ]


@router.get("/{match_id}/markets", response_model=MatchMarketsResponse)
def get_match_markets(
    match_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """TODOS los mercados con cuota de un partido, evaluados por el modelo y
    ordenados por EV: el primero es "lo más favorable". Junto a cada uno va la
    validación del mercado, porque un EV alto en un mercado sin habilidad
    demostrada no es una oportunidad, es el modelo equivocándose.

    Es una vista de ANÁLISIS: muestra todo. Los picks del día siguen siendo el
    filtro estricto (validado, en rango, EV plausible)."""
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Partido no encontrado.")

    probs = predict_match_probabilities(db, match, get_models(model_version=settings.model_version))
    diagnostics = probs.get("_diagnostics", {})

    # Mejor cuota por mercado/selección del snapshot más reciente.
    latest = db.execute(
        select(func.max(Odds.recorded_at)).where(Odds.match_id == match_id, Odds.snapshot_type == "LIVE")
    ).scalar_one_or_none()
    if latest is None:
        raise HTTPException(status_code=404, detail="Este partido no tiene cuotas sincronizadas.")
    odds_rows = db.execute(
        select(Odds).where(Odds.match_id == match_id, Odds.snapshot_type == "LIVE", Odds.recorded_at == latest)
    ).scalars().all()
    best: dict[tuple[str, str], Odds] = {}
    for o in odds_rows:
        key = (o.market, o.selection)
        if key not in best or o.odds_value > best[key].odds_value:
            best[key] = o

    # Evidencia de habilidad por mercado (fila más reciente de backtest_results).
    evidence: dict[str, BacktestResult] = {}
    for row in db.execute(
        select(BacktestResult).where(BacktestResult.model_version == settings.model_version)
        .order_by(BacktestResult.created_at.desc())
    ).scalars().all():
        evidence.setdefault(row.market, row)

    picked = {
        (p.market, p.selection)
        for p in db.execute(select(Prediction).where(Prediction.match_id == match_id, Prediction.is_pick_of_day.is_(True))).scalars().all()
    }

    rows: list[MarketRow] = []
    for (market, selection), o in best.items():
        p_model = probs.get(f"{market}_{selection}")
        if p_model is None:
            continue
        odds = float(o.odds_value)
        ev = ev_of(p_model, odds)
        ev_row = evidence.get(market)
        skill = None
        if ev_row is not None and ev_row.accuracy is not None and ev_row.base_accuracy is not None:
            skill = float(ev_row.accuracy) - float(ev_row.base_accuracy)
        validation = "SIN_VALIDAR" if skill is None else ("VALIDADO" if skill >= settings.min_historical_skill else "SIN_VENTAJA")
        rows.append(MarketRow(
            market=market, selection=selection, bookmaker=o.bookmaker, odds=odds,
            model_probability=float(p_model), implied_probability=1.0 / odds, expected_value=ev,
            historical_skill=skill, backtest_n_bets=ev_row.n_bets if ev_row else None,
            validation=validation,
            in_odds_range=settings.odds_min <= odds <= settings.odds_max,
            ev_suspicious=ev > settings.max_plausible_ev,
            is_pick=(market, selection) in picked,
        ))

    # "Lo más favorable" no puede ser simplemente el EV más alto: el EV más alto
    # es casi siempre el mayor error del modelo (por eso existe ev_suspicious).
    # Pero tampoco puede ser "lo validado aunque tenga EV negativo": en el
    # ensayo del 17/09 un córners con EV -11.8% salió primero solo por ser el
    # único mercado validado, y encima ganó — suerte disfrazada de acierto.
    # Orden: primero lo que tiene EV real (>= piso de pick), dentro de eso por
    # credibilidad (validado > sin validar > sin ventaja) y luego por EV; los
    # sospechosos al final de su grupo; todo lo sin EV real, al fondo.
    tier = {"VALIDADO": 0, "SIN_VALIDAR": 1, "SIN_VENTAJA": 2}
    # Un EV sospechoso tampoco es favorable: si el flag dice "probablemente error
    # del modelo", no puede ser el titular. Va al fondo con los sin EV real.
    rows.sort(key=lambda r: (r.expected_value < settings.min_pick_ev or r.ev_suspicious, tier[r.validation], -r.expected_value))
    return MatchMarketsResponse(
        match_id=match.id, home_team=match.home_team.name, away_team=match.away_team.name,
        league=match.league.name, kickoff_utc=match.kickoff_utc,
        home_history=diagnostics.get("home_history", 0), away_history=diagnostics.get("away_history", 0),
        rows=rows,
    )


@router.get("/performance", response_model=PerformanceResponse)
def get_performance(db: Session = Depends(get_db)):
    """Desempeño REAL de los picks recomendados que ya se liquidaron.

    A diferencia del backtest (que simula el pasado), esto mide lo que de
    verdad pasó con los picks que el sistema emitió. Es la única métrica que
    responde "¿esto gana dinero?". La llena jobs/settle_predictions.py.
    """
    settled = db.execute(
        select(
            func.count(Prediction.id),
            func.count(Prediction.id).filter(Prediction.is_win.is_(True)),
            func.coalesce(func.sum(Prediction.profit_loss), 0),
        ).where(Prediction.is_pick_of_day.is_(True), Prediction.settled_at.isnot(None))
    ).one()

    total, wins, pnl = settled[0], settled[1], float(settled[2])

    pending = db.execute(
        select(func.count(Prediction.id)).where(
            Prediction.is_pick_of_day.is_(True), Prediction.settled_at.is_(None)
        )
    ).scalar_one()

    clv_rows = db.execute(
        select(Prediction.bookmaker_odds, Prediction.closing_odds).where(
            Prediction.is_pick_of_day.is_(True), Prediction.closing_odds.isnot(None)
        )
    ).all()
    clvs = [closing_line_value(float(taken), float(closing)) for taken, closing in clv_rows]

    return PerformanceResponse(
        clv_picks=len(clvs),
        avg_clv_pct=(sum(clvs) / len(clvs) * 100) if clvs else None,
        positive_clv_rate=(sum(c > 0 for c in clvs) / len(clvs)) if clvs else None,
        settled_picks=total,
        wins=wins,
        losses=total - wins,
        hit_rate=(wins / total) if total else None,
        profit_loss_units=round(pnl, 3),
        # Yield a stake plano de 1 unidad: P&L total / unidades arriesgadas.
        yield_pct=(pnl / total * 100) if total else None,
        pending_picks=pending,
    )


@router.get("/daily-candidates", response_model=list[DailyCandidateResponse])
def get_daily_candidates(
    min_confidence: ConfidenceLevel | None = Query(
        default=None, description="Filtra dejando solo candidatos con esta confianza o mayor (BAJA < MEDIA < ALTA)."
    ),
    day: date | None = _DAY_QUERY,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Lista TODOS los candidatos del día que pasaron el filtro de cuota/EV
    (incluye los del top-picks), ordenados por EV descendente — útil para
    el frontend mostrar el resto de opciones evaluadas."""
    today_start, today_end = _day_bounds(day)

    stmt = (
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Prediction.bookmaker_odds >= settings.odds_min,
            Prediction.bookmaker_odds <= settings.odds_max,
            Prediction.expected_value >= settings.min_pick_ev,
            Match.kickoff_utc >= today_start,
            Match.kickoff_utc < today_end,
        )
        .order_by(Prediction.expected_value.desc())
    )
    picks = db.execute(stmt).scalars().all()
    responses = [_to_response(p, cls=DailyCandidateResponse) for p in picks]

    if min_confidence is not None:
        min_rank = _CONFIDENCE_RANK[min_confidence]
        responses = [r for r in responses if _CONFIDENCE_RANK[r.confidence_label] >= min_rank]

    return responses
