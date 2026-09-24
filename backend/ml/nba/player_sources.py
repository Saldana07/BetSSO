"""Fuentes para las líneas de jugadores de la NBA (puntos, rebotes...).

  - stats.nba.com leaguegamelog (PlayerOrTeam=P): la estadística de TODOS los
    jugadores en todos los partidos de una temporada, en una petición. Gratis.
  - ESPN propBets: líneas de DraftKings por jugador (más/menos e "hitos" 8+,
    10+...), con apertura y actual. Gratis. Guarda las de partidos recientes
    (en la NBA, desde los playoffs de 2026); antes de eso, 404.
  - API-Basketball: las casas publican "Player Points", "Player Assists"...
    en la MISMA respuesta de /odds que ya se pide para los partidos: no cuesta
    consultas extra. El formato de esos valores se confirma con la NBA real
    (la WNBA y la NBL solo dieron muestras sueltas): lo que no se entiende se
    cuenta y se deja fuera, nunca se adivina.

Formato de ESPN verificado el 24/09/2026 en 39 de 39 pares: de los dos
registros de cada (jugador, tipo), el PRIMERO es "más de" (su precio coincide
con el hito N+, que es "más de N-0.5").
"""
import difflib
import json
import re
import unicodedata
from pathlib import Path

from ml.nba.sources import ESPN_CORE, STATS_HEADERS, _get

BASE_STATS = ("PTS", "REB", "AST", "FG3M")
COMBOS = {"PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"), "PRA": ("PTS", "REB", "AST")}
ALL_STATS = BASE_STATS + tuple(COMBOS)
STAT_LABELS = {"PTS": "puntos", "REB": "rebotes", "AST": "asistencias", "FG3M": "triples",
               "PR": "puntos + rebotes", "PA": "puntos + asistencias", "RA": "rebotes + asistencias",
               "PRA": "puntos + rebotes + asistencias"}

ESPN_OVER_UNDER = {
    "Total Points": "PTS", "Total Rebounds": "REB", "Total Assists": "AST", "Total 3-Point Field Goals": "FG3M",
    "Total Points and Rebounds": "PR", "Total Points and Assists": "PA", "Total Assists and Rebounds": "RA",
    "Total Points, Rebounds, and Assists": "PRA",
}
ESPN_MILESTONES = {
    "Points Milestones": "PTS", "Rebounds Milestones": "REB", "Assists Milestones": "AST",
    "3-Point Field Goals Milestones": "FG3M", "Points + Rebounds Milestones": "PR", "Points + Assists Milestones": "PA",
    "Rebounds + Assists Milestones": "RA", "Points + Assists + Rebounds Milestones": "PRA",
}

ATHLETE_CACHE = Path(__file__).resolve().parents[2] / "data" / "nba" / "espn_athletes.json"


def stat_value(stat: str, row) -> float:
    """Total de la estadística (o combinado) en una fila con pts, reb, ast, fg3m."""
    parts = COMBOS.get(stat, (stat,))
    return sum(float(getattr(row, p.lower()) if hasattr(row, p.lower()) else row[p.lower()]) for p in parts)


