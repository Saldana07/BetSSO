"""Trae el resultado de cada partido que tenga picks sin liquidar.

Por qué por ID y no por fecha: el plan Free de API-Football solo permite
consultar `/fixtures?date=` en una ventana de ±1 día respecto a hoy. Si el
flujo diario no se ejecuta un día, los resultados de esa fecha quedan fuera
de la ventana para siempre y los picks nunca se liquidan ni entran al % de
acierto. `/fixtures?id=` no tiene esa restricción, y además cuesta 1 petición
por partido apostado (2-4 al día) en vez de traer los 200+ partidos del día.

Uso:
    python -m jobs.sync_pending_results
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Match, Prediction
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_match


def main():
    db = SessionLocal()
    try:
        pending = db.execute(
            select(Match)
            .join(Prediction, Prediction.match_id == Match.id)
            .where(
                Prediction.settled_at.is_(None),
                Match.status != "FINISHED",
                Match.kickoff_utc < datetime.now(timezone.utc),  # solo partidos que ya debieron jugarse
                Match.api_match_id.isnot(None),
            )
            .distinct()
        ).scalars().all()

        if not pending:
            print("No hay partidos con picks pendientes de resultado.")
            return

        updated = 0
        with APIFootballClient() as client:
            for match in pending:
                try:
                    payload = client.get_fixture_by_id(match.api_match_id)
                except APIFootballError as exc:
                    print(f"  [!] {match.id}: {exc}")
                    continue
                if payload is None:
                    continue
                upsert_match(db, payload)
                updated += 1
                print(f"  {match.kickoff_utc.date()} {match.home_team.name} vs {match.away_team.name}: "
                      f"{payload['goals']['home']}-{payload['goals']['away']} ({payload['fixture']['status']['short']})")
        db.commit()
        print(f"{updated} partidos actualizados por ID.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
