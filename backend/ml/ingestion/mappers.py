"""Convierte payloads crudos de API-Football en filas de nuestro esquema
(ver app/models.py), vía upsert idempotente por `api_*_id`.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Injury, League, Match, Odds, Team
from ml.features.poisson_xg import ASIAN_HANDICAP_LINES, TEAM_TOTAL_LINES, TOTAL_LINES


def upsert_league(db: Session, raw: dict) -> League:
    """raw = payload['league'] de /fixtures o /leagues (forma con 'id','name','country')."""
    api_league_id = raw["id"]
    league = db.execute(select(League).where(League.api_league_id == api_league_id)).scalar_one_or_none()
    if league is None:
        league = League(api_league_id=api_league_id, name=raw["name"], country=raw.get("country", ""))
        db.add(league)
        db.flush()
    return league


def upsert_team(db: Session, raw: dict, league_id: int | None = None) -> Team:
    """raw = payload de equipo con 'id','name' (visto en fixtures.teams.home/away o /teams)."""
    api_team_id = raw["id"]
    team = db.execute(select(Team).where(Team.api_team_id == api_team_id)).scalar_one_or_none()
    if team is None:
        team = Team(api_team_id=api_team_id, name=raw["name"], league_id=league_id)
        db.add(team)
        db.flush()
    elif league_id is not None and team.league_id is None:
        team.league_id = league_id
    return team


def upsert_match(db: Session, fixture_payload: dict) -> Match:
    """fixture_payload es un elemento de /fixtures.response: {fixture, league, teams, goals, score}."""
    fixture = fixture_payload["fixture"]
    league_raw = fixture_payload["league"]
    teams_raw = fixture_payload["teams"]
    goals = fixture_payload.get("goals", {})

    league = upsert_league(db, league_raw)
    home_team = upsert_team(db, teams_raw["home"], league.id)
    away_team = upsert_team(db, teams_raw["away"], league.id)

    api_match_id = fixture["id"]
    match = db.execute(select(Match).where(Match.api_match_id == api_match_id)).scalar_one_or_none()

    kickoff_utc = datetime.fromisoformat(fixture["date"].replace("Z", "+00:00"))
    status_short = fixture.get("status", {}).get("short", "NS")
    status = _map_status(status_short)

    if match is None:
        match = Match(
            api_match_id=api_match_id,
            league_id=league.id,
            season=str(league_raw.get("season", "")),
            round=league_raw.get("round"),
            kickoff_utc=kickoff_utc,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            status=status,
            referee=fixture.get("referee"),
            venue=(fixture.get("venue") or {}).get("name"),
        )
        db.add(match)
    else:
        match.status = status
        match.kickoff_utc = kickoff_utc

    match.home_goals = goals.get("home")
    match.away_goals = goals.get("away")

    db.flush()
    return match


def _map_status(short_status: str) -> str:
    finished = {"FT", "AET", "PEN"}
    live = {"1H", "2H", "HT", "ET", "P", "LIVE"}
    postponed = {"PST", "CANC", "ABD", "SUSP"}
    if short_status in finished:
        return "FINISHED"
    if short_status in live:
        return "LIVE"
    if short_status in postponed:
        return "POSTPONED"
    return "SCHEDULED"


_MARKET_NAME_MAP = {
    "Match Winner": "1X2",
    "Double Chance": "DOUBLE_CHANCE",
    "Both Teams Score": "BTTS",
}
_SELECTION_MAP = {
    "Home": "HOME",
    "Draw": "DRAW",
    "Away": "AWAY",
    "Yes": "YES",
    "No": "NO",
    "Home/Draw": "1X",
    "Draw/Away": "X2",
    "Home/Away": "12",
}

# Nombre EXACTO de mercado de temporada completa en API-Football; existen variantes
# como 'Goals Over/Under First Half' / '- Second Half' que deben excluirse explícitamente
# (un simple "in" haría match con esas también y contaminaría las cuotas de partido completo).
_FULL_MATCH_OU_BET_NAME = "Goals Over/Under"
# Total de córners del partido completo. Excluye 'Home Corners Over/Under',
# 'Total Corners (1st Half)', etc. Las líneas se limitan a las validadas en
# ml/features/corners.py (LINES) y SOLO a .5: una línea entera ('Over 9')
# puede empatar ("push") y settlement.py no contempla devoluciones.
_CORNERS_OU_BET_NAME = "Corners Over Under"
_CORNERS_LINES = ("8.5", "9.5")


_TOTAL_LINES = tuple(f"{l}" for l in TOTAL_LINES)              # "0.5".."4.5"
_TEAM_TOTAL_LINES = tuple(f"{l}" for l in TEAM_TOTAL_LINES)    # "0.5".."2.5"
_AH_LINES = {f"{l:+.1f}" for l in ASIAN_HANDICAP_LINES}        # "-2.5".."+2.5"


def _over_under(value_label: str, prefix: str, allowed_lines) -> tuple[str, str] | None:
    parts = value_label.split()
    if len(parts) != 2:
        return None
    direction, line = parts
    if direction not in ("Over", "Under") or line not in allowed_lines:
        return None
    return f"{prefix}{line}", direction.upper()


def _map_bet(bet_name: str, value_label: str) -> tuple[str, str] | None:
    """Traduce (tipo de apuesta, valor) de API-Football a (market, selection) nuestros.
    Devuelve None para todo lo que no modelamos (mitades, cuartos de hándicap, etc.)."""
    if bet_name == _FULL_MATCH_OU_BET_NAME:
        return _over_under(value_label, "OU_", _TOTAL_LINES)
    if bet_name == _CORNERS_OU_BET_NAME:
        return _over_under(value_label, "CORNERS_OU_", _CORNERS_LINES)
    if bet_name == "Total - Home":
        return _over_under(value_label, "TEAM_OU_HOME_", _TEAM_TOTAL_LINES)
    if bet_name == "Total - Away":
        return _over_under(value_label, "TEAM_OU_AWAY_", _TEAM_TOTAL_LINES)

    if bet_name == "Asian Handicap":
        # En API-Football el número es SIEMPRE la línea del local y los dos
        # valores de un par son los dos lados de esa misma línea:
        #   ('Home -0.5', 2.20) = local gana;  ('Away -0.5', 1.65) = visitante o empate.
        # Leerlo como "el visitante da 0.5" invierte el mercado y produce EVs
        # absurdos (+1000%): ambos lados no pueden ser favoritos a la vez.
        parts = value_label.split()
        if len(parts) != 2 or parts[0] not in ("Home", "Away"):
            return None
        try:
            home_line = float(parts[1])
        except ValueError:
            return None
        key = f"{home_line:+.1f}"
        if key not in _AH_LINES:   # descarta enteros y cuartos (implican devolución)
            return None
        return f"AH_{key}", parts[0].upper()

    if bet_name == "Exact Score":
        # '2:1' -> selección '2-1'. Solo hasta 4-4, que es lo que el modelo deriva.
        try:
            h, a = (int(x) for x in value_label.split(":"))
        except ValueError:
            return None
        if h > 4 or a > 4:
            return None
        return "EXACT_SCORE", f"{h}-{a}"

    return None


def upsert_odds_for_match(
    db: Session,
    match_id: int,
    odds_payload: dict,
    snapshot_type: str,
    recorded_at: datetime,
) -> int:
    """odds_payload es un elemento de /odds.response: {fixture, bookmakers:[{name, bets:[{name, values:[{value, odd}]}]}]}.

    Devuelve la cantidad de filas de cuotas insertadas.
    """
    inserted = 0
    for bookmaker in odds_payload.get("bookmakers", []):
        bookmaker_name = bookmaker.get("name", "unknown")
        for bet in bookmaker.get("bets", []):
            bet_name = bet.get("name", "")
            named_market = _MARKET_NAME_MAP.get(bet_name)

            for value in bet.get("values", []):
                value_label = value.get("value", "")
                odd_str = value.get("odd")
                if odd_str is None:
                    continue

                if named_market:
                    market = named_market
                    selection = _SELECTION_MAP.get(value_label)
                    if selection is None:
                        continue
                else:
                    mapped = _map_bet(bet_name, value_label)
                    if mapped is None:
                        continue
                    market, selection = mapped

                try:
                    odds_value = float(odd_str)
                except (TypeError, ValueError):
                    continue
                if odds_value <= 1.0:
                    continue

                db.add(Odds(
                    match_id=match_id,
                    bookmaker=bookmaker_name,
                    market=market,
                    selection=selection,
                    odds_value=odds_value,
                    snapshot_type=snapshot_type,
                    recorded_at=recorded_at,
                ))
                inserted += 1
    return inserted


def upsert_injuries(db: Session, injuries_payload: list[dict]) -> int:
    """injuries_payload = /injuries.response: [{player:{id,name,type,reason}, team:{id}, fixture:{date}}]"""
    inserted = 0
    for item in injuries_payload:
        team_raw = item.get("team", {})
        player_raw = item.get("player", {})
        team = db.execute(select(Team).where(Team.api_team_id == team_raw.get("id"))).scalar_one_or_none()
        if team is None:
            continue

        status_type = (player_raw.get("type") or "").upper()
        status = "SUSPENDED" if "SUSPEN" in status_type else "INJURED"

        db.add(Injury(
            team_id=team.id,
            player_name=player_raw.get("name", "unknown"),
            status=status,
            impact_score=1,
        ))
        inserted += 1
    return inserted
