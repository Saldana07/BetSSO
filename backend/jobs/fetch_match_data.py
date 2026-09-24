"""Trae resultado + estadísticas de los partidos ya jugados, con presupuesto
de cuota de API.

`/fixtures?id=` devuelve en UNA petición el resultado y las estadísticas
(córners, tiros, tiros a puerta, tarjetas, faltas). El plan gratuito no
permite pedir varios partidos a la vez (`ids`), así que cada partido cuesta 1
de las 100 peticiones diarias. Por eso hay prioridades y presupuesto:

  1. partidos con picks sin liquidar (lo que el usuario ve en "Ayer/Hoy");
  2. partidos con mercados evaluados sin liquidar (para medir el modelo
     contra la casa: ver MarketEvaluation);
  3. historial colombiano (Primera A y B), de lo más reciente hacia atrás:
     el modelo propio de Colombia (ml/features/colombia.py) solo aprende de
     partidos con estadísticas;
  4. partidos recientes de las ligas cubiertas por el histórico (completan
     el CSV entre copias del archivo: ver count_stats_overlay.py).

Nada corre solo: lo lanzan los botones de la página (jobs/on_demand.py).
"Verificar mis picks" pide solo la prioridad 1 y puede usar toda la cuota
que quede; "Completar datos" pide las prioridades 2-4 con reserva: mientras
la actualización del día (partidos y cuotas) no se haya hecho, se guardan
RESERVE_BEFORE_DAILY peticiones para ella; después, RESERVE_AFTER_DAILY.
La cuota se reinicia a las 00:00 UTC (19:00 en Colombia).

Uso:
    python -m jobs.fetch_match_data [--max 10] [--tiers 1] [--reserve 0]
"""
import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import League, MarketEvaluation, Match, Prediction
from ml.features.count_stats import (
    COVERED_COUNTRIES, DIVISION_COUNTRY, SPI_SOURCE_TAG, _load_raw, competition_country, default_resolver,
)
from ml.features.colombia import COLOMBIA_DIVISIONS
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_match

DAILY_MARKER = Path(__file__).resolve().parents[1] / ".daily_done"
RESERVE_BEFORE_DAILY = 70   # la actualización del día usa ~40-75 en fin de semana
RESERVE_AFTER_DAILY = 10    # margen para verificar picks y refrescar cuotas
ALL_TIERS = frozenset({1, 2, 3, 4})
FINISHED_AFTER = timedelta(hours=2, minutes=30)   # desde el inicio hasta que suele haber estadísticas
RETRY_AFTER = timedelta(hours=6)                  # si la API aún no las tenía
RETRY_WINDOW = timedelta(days=4)                  # después, se asume que nunca las tendrá
FRESHNESS_WINDOW = timedelta(days=21)             # prioridad 4: solo partidos recientes
# Colombia: el modelo usa los últimos ~20 partidos de cada equipo; pedir las
# estadísticas de 2022-2024 (~1,000 partidos) costaría semanas de cuota para
# casi nada. Solo desde esta fecha.
COLOMBIA_STATS_SINCE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def daily_flow_done(today: date) -> bool:
    try:
        return DAILY_MARKER.read_text(encoding="utf-8", errors="ignore").strip() == today.isoformat()
    except OSError:
        return False


def request_budget(used: int, limit: int, daily_done: bool, reserve: int | None = None) -> int:
    """Peticiones que se pueden gastar. `reserve` fija la reserva (0 = toda la
    cuota que quede, para lo que el usuario pide explícitamente)."""
    if reserve is None:
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


