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
from ml.value_betting.settlement import UnknownMarketError, bet_won, profit_loss


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

    stats = {"settled": 0, "wins": 0, "losses": 0, "errors": 0, "pnl": 0.0}
    now = datetime.now(timezone.utc)

    for prediction in pending:
        match = prediction.match
        try:
            won = bet_won(prediction.market, prediction.selection, match.home_goals, match.away_goals)
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


if __name__ == "__main__":
    main()
