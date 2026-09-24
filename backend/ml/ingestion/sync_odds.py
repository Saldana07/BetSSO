"""Sincroniza cuotas para los partidos de una fecha ya presentes en la DB
(ejecutar sync_fixtures.py primero). Cada corrida inserta un nuevo snapshot
de cuotas (no sobreescribe) para poder reconstruir la evolución apertura ->
cierre requerida por la metodología de value betting.

Uso:
    python -m ml.ingestion.sync_odds --date 2026-09-14 --snapshot-type OPENING
    python -m ml.ingestion.sync_odds --date 2026-09-14 --snapshot-type LIVE --fresh-hours 3
"""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Match, Odds
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_odds_for_match

settings = get_settings()


def team_history_counts(db: Session, team_ids: set[int]) -> dict[int, int]:
    """Partidos jugados (con resultado) de cada equipo, en una sola consulta."""
    if not team_ids:
        return {}
    played = (Match.status == "FINISHED") & Match.home_goals.isnot(None)
    sides = union_all(
        select(Match.home_team_id.label("team_id")).where(played, Match.home_team_id.in_(team_ids)),
        select(Match.away_team_id.label("team_id")).where(played, Match.away_team_id.in_(team_ids)),
    ).subquery()
    counts = dict(db.execute(select(sides.c.team_id, func.count()).group_by(sides.c.team_id)).all())
    return {t: counts.get(t, 0) for t in team_ids}


def last_odds_at(db: Session, match_id: int, snapshot_type: str = "LIVE") -> datetime | None:
    return db.execute(select(func.max(Odds.recorded_at)).where(
        Odds.match_id == match_id, Odds.snapshot_type == snapshot_type)).scalar_one_or_none()


def sync_odds(date: str, snapshot_type: str, only_with_history: bool = True, limit: int | None = None,
              fresh_hours: float | None = None) -> int:
    """`fresh_hours`: no vuelve a pedir las cuotas de un partido si las que hay
    tienen menos de esas horas (actualizar dos veces no gasta el doble)."""
    day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    total_inserted = 0
    try:
        # Solo partidos aún no jugados necesitan cuotas — pedirlas para
        # partidos FINISHED/LIVE desperdicia cuota diaria de API-Football
        # (100 req/día en el plan Free) sin ningún uso para el pick del día.
        matches = db.execute(
            select(Match).where(
                Match.kickoff_utc >= max(day_start, now),
                Match.kickoff_utc < day_end,
                Match.status == "SCHEDULED",
            )
        ).scalars().all()

        if not matches:
            print(f"No hay partidos SCHEDULED en DB para {date}. Ejecuta sync_fixtures.py primero.")
            return 0

        if only_with_history:
            # Solo pedimos cuotas de partidos que podrían generar un pick.
            # generate_daily_pick descarta los equipos sin historial, así que
            # pedir sus cuotas quema cuota diaria (100 req/día en el plan Free)
            # para nada: en una jornada normal hay 200+ partidos programados.
            history = team_history_counts(db, {m.home_team_id for m in matches} | {m.away_team_id for m in matches})
            eligible = [m for m in matches
                        if min(history[m.home_team_id], history[m.away_team_id]) >= settings.min_team_history_matches]
            print(
                f"{len(matches)} partidos programados, {len(eligible)} con historial suficiente "
                f"(>={settings.min_team_history_matches} partidos por equipo). Se piden cuotas solo de esos."
            )
            matches = eligible

        if fresh_hours is not None:
            fresh = [m for m in matches
                     if (last := last_odds_at(db, m.id, snapshot_type)) is not None and now - last < timedelta(hours=fresh_hours)]
            if fresh:
                print(f"{len(fresh)} ya tienen cuotas de las últimas {fresh_hours:g} h: no se vuelven a pedir.")
            matches = [m for m in matches if m not in fresh]

        if limit is not None:
            matches = matches[:limit]

        if not matches:
            print("Ningún partido necesita cuotas nuevas: no se gasta cuota de API.")
            return 0

        recorded_at = datetime.now(timezone.utc)
        with APIFootballClient() as client:
            for match in matches:
                try:
                    odds_results = client.get_odds(fixture_id=match.api_match_id)
                except APIFootballError as exc:
                    print(f"  [!] cuotas del partido {match.id}: {exc}")
                    if "limit" in str(exc).lower():
                        print("Cuota de la API agotada: se guarda lo traído hasta aquí.")
                        break
                    continue
                for odds_payload in odds_results:
                    inserted = upsert_odds_for_match(db, match.id, odds_payload, snapshot_type, recorded_at)
                    total_inserted += inserted
                db.commit()   # lo traído queda guardado aunque la corrida se corte
    finally:
        db.close()
    return total_inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--snapshot-type", required=True, choices=["OPENING", "LIVE", "CLOSING"],
    )
    parser.add_argument(
        "--all-matches", action="store_true",
        help="Pide cuotas de TODOS los partidos, no solo los que tienen historial suficiente (gasta mucha más cuota)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Tope de partidos a consultar (protege la cuota diaria)")
    parser.add_argument("--fresh-hours", type=float, default=None,
                        help="No volver a pedir las cuotas que tengan menos de estas horas")
    args = parser.parse_args()

    n = sync_odds(args.date, args.snapshot_type, only_with_history=not args.all_matches, limit=args.limit,
                  fresh_hours=args.fresh_hours)
    print(f"Insertadas {n} filas de cuotas ({args.snapshot_type}) para {args.date}")


if __name__ == "__main__":
    main()
