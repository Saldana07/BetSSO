"""Trae resultado + estadísticas de los partidos ya jugados, con presupuesto
de cuota de API.

`/fixtures?id=` devuelve en UNA petición el resultado y las estadísticas
(córners, tiros, tiros a puerta, tarjetas, faltas). El plan gratuito no
permite pedir varios partidos a la vez (`ids`), así que cada partido cuesta 1
de las 100 peticiones diarias. Por eso hay prioridades y presupuesto:

  1. partidos con picks sin liquidar (lo que el usuario ve en "Ayer/Hoy");
  2. partidos con mercados evaluados sin liquidar (para medir el modelo
     contra la casa: ver MarketEvaluation);
  3. partidos recientes de las ligas cubiertas por el histórico (completan
     el CSV, que se actualiza una vez al mes: ver count_stats_overlay.py).

Presupuesto: la cuota se reinicia a las 00:00 UTC y el flujo diario la
necesita a las 13:00 UTC (08:00 en Colombia). Mientras el flujo de hoy no
haya corrido, se reservan RESERVE_BEFORE_DAILY peticiones para él; después,
solo RESERVE_AFTER_DAILY. Lo que no quepa hoy se trae en la próxima corrida
(la tarea "liquidar" pasa cada 2 horas).

Uso:
    python -m jobs.fetch_match_data [--max 10]
"""
import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import League, MarketEvaluation, Match, Prediction
from ml.features.count_stats import (
    COVERED_COUNTRIES, DIVISION_COUNTRY, SPI_SOURCE_TAG, competition_country, default_resolver,
)
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_match

DAILY_MARKER = Path(__file__).resolve().parents[1] / ".daily_done"
RESERVE_BEFORE_DAILY = 70   # el flujo diario usa ~40-75 en fin de semana
RESERVE_AFTER_DAILY = 5     # margen para pruebas manuales
FINISHED_AFTER = timedelta(hours=2, minutes=30)   # desde el inicio hasta que suele haber estadísticas
RETRY_AFTER = timedelta(hours=6)                  # si la API aún no las tenía
RETRY_WINDOW = timedelta(days=4)                  # después, se asume que nunca las tendrá
FRESHNESS_WINDOW = timedelta(days=21)             # prioridad 3: solo partidos recientes


def daily_flow_done(today: date) -> bool:
    try:
        return DAILY_MARKER.read_text(encoding="utf-8", errors="ignore").strip() == today.isoformat()
    except OSError:
        return False


def request_budget(used: int, limit: int, daily_done: bool) -> int:
    reserve = RESERVE_AFTER_DAILY if daily_done else RESERVE_BEFORE_DAILY
    return max(0, limit - used - reserve)


def _pending_clause(now: datetime):
    """Partido que ya debió terminar y al que le falta resultado o estadísticas,
    sin haberlo pedido recién (o pedido hace rato y aún en ventana de reintento)."""
    return and_(
        Match.api_match_id.isnot(None),
        Match.kickoff_utc < now - FINISHED_AFTER,
        Match.status != "POSTPONED",
        or_(Match.status != "FINISHED", and_(Match.home_corners.is_(None), Match.home_shots.is_(None))),
        or_(
            Match.stats_fetched_at.is_(None),
            and_(Match.stats_fetched_at < now - RETRY_AFTER, Match.kickoff_utc > now - RETRY_WINDOW),
        ),
    )


