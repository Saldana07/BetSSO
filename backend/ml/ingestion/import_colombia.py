"""Historial del fútbol colombiano (Primera A y Primera B) desde API-Football,
solo con el plan gratuito y dentro del presupuesto de cuota.

El plan gratuito deja bajar temporadas completas solo de 2022 a 2024. Para
2025 y 2026 se usa el historial de enfrentamientos (`/fixtures/headtohead`),
que sí trae todas las temporadas: una petición por pareja de equipos de la
Primera A actual (~190) cubre todos los partidos entre ellos. Cada partido
guarda resultado y árbitro; las estadísticas (córners, tarjetas...) las trae
después jobs/fetch_match_data.py, una petición por partido, de lo más
reciente hacia atrás.

Todo avanza por etapas y se reanuda: el progreso queda en STATE_PATH. Cuando
termina, cada ejecución solo añade las parejas de equipos nuevos (ascensos).

Uso:
    python -m ml.ingestion.import_colombia
"""
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import distinct, select

from app.database import SessionLocal
from app.models import League, Match, Team
from jobs.fetch_match_data import daily_flow_done, request_budget
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_match

COLOMBIA_LEAGUES = (239, 240)          # Primera A (Liga BetPlay), Primera B (Torneo BetPlay)
FREE_PLAN_SEASONS = (2022, 2023, 2024)
STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "raw" / "colombia" / "import_state.json"


def _state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"seasons": [], "pairs": []}


def _save(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _current_primera_a_teams(db) -> list[int]:
    """api_team_id de los equipos de la Primera A del último año con partidos."""
    league = db.execute(select(League).where(League.api_league_id == 239)).scalar_one_or_none()
    if league is None:
        return []
    last_year = datetime.now(timezone.utc).year
    ids = set()
    for col in (Match.home_team_id, Match.away_team_id):
        ids |= set(db.execute(
            select(distinct(Team.api_team_id)).join(Match, col == Team.id)
            .where(Match.league_id == league.id, Match.season == str(last_year), Team.api_team_id.isnot(None))
        ).scalars().all())
    return sorted(ids)


def _upsert_colombian(db, fixtures: list[dict]) -> int:
    n = 0
    for f in fixtures:
        if f["league"]["id"] in COLOMBIA_LEAGUES:
            upsert_match(db, f)
            n += 1
    db.commit()
    return n


def main() -> None:
    db = SessionLocal()
    state = _state()
    try:
        seasons_todo = [(lg, s) for lg in COLOMBIA_LEAGUES for s in FREE_PLAN_SEASONS
                        if f"{lg}-{s}" not in state["seasons"]]
        teams = _current_primera_a_teams(db)
        pairs_todo = [f"{a}-{b}" for a, b in itertools.combinations(teams, 2) if f"{a}-{b}" not in state["pairs"]]
        if not seasons_todo and not pairs_todo:
            print("Colombia: historial completo (temporadas 2022-2024 y enfrentamientos 2025-2026).")
            return

        with APIFootballClient() as client:
            used, limit = client.quota()
            budget = request_budget(used, limit, daily_flow_done(datetime.now(timezone.utc).date()))
            done_seasons = done_pairs = matches = 0
            for lg, season in seasons_todo:
                if budget <= 0:
                    break
                try:
                    matches += _upsert_colombian(db, client._get("/fixtures", {"league": lg, "season": season}))
                except APIFootballError as exc:
                    print(f"  [!] liga {lg} {season}: {exc}")
                    continue
                finally:
                    budget -= 1
                state["seasons"].append(f"{lg}-{season}")
                done_seasons += 1
                _save(state)
            for pair in pairs_todo:
                if budget <= 0:
                    break
                try:
                    matches += _upsert_colombian(db, client._get("/fixtures/headtohead", {"h2h": pair}))
                except APIFootballError as exc:
                    print(f"  [!] enfrentamientos {pair}: {exc}")
                    continue
                finally:
                    budget -= 1
                state["pairs"].append(pair)
                done_pairs += 1
                _save(state)
        left = (len(seasons_todo) - done_seasons) + (len(pairs_todo) - done_pairs)
        print(f"Colombia: {done_seasons} temporadas y {done_pairs} parejas de equipos importadas "
              f"({matches} partidos); faltan {left} peticiones para completar el historial.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
