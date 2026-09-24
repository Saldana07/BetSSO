"""Fuentes de datos de la NBA.

Gratis y sin clave (no gastan ninguna cuota):
  - stats.nba.com  leaguegamelog: todos los partidos de una temporada con la
    estadística de cada equipo, en UNA petición (historial y resultados).
  - cdn.nba.com    scheduleLeagueV2: el calendario completo de la temporada
    en curso, con hora UTC, estado y marcador.
  - ESPN           cuotas de apertura/cierre de varias casas por partido, desde
    2017 (la vara para medir el modelo contra el mercado) y las líneas de los
    próximos partidos.

Con la clave de API-Football (misma cuenta de API-Sports, cuota APARTE de 100
diarias): API-Basketball, cuotas de Betano, Pinnacle, Bet365... para los
partidos de ayer/hoy/mañana (el plan gratis no da otras fechas).

Las funciones parse_* son puras (sin red) y tienen tests.
"""
import statistics
import time
from datetime import date, datetime, timezone

import httpx

from app.core.config import get_settings
from ml.ingestion import api_usage

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
# nba.com rechaza (403) o deja colgada la petición sin cabeceras de navegador.
NBA_HEADERS = {
    "User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate", "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
}
STATS_HEADERS = {**NBA_HEADERS, "x-nba-stats-origin": "stats", "x-nba-stats-token": "true"}

SEASON_TYPES = {"Regular Season": "REGULAR", "PlayIn": "PLAYIN", "Playoffs": "PLAYOFFS"}
# Prefijo del GAME_ID -> tipo de partido.
GAME_ID_TYPES = {"001": "PRESEASON", "002": "REGULAR", "004": "PLAYOFFS", "005": "PLAYIN", "006": "CUP"}

ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
# En la lista de "casas" de ESPN hay proyecciones de medios, no casas: fuera.
NOT_BOOKMAKERS = ("accuscore", "numberfire", "teamrankings")


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def season_of(day: date) -> str:
    """Temporada NBA a la que pertenece una fecha (empieza en octubre)."""
    return season_label(day.year if day.month >= 8 else day.year - 1)


def _get(url: str, headers: dict | None = None, params: dict | None = None, timeout: float = 60, tries: int = 3):
    last = None
    for attempt in range(tries):
        try:
            r = httpx.get(url, headers=headers, params=params, timeout=timeout, follow_redirects=True)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"{url}: HTTP {r.status_code}")
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
        if attempt < tries - 1:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"No se pudo leer {url}: {last}")


# --- stats.nba.com --------------------------------------------------------------

def fetch_league_game_log(season: str, season_type: str = "Regular Season") -> list[dict]:
    data = _get("https://stats.nba.com/stats/leaguegamelog", headers=STATS_HEADERS, timeout=120, params={
        "Counter": 0, "Direction": "ASC", "LeagueID": "00", "PlayerOrTeam": "T", "Season": season,
        "SeasonType": season_type, "Sorter": "DATE", "DateFrom": "", "DateTo": "",
    })
    rs = data["resultSets"][0]
    return [dict(zip(rs["headers"], row)) for row in rs["rowSet"]]


def parse_game_log(rows: list[dict], season: str) -> list[dict]:
    """Filas por equipo -> un registro por partido (local/visitante por MATCHUP:
    "LAL vs. MIN" es el local, "MIN @ LAL" el visitante)."""
    by_game: dict[str, dict[str, dict]] = {}
    for r in rows:
        side = "home" if " vs. " in r["MATCHUP"] else "away" if " @ " in r["MATCHUP"] else None
        if side:
            by_game.setdefault(r["GAME_ID"], {})[side] = r
    games = []
    for gid, sides in by_game.items():
        if set(sides) != {"home", "away"}:
            continue
        h, a = sides["home"], sides["away"]
        game = {
            "id": gid, "season": season, "season_type": GAME_ID_TYPES.get(gid[:3], "REGULAR"),
            "game_date": date.fromisoformat(h["GAME_DATE"][:10]),
            "home_team": (h["TEAM_ID"], h["TEAM_ABBREVIATION"], h["TEAM_NAME"]),
            "away_team": (a["TEAM_ID"], a["TEAM_ABBREVIATION"], a["TEAM_NAME"]),
            "status": "FINISHED",
        }
        for prefix, r in (("home", h), ("away", a)):
            game[f"{prefix}_pts"] = r["PTS"]
            game[f"{prefix}_fga"] = r["FGA"]
            game[f"{prefix}_fta"] = r["FTA"]
            game[f"{prefix}_oreb"] = r["OREB"]
            game[f"{prefix}_tov"] = r["TOV"]
        games.append(game)
    return games


