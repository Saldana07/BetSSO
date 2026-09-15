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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Match, Prediction
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.value_betting.settlement import MissingStatsError, UnknownMarketError, bet_won, profit_loss


def _fill_missing_corners(db: Session, pending: list[Prediction]) -> int:
    """Trae de API-Football los córners de los partidos terminados que tengan
    picks de córners pendientes y aún no los tengan cargados. Cuesta 1
    petición por partido, pero solo para partidos donde de verdad apostamos
    (normalmente 0-3 al día), así que no compromete la cuota diaria."""
    matches_needing_corners = {
        p.match.id: p.match
        for p in pending
        if p.market.startswith("CORNERS_") and p.match.home_corners is None and p.match.api_match_id
    }
    if not matches_needing_corners:
        return 0

    filled = 0
    with APIFootballClient() as client:
        for match in matches_needing_corners.values():
            try:
                corners = client.get_fixture_corners(match.api_match_id)
            except APIFootballError as exc:
                print(f"  [!] No se pudieron traer córners del partido {match.id}: {exc}")
                continue
            if corners is None:
                continue
            match.home_corners, match.away_corners = corners
            filled += 1
    db.flush()
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

    stats["corners_fetched"] = _fill_missing_corners(db, pending)

    for prediction in pending:
        match = prediction.match
        try:
            won = bet_won(
                prediction.market, prediction.selection,
                match.home_goals, match.away_goals,
                home_corners=match.home_corners, away_corners=match.away_corners,
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

    db.commit()
    return stats


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
        print(f"{stats['waiting_stats']} picks de córners esperan a que la API publique las estadísticas del partido.")


if __name__ == "__main__":
    main()
