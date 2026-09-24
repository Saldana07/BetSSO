"""Guardado de equipos, partidos y cuotas de la NBA."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.models import NBA_TABLES, NbaGame, NbaOdds, NbaPlayer, NbaPlayerGame, NbaPropOdds, NbaTeam
from ml.nba.player_sources import name_key

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


def upsert_player_games(db: Session, rows: list[dict]) -> int:
    """Jugadores y su estadística por partido, por lotes (26.000 filas por
    temporada). Solo partidos que existen en nba_games."""
    known = set(db.execute(select(NbaGame.id).where(NbaGame.id.in_({r["game_id"] for r in rows}))).scalars())
    rows = [r for r in rows if r["game_id"] in known]
    players = {r["player_id"]: r["player_name"] for r in rows}
    for chunk in _chunks([{"id": pid, "name": n, "name_key": name_key(n)} for pid, n in players.items()], 2000):
        stmt = insert(NbaPlayer).values(chunk)
        db.execute(stmt.on_conflict_do_update(index_elements=["id"], set_={"name": stmt.excluded.name,
                                                                            "name_key": stmt.excluded.name_key}))
    cols = ("game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast", "fg3m")
    for chunk in _chunks([{c: r[c] for c in cols} for r in rows], 2000):
        stmt = insert(NbaPlayerGame).values(chunk)
        db.execute(stmt.on_conflict_do_update(index_elements=["game_id", "player_id"],
                                              set_={c: getattr(stmt.excluded, c) for c in cols[2:]}))
    return len(rows)


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def store_props(db: Session, game_id: str, source: str, bookmaker: str, rows: list[dict], recorded_at: datetime,
                replace_snapshots: tuple[str, ...] = ()) -> int:
    """Líneas de jugadores (filas con player_name, player_id, stat, line, side, odds, snapshot)."""
    if replace_snapshots:
        db.execute(delete(NbaPropOdds).where(NbaPropOdds.game_id == game_id, NbaPropOdds.source == source,
                                             NbaPropOdds.bookmaker == bookmaker,
                                             NbaPropOdds.snapshot.in_(replace_snapshots)))
    for r in rows:
        db.add(NbaPropOdds(game_id=game_id, source=source, bookmaker=bookmaker, player_name=r["player_name"][:80],
                           player_id=r.get("player_id"), stat=r["stat"], line=r["line"], side=r["side"],
                           odds_value=r["odds"], snapshot=r["snapshot"], recorded_at=recorded_at))
    return len(rows)


def game_players(db: Session, game: NbaGame, recent_days: int = 400) -> dict[int, str]:
    """Jugadores posibles de un partido: los que jugaron en él (si ya se jugó)
    o los que jugaron con alguno de los dos equipos en el último año."""
    rows = db.execute(select(NbaPlayer.id, NbaPlayer.name).join(NbaPlayerGame, NbaPlayerGame.player_id == NbaPlayer.id)
                      .where(NbaPlayerGame.game_id == game.id)).all()
    if rows:
        return dict(rows)
    since = game.game_date - timedelta(days=recent_days)
    rows = db.execute(
        select(NbaPlayer.id, NbaPlayer.name).join(NbaPlayerGame, NbaPlayerGame.player_id == NbaPlayer.id)
        .join(NbaGame, NbaGame.id == NbaPlayerGame.game_id)
        .where(NbaPlayerGame.team_id.in_((game.home_team_id, game.away_team_id)), NbaGame.game_date >= since)
        .distinct()).all()
    return dict(rows)


def games_by_nicknames(db: Session, day) -> dict[tuple[str, str], NbaGame]:
    rows = db.execute(select(NbaGame).where(NbaGame.game_date == day)).scalars().all()
    return {(nickname(g.home_team.name), nickname(g.away_team.name)): g for g in rows}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
