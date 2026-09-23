"""Liquida los picks cuyos partidos ya terminaron: marca si ganaron y cuánto
dejaron a stake plano de 1 unidad.

Es la contraparte imprescindible de generate_daily_pick.py: sin esto el
sistema emite recomendaciones para siempre sin que nadie mida jamás si
acierta. El backtest dice cómo habría funcionado en el pasado simulado; esto
dice cómo está funcionando de verdad.

Conviene correrlo a diario (después de que terminen los partidos del día),
por ejemplo justo antes de generar los picks del día siguiente.

Uso:
    python -m jobs.settle_predictions
"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import MarketEvaluation, Match, Odds, Prediction
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_odds_for_match
from ml.value_betting.settlement import MissingStatsError, UnknownMarketError, bet_won, profit_loss


def _capture_closing_odds(db: Session, pending: list[Prediction]) -> int:
    """Guarda en cada pick pendiente la mejor cuota de CIERRE de su selección.

    API-Football conserva las cuotas pre-partido tras el final (su última
    actualización, ~2 h antes del inicio, es el cierre a efectos prácticos).
    Se persiste el snapshot completo como `CLOSING` en `odds` — el esquema lo
    contemplaba desde el principio — y de ahí se toma el mejor precio para el
    mercado/selección del pick, igual que se tomó el mejor precio al apostar.
    Cuesta 1 petición por partido apostado."""
    matches = {p.match.id: p.match for p in pending if p.closing_odds is None and p.match.api_match_id}
    if not matches:
        return 0

    recorded_at = datetime.now(timezone.utc)
    with APIFootballClient() as client:
        for match in matches.values():
            already = db.execute(
                select(func.count(Odds.id)).where(Odds.match_id == match.id, Odds.snapshot_type == "CLOSING")
            ).scalar_one()
            if already:
                continue
            try:
                for payload in client.get_odds(fixture_id=match.api_match_id):
                    upsert_odds_for_match(db, match.id, payload, "CLOSING", recorded_at)
            except APIFootballError as exc:
                print(f"  [!] Sin cuotas de cierre para el partido {match.id}: {exc}")
    db.flush()

    filled = 0
    for p in pending:
        if p.closing_odds is not None:
            continue
        base = select(Odds.odds_value, Odds.bookmaker).where(
            Odds.match_id == p.match_id, Odds.snapshot_type == "CLOSING",
            Odds.market == p.market, Odds.selection == p.selection,
        )
        # Misma casa primero: es la comparación limpia ("mi precio en Betano
        # contra el cierre de Betano"). Mejor-contra-mejor como respaldo, pero
        # es más ruidoso: una sola casa rezagada oculta el movimiento del
        # mercado (ayer: Betano se quedó en 1.65 mientras Bet365 bajó a 1.57).
        row = None
        if p.bookmaker:
            row = db.execute(base.where(Odds.bookmaker == p.bookmaker)).first()
        if row is None:
            row = db.execute(base.order_by(Odds.odds_value.desc()).limit(1)).first()
        if row:
            p.closing_odds, p.closing_bookmaker = float(row[0]), row[1]
            filled += 1
    return filled


def settle(db: Session) -> dict:
    pending = db.execute(
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Prediction.settled_at.is_(None),
            Match.status == "FINISHED",
            Match.home_goals.isnot(None),
            Match.away_goals.isnot(None),
        )
    ).scalars().all()

    stats = {"settled": 0, "wins": 0, "losses": 0, "errors": 0, "waiting_stats": 0, "pnl": 0.0}
    now = datetime.now(timezone.utc)

    # El resultado y las estadísticas los trae antes jobs/fetch_match_data.py
    # (con presupuesto de cuota); aquí solo se liquida lo que ya está en la BD.

    # El cierre se captura para TODO pick de partido terminado que aún no lo
    # tenga (también los ya liquidados antes de existir esta métrica).
    needs_closing = db.execute(
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .where(Prediction.closing_odds.is_(None), Match.status == "FINISHED")
    ).scalars().all()
    stats["closing_captured"] = _capture_closing_odds(db, needs_closing)

    for prediction in pending:
        match = prediction.match
        try:
            won = bet_won(
                prediction.market, prediction.selection,
                match.home_goals, match.away_goals,
                stats=match.stat_totals(),
            )
        except MissingStatsError:
            # Falta la estadística (ej. la API aún no publicó los córners):
            # se deja pendiente y se reintentará en la próxima corrida.
            stats["waiting_stats"] += 1
            continue
        except UnknownMarketError as exc:
            # No se liquida a ciegas: se deja pendiente y se avisa, en vez de
            # inventar un resultado que contaminaría las métricas.
            print(f"  [!] {exc}")
            stats["errors"] += 1
            continue

        pnl = profit_loss(won, float(prediction.bookmaker_odds))
        prediction.is_win = won
        prediction.profit_loss = pnl
        prediction.settled_at = now

        stats["settled"] += 1
        stats["wins" if won else "losses"] += 1
        stats["pnl"] += pnl

    stats.update(settle_evaluations(db, now))
    db.commit()
    return stats


def settle_evaluations(db: Session, now: datetime) -> dict:
    """Liquida TODOS los mercados evaluados de partidos terminados (no solo los
    picks). Es lo que permite medir el modelo contra la casa con cientos de
    casos por semana en vez de 3-5 picks al día."""
    pending = db.execute(
        select(MarketEvaluation)
        .join(Match, MarketEvaluation.match_id == Match.id)
        .where(
            MarketEvaluation.settled_at.is_(None),
            Match.status == "FINISHED",
            Match.home_goals.isnot(None),
            Match.away_goals.isnot(None),
        )
    ).scalars().all()
    settled = waiting = 0
    for ev in pending:
        match = ev.match
        try:
            ev.is_win = bet_won(ev.market, ev.selection, match.home_goals, match.away_goals, stats=match.stat_totals())
        except (MissingStatsError, UnknownMarketError):
            waiting += 1
            continue
        ev.settled_at = now
        settled += 1
    return {"evaluations_settled": settled, "evaluations_waiting": waiting}


def main():
    db = SessionLocal()
    try:
        stats = settle(db)
    finally:
        db.close()

    if not stats["settled"]:
        print("No había picks pendientes de liquidar.")
    else:
        hit_rate = stats["wins"] / stats["settled"] * 100
        yield_pct = stats["pnl"] / stats["settled"] * 100
        print(f"Liquidados: {stats['settled']} picks ({stats['wins']} ganados / {stats['losses']} perdidos)")
        print(f"Acierto: {hit_rate:.1f}%   |   P&L: {stats['pnl']:+.2f} unidades   |   Yield: {yield_pct:+.2f}%")

    if stats["errors"]:
        print(f"{stats['errors']} picks no se pudieron liquidar (mercado desconocido) y siguen pendientes.")
    if stats["waiting_stats"]:
        print(f"{stats['waiting_stats']} picks de conteo (córners/tiros/tarjetas/faltas) esperan a que la API publique las estadísticas del partido.")
    if stats.get("evaluations_settled") or stats.get("evaluations_waiting"):
        print(f"Mercados evaluados liquidados: {stats['evaluations_settled']} "
              f"({stats['evaluations_waiting']} esperan estadísticas).")
    if stats.get("closing_captured"):
        print(f"Cuotas de cierre capturadas para {stats['closing_captured']} picks (Closing Line Value).")


if __name__ == "__main__":
    main()
