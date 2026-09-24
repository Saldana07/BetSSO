"""Refresca las cuotas de los partidos que empiezan pronto y rehace sus picks.

Lo lanza el botón "Refrescar cuotas" de la página (nada corre solo). Las
cuotas de la actualización del día pueden tener 10+ horas cuando llega un
partido de la noche (muchos colombianos empiezan entre 19:30 y 21:00). Pide
SOLO las de los partidos con historial suficiente que empiezan en las
próximas horas y cuyas cuotas tienen más de FRESH_ODDS_HOURS: 1 petición por
partido. Luego regenera los picks de esos días.

Uso:
    python -m jobs.refresh_odds [--hours 12] [--scope colombia]
"""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Match
from ml.features.colombia import is_colombian
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_odds_for_match
from ml.ingestion.sync_odds import last_odds_at, team_history_counts

FRESH_ODDS_HOURS = 3.0
DEFAULT_HOURS = 12.0

settings = get_settings()


def upcoming_needing_odds(db: Session, now: datetime, hours: float = DEFAULT_HOURS, scope: str | None = None,
                          fresh_hours: float = FRESH_ODDS_HOURS) -> list[Match]:
    """Partidos que empiezan en las próximas `hours` horas, con historial
    suficiente y sin cuotas recientes: lo que costaría refrescar."""
    matches = db.execute(
        select(Match).where(
            Match.status == "SCHEDULED", Match.api_match_id.isnot(None),
            Match.kickoff_utc > now, Match.kickoff_utc < now + timedelta(hours=hours),
        ).order_by(Match.kickoff_utc)
    ).scalars().all()
    if scope == "colombia":
        matches = [m for m in matches if is_colombian(m)]
    elif scope == "europa":
        matches = [m for m in matches if not is_colombian(m)]
    history = team_history_counts(db, {m.home_team_id for m in matches} | {m.away_team_id for m in matches})
    out = []
    for m in matches:
        if min(history[m.home_team_id], history[m.away_team_id]) < settings.min_team_history_matches:
            continue
        last = last_odds_at(db, m.id)
        if last is not None and now - last < timedelta(hours=fresh_hours):
            continue
        out.append(m)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS, help="Partidos que empiezan dentro de estas horas")
    parser.add_argument("--scope", choices=["europa", "colombia"], default=None, help="Solo un apartado; por defecto ambos")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        todo = upcoming_needing_odds(db, now, args.hours, args.scope)
        if not todo:
            print(f"Ningún partido de las próximas {args.hours:g} h necesita cuotas nuevas: no se gasta cuota.")
            return
        fetched = 0
        with APIFootballClient() as client:
            used, limit = client.quota()
            budget = max(0, limit - used)   # lo pidió el usuario: puede usar lo que quede
            for m in todo[:budget]:
                try:
                    for payload in client.get_odds(fixture_id=m.api_match_id):
                        upsert_odds_for_match(db, m.id, payload, "LIVE", now)
                    fetched += 1
                except APIFootballError as exc:
                    print(f"  [!] cuotas {m.home_team.name}-{m.away_team.name}: {exc}")
        db.commit()
        print(f"Cuotas nuevas de {fetched} de {len(todo)} partidos de las próximas {args.hours:g} h.")
        if fetched < len(todo):
            print("No alcanzó la cuota de la API para el resto (se renueva a las 19:00 de Colombia).")
        days = sorted({m.kickoff_utc.astimezone(timezone.utc).date() for m in todo})
    finally:
        db.close()

    if fetched:
        from jobs.generate_daily_pick import generate
        generate(days, args.scope)


if __name__ == "__main__":
    main()