def candidate_matches(db: Session, now: datetime, tiers=ALL_TIERS) -> list[tuple[int, Match]]:
    """[(prioridad, partido)] en orden de prioridad, sin repetidos (solo las
    prioridades pedidas: la 4 carga el histórico y no se calcula si no hace falta)."""
    pending = _pending_clause(now)
    by_tier: dict[int, list[Match]] = {}
    if 1 in tiers:
        by_tier[1] = db.execute(
            select(Match).where(pending, exists().where(Prediction.match_id == Match.id, Prediction.settled_at.is_(None)))
            .order_by(Match.kickoff_utc)
        ).scalars().all()
    if 2 in tiers:
        by_tier[2] = db.execute(
            select(Match).where(pending, exists().where(MarketEvaluation.match_id == Match.id, MarketEvaluation.settled_at.is_(None)))
            .order_by(Match.kickoff_utc)
        ).scalars().all()
    if 3 in tiers:
        # Historial colombiano, lo más reciente primero.
        by_tier[3] = db.execute(
            select(Match).join(League, Match.league_id == League.id)
            .where(pending, League.api_league_id.in_(COLOMBIA_DIVISIONS), Match.kickoff_utc >= COLOMBIA_STATS_SINCE)
            .order_by(Match.kickoff_utc.desc())
        ).scalars().all()
    if 4 in tiers:
        by_tier[4] = _recent_league_matches(db, pending, now)

    seen: set[int] = set()
    out: list[tuple[int, Match]] = []
    for tier in sorted(by_tier):
        for m in by_tier[tier]:
            if m.id not in seen:
                seen.add(m.id)
                out.append((tier, m))
    return out


def _recent_league_matches(db: Session, pending, now: datetime) -> list[Match]:
    """Prioridad 4: partidos de liga de las divisiones del histórico (ambos
    equipos emparejados, misma división actual, del país de la liga)."""
    resolver = default_resolver()
    # Lo que el histórico (espejo + copias de football-data) ya tiene no hace
    # falta pedirlo a la API: solo partidos posteriores a su último día en
    # cada división (antes eran ~700 partidos en cola, casi todos ya cubiertos).
    raw = _load_raw()
    covered_until = raw.groupby("Division")["MatchDate"].max()
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
        if resolver.last_division[ak] != div or DIVISION_COUNTRY.get(div) != country:
            continue
        last = covered_until.get(div)
        match_day = m.kickoff_utc.astimezone(timezone.utc).replace(tzinfo=None)
        if last is not None and match_day.date() <= last.date():
            continue
        tier3.append(m)
    return tier3


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


def fetch_finished_match_data(db: Session, max_requests: int | None = None, tiers=ALL_TIERS,
                              reserve: int | None = None) -> dict:
    now = datetime.now(timezone.utc)
    candidates = candidate_matches(db, now, tiers)
    summary = {"candidates": len(candidates), "fetched": 0, "with_stats": 0, "by_tier": {1: 0, 2: 0, 3: 0, 4: 0}, "budget": 0}
    if not candidates:
        return summary

    with APIFootballClient() as client:
        used, limit = client.quota()
        budget = request_budget(used, limit, daily_flow_done(now.date()), reserve)
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
    parser.add_argument("--tiers", default="1,2,3,4", help="Prioridades a traer, ej. 1 (solo partidos con picks)")
    parser.add_argument("--reserve", type=int, default=None,
                        help="Peticiones a dejar sin gastar (por defecto, según la actualización del día)")
    args = parser.parse_args()
    tiers = frozenset(int(t) for t in args.tiers.split(",") if t.strip())
    db = SessionLocal()
    try:
        s = fetch_finished_match_data(db, args.max, tiers, args.reserve)
    finally:
        db.close()
    if not s["candidates"]:
        print("No hay partidos jugados pendientes de resultado o estadísticas.")
        return
    t = s["by_tier"]
    print(f"Partidos pendientes: {s['candidates']} | presupuesto de API: {s['budget']} | "
          f"traídos: {s['fetched']} (picks {t[1]}, mercados evaluados {t[2]}, Colombia {t[3]}, histórico {t[4]}), "
          f"con estadísticas: {s['with_stats']}")
    if s["fetched"] < s["candidates"]:
        print(f"Quedan {s['candidates'] - s['fetched']} para otro momento (cuota de API).")


if __name__ == "__main__":
    main()
