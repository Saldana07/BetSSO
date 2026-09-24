"""Guardado de equipos, partidos y cuotas de la NBA."""
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.models import NBA_TABLES, NbaGame, NbaOdds, NbaTeam

BOX_FIELDS = ("pts", "fga", "fta", "oreb", "tov")


def ensure_tables() -> None:
    """Crea las tablas de la NBA si no existen (no toca las de fútbol)."""
    Base.metadata.create_all(engine, tables=NBA_TABLES)


def nickname(team_name: str) -> str:
    """"Portland Trail Blazers" -> "blazers": el apodo es único entre los 30
    equipos y no cambia cuando cambia la ciudad o el nombre largo ("LA
    Clippers" / "Los Angeles Clippers")."""
    return team_name.strip().split()[-1].lower()


def upsert_team(db: Session, team: tuple[int, str, str]) -> None:
    team_id, abbr, name = team
    row = db.get(NbaTeam, team_id)
    if row is None:
        db.add(NbaTeam(id=team_id, abbreviation=abbr, name=name))
    else:   # se procesa de lo más antiguo a lo más nuevo: queda el nombre actual
        row.abbreviation, row.name = abbr, name


def upsert_games(db: Session, games: list[dict]) -> int:
    """Inserta o actualiza. Nunca borra un dato con un vacío (el calendario no
    trae estadísticas y el registro de partidos no trae la hora)."""
    changed = 0
    for g in games:
        upsert_team(db, g["home_team"])
        upsert_team(db, g["away_team"])
        db.flush()
        row = db.get(NbaGame, g["id"])
        if row is None:
            row = NbaGame(id=g["id"], season=g["season"], season_type=g["season_type"], game_date=g["game_date"],
                          home_team_id=g["home_team"][0], away_team_id=g["away_team"][0], status=g["status"])
            db.add(row)
        row.season, row.season_type, row.game_date = g["season"], g["season_type"], g["game_date"]
        row.home_team_id, row.away_team_id = g["home_team"][0], g["away_team"][0]
        if row.status != "FINISHED" or g["status"] == "FINISHED":
            row.status = g["status"]
        if g.get("tipoff_utc"):
            row.tipoff_utc = g["tipoff_utc"]
        for side in ("home", "away"):
            for f in BOX_FIELDS:
                v = g.get(f"{side}_{f}")
                if v is not None:
                    setattr(row, f"{side}_{f}", v)
        changed += 1
    return changed


def store_odds(db: Session, game_id: str, source: str, rows: list[dict], recorded_at: datetime,
               replace_snapshots: tuple[str, ...] = ()) -> int:
    """Guarda filas de cuota. `replace_snapshots`: borra antes las de esos tipos
    y esa fuente (el historial de ESPN se reescribe entero si se vuelve a pedir;
    las LIVE se acumulan para ver cómo se mueve la línea)."""
    if replace_snapshots:
        db.execute(delete(NbaOdds).where(NbaOdds.game_id == game_id, NbaOdds.source == source,
                                         NbaOdds.snapshot.in_(replace_snapshots)))
    for r in rows:
        db.add(NbaOdds(game_id=game_id, source=source, bookmaker=r["bookmaker"], market=r["market"],
                       selection=r["selection"], odds_value=r["odds"], snapshot=r["snapshot"],
                       recorded_at=recorded_at))
    return len(rows)


def games_by_nicknames(db: Session, day) -> dict[tuple[str, str], NbaGame]:
    rows = db.execute(select(NbaGame).where(NbaGame.game_date == day)).scalars().all()
    return {(nickname(g.home_team.name), nickname(g.away_team.name)): g for g in rows}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