# --- cdn.nba.com ----------------------------------------------------------------

def fetch_schedule() -> dict:
    return _get("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json", headers=NBA_HEADERS, timeout=120)


def parse_schedule(data: dict) -> list[dict]:
    ls = data["leagueSchedule"]
    season = ls["seasonYear"]
    out = []
    for gd in ls["gameDates"]:
        for g in gd["games"]:
            gid = g["gameId"]
            if gid[:3] not in GAME_ID_TYPES:
                continue   # All-Star y similares
            status = {1: "SCHEDULED", 2: "LIVE", 3: "FINISHED"}.get(g["gameStatus"], "SCHEDULED")
            if g.get("postponedStatus") not in (None, "", "N"):
                status = "POSTPONED"
            h, a = g["homeTeam"], g["awayTeam"]
            if not h.get("teamId") or not a.get("teamId"):
                continue   # cruces de playoffs aún por definir
            game = {
                "id": gid, "season": season, "season_type": GAME_ID_TYPES[gid[:3]],
                "game_date": date.fromisoformat(g["gameDateEst"][:10]),
                "tipoff_utc": datetime.fromisoformat(g["gameDateTimeUTC"].replace("Z", "+00:00")),
                "home_team": (h["teamId"], h["teamTricode"], f"{h['teamCity']} {h['teamName']}"),
                "away_team": (a["teamId"], a["teamTricode"], f"{a['teamCity']} {a['teamName']}"),
                "status": status,
                "neutral": bool(g.get("isNeutral")),
            }
            if status == "FINISHED":
                game["home_pts"], game["away_pts"] = h.get("score"), a.get("score")
            out.append(game)
    return out


# --- ESPN -----------------------------------------------------------------------

def fetch_espn_scoreboard(day: date) -> list[dict]:
    return _get(f"{ESPN_SITE}/scoreboard", params={"dates": day.strftime("%Y%m%d"), "limit": 50}).get("events", [])