# --- Nombres ----------------------------------------------------------------------

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def name_key(name: str) -> str:
    """"Luka Dončić" -> "luka doncic"; "Jaren Jackson Jr." -> "jaren jackson"."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"[^a-z ]", " ", plain.replace("'", "").replace(".", ""))
    words = [w for w in plain.split() if w not in _SUFFIXES]
    return " ".join(words)


def match_player(name: str, candidates: dict[int, str]) -> int | None:
    """Empareja un nombre de una casa con los jugadores posibles (los de los
    dos equipos). Exacto; si no, mismo apellido y único; si no, parecido ≥ 0.88."""
    key = name_key(name)
    keys = {pid: name_key(n) for pid, n in candidates.items()}
    exact = [pid for pid, k in keys.items() if k == key]
    if len(exact) == 1:
        return exact[0]
    last = key.split()[-1] if key else ""
    same_last = [pid for pid, k in keys.items() if k.split()[-1:] == [last]]
    if len(same_last) == 1 and key.split()[:1] and keys[same_last[0]][:1] == key[:1]:
        return same_last[0]
    best = max(keys.items(), key=lambda kv: difflib.SequenceMatcher(None, kv[1], key).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, best[1], key).ratio() >= 0.88:
        return best[0]
    return None


# --- stats.nba.com ----------------------------------------------------------------

def fetch_player_game_log(season: str, season_type: str = "Regular Season") -> list[dict]:
    data = _get("https://stats.nba.com/stats/leaguegamelog", headers=STATS_HEADERS, timeout=180, params={
        "Counter": 0, "Direction": "ASC", "LeagueID": "00", "PlayerOrTeam": "P", "Season": season,
        "SeasonType": season_type, "Sorter": "DATE", "DateFrom": "", "DateTo": "",
    })
    rs = data["resultSets"][0]
    return [dict(zip(rs["headers"], row)) for row in rs["rowSet"]]


def parse_player_log(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        minutes = r.get("MIN")
        try:
            minutes = float(minutes) if minutes is not None else 0.0
        except (TypeError, ValueError):
            mm, _, ss = str(minutes).partition(":")
            minutes = float(mm or 0) + float(ss or 0) / 60
        out.append({"game_id": r["GAME_ID"], "player_id": r["PLAYER_ID"], "player_name": r["PLAYER_NAME"],
                    "team_id": r["TEAM_ID"], "minutes": minutes, "pts": r["PTS"] or 0, "reb": r["REB"] or 0,
                    "ast": r["AST"] or 0, "fg3m": r["FG3M"] or 0})
    return out


# --- ESPN -------------------------------------------------------------------------

def fetch_espn_props(event_id: str, provider_id: str = "100") -> list[dict] | None:
    """None si ESPN no tiene líneas de jugadores para ese partido."""
    try:
        return _get(f"{ESPN_CORE}/events/{event_id}/competitions/{event_id}/odds/{provider_id}/propBets",
                    params={"limit": 1000}, timeout=30, tries=1).get("items", [])
    except RuntimeError:
        return None


def espn_athlete_id(item: dict) -> str | None:
    m = re.search(r"/athletes/(\d+)", ((item.get("athlete") or {}).get("$ref") or ""))
    return m.group(1) if m else None


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_espn_props(items: list[dict], current_snapshot: str) -> list[dict]:
    """Filas {athlete_id, stat, line, side, odds, snapshot}. `current_snapshot`:
    CLOSE para partidos jugados, LIVE para los que vienen. Los pares de
    más/menos llegan como dos registros seguidos: el primero es "más de"."""
    rows: list[dict] = []
    pairs: dict[tuple, list[dict]] = {}
    for it in items:
        tname = (it.get("type") or {}).get("name")
        aid = espn_athlete_id(it)
        if not aid:
            continue
        odds = it.get("odds") or {}
        dec, total = odds.get("decimal") or {}, odds.get("total") or {}
        if tname in ESPN_OVER_UNDER:
            pairs.setdefault((tname, aid), []).append(it)
        elif tname in ESPN_MILESTONES:
            stat = ESPN_MILESTONES[tname]
            for snapshot, price, target in ((current_snapshot, dec.get("value"), (it.get("current") or {}).get("target", {}).get("value")),
                                            ("OPEN", dec.get("open"), (it.get("open") or {}).get("target", {}).get("value"))):
                price, target = _float(price), _float(target)
                if price and price > 1 and target:
                    rows.append({"athlete_id": aid, "stat": stat, "line": target - 0.5, "side": "OVER",
                                 "odds": price, "snapshot": snapshot})
    for (tname, aid), its in pairs.items():
        if len(its) != 2:
            continue   # sin su pareja no se sabe qué lado es
        stat = ESPN_OVER_UNDER[tname]
        for side, it in zip(("OVER", "UNDER"), its):
            dec, total = (it.get("odds") or {}).get("decimal") or {}, (it.get("odds") or {}).get("total") or {}
            for snapshot, price, line in ((current_snapshot, dec.get("value"), total.get("value")),
                                          ("OPEN", dec.get("open"), total.get("open"))):
                price, line = _float(price), _float(line)
                if price and price > 1 and line is not None:
                    rows.append({"athlete_id": aid, "stat": stat, "line": line, "side": side,
                                 "odds": price, "snapshot": snapshot})
    return rows


def _load_cache() -> dict:
    try:
        return json.loads(ATHLETE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def espn_athlete_names(ids: set[str]) -> dict[str, str]:
    """Nombre de cada atleta de ESPN (1 petición por atleta nuevo; se guardan)."""
    cache = _load_cache()
    missing = [i for i in ids if i not in cache]
    for aid in missing:
        try:
            cache[aid] = _get(f"{ESPN_CORE}/athletes/{aid}", timeout=30, tries=2).get("displayName") or ""
        except RuntimeError:
            continue
    if missing:
        ATHLETE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ATHLETE_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {i: cache.get(i, "") for i in ids}


# --- API-Basketball ---------------------------------------------------------------

APISPORTS_PROP_BETS = {
    "Player Points": "PTS", "Home Player Points": "PTS", "Away Player Points": "PTS",
    "Player Rebounds": "REB", "Home Player Rebounds": "REB", "Away Player Rebounds": "REB",
    "Player Assists": "AST", "Home Player Assists": "AST", "Away Player Assists": "AST",
    "Player Threes Made": "FG3M", "Home Player Threes Made": "FG3M", "Away Player Threes Made": "FG3M",
    "Player Points and Rebounds": "PR", "Home Player Points and Rebounds": "PR", "Away Player Points and Rebounds": "PR",
    "Player Points and Assists": "PA", "Home Player Points and Assists": "PA", "Away Player Points and Assists": "PA",
    "Player Assists and Rebounds": "RA", "Home Player Assists and Rebounds": "RA", "Away Player Assists and Rebounds": "RA",
    "Player Points, Assists and Rebounds": "PRA", "Home Player Points, Assists and Rebounds": "PRA",
    "Away Player Points, Assists and Rebounds": "PRA",
}
APISPORTS_MILESTONES = {
    "Player Points Milestones": "PTS", "Home Player Points Milestones": "PTS", "Away Player Points Milestones": "PTS",
    "Player Rebounds Milestones": "REB", "Home Player Rebounds Milestones": "REB", "Away Player Rebounds Milestones": "REB",
    "Player Assists Milestones": "AST", "Home Player Assists Milestones": "AST", "Away Player Assists Milestones": "AST",
    "Player Threes Made Milestones": "FG3M", "Away Player Threes Made Milestones": "FG3M",
    "Home Player Threes Made Milestones": "FG3M",
}

_NUM = r"(\d+(?:\.\d+)?)"
# Formatos posibles de un valor de más/menos. Se prueban en orden.
_OU_PATTERNS = [
    re.compile(rf"^(?P<name>.+?)\s*[-–:|]\s*(?P<side>over|under)\s*\(?{_NUM}\)?$", re.I),
    re.compile(rf"^(?P<side>over|under)\s*\(?{_NUM}\)?\s*[-–:|(]\s*(?P<name>.+?)\)?$", re.I),
    re.compile(rf"^(?P<name>.+?)\s+(?P<side>over|under)\s+{_NUM}$", re.I),
]
_MILESTONE = re.compile(rf"^(?P<name>.+?)\s*[-–:|]\s*{_NUM}\+?$")


def parse_apisports_props(response: list[dict]) -> tuple[list[dict], int]:
    """(filas {bookmaker, player_name, stat, line, side, odds}, valores sin entender)."""
    rows, unparsed = [], 0
    for resp in response:
        for bk in resp.get("bookmakers", []):
            for bet in bk.get("bets", []):
                name = bet.get("name") or ""
                stat = APISPORTS_PROP_BETS.get(name)
                mstat = APISPORTS_MILESTONES.get(name)
                if not stat and not mstat:
                    continue
                for v in bet.get("values", []):
                    label, odd = str(v.get("value", "")).strip(), _float(v.get("odd"))
                    if not odd or odd <= 1:
                        continue
                    parsed = None
                    if stat:
                        for pat in _OU_PATTERNS:
                            m = pat.match(label)
                            if m:
                                line = float(m.group(3) if m.re is not _OU_PATTERNS[1] else m.group(2))
                                parsed = (m.group("name").strip(), stat, line, m.group("side").upper())
                                break
                    else:
                        m = _MILESTONE.match(label)
                        if m:
                            parsed = (m.group("name").strip(), mstat, float(m.group(2)) - 0.5, "OVER")
                    if parsed is None:
                        unparsed += 1
                        continue
                    pname, pstat, line, side = parsed
                    rows.append({"bookmaker": bk["name"], "player_name": pname, "stat": pstat, "line": line,
                                 "side": side, "odds": odd})
    return rows, unparsed