def candidate_matches(db: Session, now: datetime) -> list[tuple[int, Match]]:
    """[(prioridad, partido)] en orden de prioridad, sin repetidos."""
    pending = _pending_clause(now)
    tier1 = db.execute(
        select(Match).where(pending, exists().where(Prediction.match_id == Match.id, Prediction.settled_at.is_(None)))
        .order_by(Match.kickoff_utc)
    ).scalars().all()
    tier2 = db.execute(
        select(Match).where(pending, exists().where(MarketEvaluation.match_id == Match.id, MarketEvaluation.settled_at.is_(None)))
        .order_by(Match.kickoff_utc)
    ).scalars().all()

    # Prioridad 3: partidos de liga de las divisiones del histórico (ambos
    # equipos emparejados, misma división actual, del país de la liga).
    resolver = default_resolver()
    tier3 = []
    for m in db.execute(
        select(Match).join(League, Match.league_id == League.id)
        .where(pending, League.country.in_(COVERED_COUNTRIES | {SPI_SOURCE_TAG}), Match.kickoff_utc > now - FRESHNESS_WINDOW)
        .order_by(Match.kickoff_utc.desc())
    ).scalars().all():
        country = competition_country(m.league.name, m.league.country)
        hk = resolver.resolve(m.home_team.name, country)
        ak = resolver.resolve(m.away_team.name, country)
        if hk is None or ak is None:
            continue
        div = resolver.last_division[hk]
        if resolver.last_division[ak] == div and DIVISION_COUNTRY.get(div) == country:
            tier3.append(m)

    seen: set[int] = set()
    out: list[tuple[int, Match]] = []
    for tier, matches in ((1, tier1), (2, tier2), (3, tier3)):
        for m in matches:
            if m.id not in seen:
                seen.add(m.id)
                out.append((tier, m))
    return out


def apply_fixture_payload(db: Session, match: Match, payload: dict, now: datetime) -> bool:
    """Guarda resultado y estadísticas. True si trajo estadísticas de ambos equipos."""
    upsert_match(db, payload)
    match.stats_fetched_at = now
    stats = APIFootballClient.parse_fixture_statistics(payload.get("statistics") or [])
    if not stats:
        return False
    home = stats.get(match.home_team.api_team_id)
    away = stats.get(match.away_team.api_team_id)
    if home is None or away is None:
        return False
    match.home_corners, match.away_corners = home["corners"], away["corners"]
    match.home_shots, match.away_shots = home["shots"], away["shots"]
    match.home_shots_on_target, match.away_shots_on_target = home["shots_on_target"], away["shots_on_target"]
    match.home_yellow, match.away_yellow = home["yellow"], away["yellow"]
    match.home_red, match.away_red = home["red"], away["red"]
    match.home_fouls, match.away_fouls = home["fouls"], away["fouls"]
    return True


def fetch_match(client: APIFootballClient, db: Session, match: Match, now: datetime | None = None) -> bool:
    """Una petición: resultado + estadísticas de un partido."""
    now = now or datetime.now(timezone.utc)
    try:
        payload = client.get_fixture_by_id(match.api_match_id)
    except APIFootballError as exc:
        print(f"  [!] {match.id}: {exc}")
        return False
    if payload is None:
        match.stats_fetched_at = now
        return False
    return apply_fixture_payload(db, match, payload, now)


def fetch_finished_match_data(db: Session, max_requests: int | None = None) -> dict:
    now = datetime.now(timezone.utc)
    candidates = candidate_matches(db, now)
    summary = {"candidates": len(candidates), "fetched": 0, "with_stats": 0, "by_tier": {1: 0, 2: 0, 3: 0}, "budget": 0}
    if not candidates:
        return summary

    with APIFootballClient() as client:
        used, limit = client.quota()
        budget = request_budget(used, limit, daily_flow_done(now.date()))
        if max_requests is not None:
            budget = min(budget, max_requests)
        summary["budget"] = budget
        for tier, match in candidates[:budget]:
            got_stats = fetch_match(client, db, match, now)
            db.commit()  # progreso parcial a salvo si algo corta la corrida
            summary["fetched"] += 1
            summary["with_stats"] += int(got_stats)
            summary["by_tier"][tier] += 1
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=None, help="Tope de peticiones en esta corrida")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        s = fetch_finished_match_data(db, args.max)
    finally:
        db.close()
    if not s["candidates"]:
        print("No hay partidos jugados pendientes de resultado o estadísticas.")
        return
    t = s["by_tier"]
    print(f"Partidos pendientes: {s['candidates']} | presupuesto de API: {s['budget']} | "
          f"traídos: {s['fetched']} (picks {t[1]}, mercados evaluados {t[2]}, histórico {t[3]}), "
          f"con estadísticas: {s['with_stats']}")
    if s["fetched"] < s["candidates"]:
        print(f"Quedan {s['candidates'] - s['fetched']} para las próximas corridas (cuota de API).")


if __name__ == "__main__":
    main()