def parse_espn_events(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        comp = e["competitions"][0]
        teams = {c["homeAway"]: c["team"]["displayName"] for c in comp["competitors"]}
        if set(teams) != {"home", "away"}:
            continue
        out.append({"event_id": e["id"], "home": teams["home"], "away": teams["away"],
                    "start": datetime.fromisoformat(e["date"].replace("Z", "+00:00"))})
    return out


def fetch_espn_odds(event_id: str) -> list[dict]:
    return _get(f"{ESPN_CORE}/events/{event_id}/competitions/{event_id}/odds", timeout=30).get("items", [])


def fetch_espn_injuries() -> dict:
    return _get(f"{ESPN_SITE}/injuries", timeout=30)


INJURY_STATUS_ES = {"Out": "Fuera", "Day-To-Day": "Duda (día a día)", "Questionable": "Duda",
                    "Doubtful": "Muy dudoso", "Probable": "Probable", "Suspension": "Sancionado"}


def parse_espn_injuries(data: dict) -> dict[str, list[dict]]:
    """{apodo del equipo: [{jugador, estado, comentario, fecha}]} del parte de
    lesiones de ESPN (gratis). Fuera primero."""
    order = {"Out": 0, "Suspension": 0, "Doubtful": 1, "Questionable": 2, "Day-To-Day": 2, "Probable": 3}
    out: dict[str, list[dict]] = {}
    for team in data.get("injuries", []):
        name = team.get("displayName") or ""
        rows = []
        for inj in team.get("injuries", []):
            status = inj.get("status") or ""
            rows.append({"player": (inj.get("athlete") or {}).get("displayName", "?"),
                         "status": INJURY_STATUS_ES.get(status, status), "out": order.get(status, 9) == 0,
                         "comment": inj.get("shortComment") or "", "date": inj.get("date"),
                         "_order": order.get(status, 9)})
        rows.sort(key=lambda r: r.pop("_order"))
        if name:
            out[name.split()[-1].lower()] = rows
    return out


def american_to_decimal(american) -> float | None:
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a >= 100:
        return round(1 + a / 100, 3)
    if a <= -100:
        return round(1 + 100 / -a, 3)
    return None


def line_key(value: float) -> str:
    """-9.5 -> "-9.5", 220.0 -> "220"."""
    return f"{float(value):g}"


def _decimal(block: dict | None, field: str) -> float | None:
    """{"spread": {"decimal": 1.95, "american": "-105"}} -> 1.95."""
    v = (block or {}).get(field) or {}
    return v.get("decimal") or american_to_decimal(v.get("american"))


def parse_espn_odds(items: list[dict]) -> list[dict]:
    """Ítems de ESPN -> filas de cuota {bookmaker, market, selection, odds, snapshot}.

    Los campos de arriba (spread, overUnder, moneyLine...) son la última línea
    antes del partido: CLOSE. Los bloques `open` (solo desde ~2023) son la
    apertura. La línea del hándicap es la del LOCAL."""
    rows = []
    for it in items:
        book = (it.get("provider") or {}).get("name") or ""
        if not book or "live" in book.lower() or book.lower() in NOT_BOOKMAKERS:
            continue
        home, away = it.get("homeTeamOdds") or {}, it.get("awayTeamOdds") or {}

        def add(market, selection, odds, snapshot):
            if odds and odds > 1.0:
                rows.append({"bookmaker": book, "market": market, "selection": selection,
                             "odds": float(odds), "snapshot": snapshot})

        spread, total = it.get("spread"), it.get("overUnder")
        if spread is not None and abs(float(spread)) <= 30:
            m = f"SPREAD_{line_key(spread)}"
            add(m, "HOME", american_to_decimal(home.get("spreadOdds")), "CLOSE")
            add(m, "AWAY", american_to_decimal(away.get("spreadOdds")), "CLOSE")
        if total is not None and 150 <= float(total) <= 300:
            m = f"TOTAL_{line_key(total)}"
            add(m, "OVER", american_to_decimal(it.get("overOdds")), "CLOSE")
            add(m, "UNDER", american_to_decimal(it.get("underOdds")), "CLOSE")
        add("ML", "HOME", american_to_decimal(home.get("moneyLine")), "CLOSE")
        add("ML", "AWAY", american_to_decimal(away.get("moneyLine")), "CLOSE")

        # Apertura.
        ho, ao = home.get("open") or {}, away.get("open") or {}
        hs = ((ho.get("pointSpread") or {}).get("american"))
        try:
            open_spread = float(hs) if hs not in (None, "", "EVEN") else None
        except ValueError:
            open_spread = None
        if open_spread is not None and abs(open_spread) <= 30:
            m = f"SPREAD_{line_key(open_spread)}"
            add(m, "HOME", _decimal(ho, "spread"), "OPEN")
            add(m, "AWAY", _decimal(ao, "spread"), "OPEN")
        add("ML", "HOME", _decimal(ho, "moneyLine"), "OPEN")
        add("ML", "AWAY", _decimal(ao, "moneyLine"), "OPEN")
        tot = (it.get("open") or {})
        tline = ((tot.get("total") or {}).get("american") or "").lstrip("ou")
        try:
            open_total = float(tline) if tline else None
        except ValueError:
            open_total = None
        if open_total is not None and 150 <= open_total <= 300:
            m = f"TOTAL_{line_key(open_total)}"
            add(m, "OVER", _decimal(tot, "over"), "OPEN")
            add(m, "UNDER", _decimal(tot, "under"), "OPEN")
    return rows


def consensus_lines(rows: list[dict], snapshot: str = "CLOSE") -> dict:
    """Línea del mercado: mediana entre casas del hándicap del local y del total."""
    spreads = [float(r["market"].split("_", 1)[1]) for r in rows
               if r["snapshot"] == snapshot and r["market"].startswith("SPREAD_") and r["selection"] == "HOME"]
    totals = [float(r["market"].split("_", 1)[1]) for r in rows
              if r["snapshot"] == snapshot and r["market"].startswith("TOTAL_") and r["selection"] == "OVER"]
    return {"spread": statistics.median(spreads) if spreads else None,
            "total": statistics.median(totals) if totals else None}


# --- API-Basketball (clave de API-Sports, cuota propia) -------------------------

BASKETBALL_URL = "https://v1.basketball.api-sports.io"
NBA_LEAGUE_ID = 12


class BasketballClient:
    """Cada petición se anota en data/api_usage_basketball.json (la de /status
    va con retraso, igual que en fútbol)."""

    def __init__(self):
        self._client = httpx.Client(base_url=BASKETBALL_URL, timeout=30,
                                    headers={"x-apisports-key": get_settings().api_football_key})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def _get(self, endpoint: str, params: dict) -> list:
        api_usage.record(endpoint, params, path=api_usage.BASKETBALL_USAGE_PATH)
        body = self._client.get(endpoint, params=params).json()
        errors = body.get("errors")
        if errors:
            raise RuntimeError(f"API-Basketball {endpoint}: {errors}")
        return body.get("response") or []

    def nba_games(self, day: date) -> list[dict]:
        games = self._get("/games", {"date": day.isoformat(), "timezone": "UTC"})
        return [g for g in games if g["league"]["id"] == NBA_LEAGUE_ID]

    def odds(self, game_id: int) -> list[dict]:
        return self._get("/odds", {"game": game_id})


def parse_apisports_odds(response: list[dict]) -> list[dict]:
    """Ganador (con prórroga), hándicap asiático y total de puntos, líneas .5
    y enteras tal cual (la liquidación solo acepta .5)."""
    rows = []
    for resp in response:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                name = bet.get("name")
                for v in bet.get("values", []):
                    try:
                        odds = float(v["odd"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    label = str(v.get("value", ""))
                    market = selection = None
                    if name == "Home/Away" and label in ("Home", "Away"):
                        market, selection = "ML", label.upper()
                    elif name == "Asian Handicap":
                        # El número es SIEMPRE la línea del local, también en el
                        # lado "Away" (igual que en API-Football). Verificado el
                        # 24/09 en Betano: 'Away +1.5' paga 1.80 con el
                        # visitante favorito a 1.70 => es "visitante gana por 2+",
                        # no "visitante +1.5".
                        side, _, line = label.partition(" ")
                        try:
                            line_v = float(line)
                        except ValueError:
                            continue
                        if side in ("Home", "Away"):
                            market, selection = f"SPREAD_{line_key(line_v)}", side.upper()
                    elif name == "Over/Under":
                        side, _, line = label.partition(" ")
                        try:
                            line_v = float(line)
                        except ValueError:
                            continue
                        if side in ("Over", "Under") and 150 <= line_v <= 300:
                            market, selection = f"TOTAL_{line_key(line_v)}", side.upper()
                    if market and odds > 1.0:
                        rows.append({"bookmaker": bk["name"], "market": market, "selection": selection,
                                     "odds": odds, "snapshot": "LIVE"})
    return rows


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
