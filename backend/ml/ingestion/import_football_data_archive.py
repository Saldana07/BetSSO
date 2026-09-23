"""Copias públicas de football-data.co.uk en Internet Archive (archive.org).

football-data.co.uk es la fuente original de nuestro histórico (el espejo de
Kaggle lo republica una vez al mes) y está bloqueada en Colombia por el
filtro de Coljuegos contra sitios de apuestas. Internet Archive guarda copias
de sus CSV varias veces al mes. Verificado el 23/09: en 357 partidos que
están en ambas fuentes, 4,284 de 4,284 valores son idénticos, y aportan 211
partidos de la temporada 2026-27 posteriores al espejo, más el árbitro de las
ligas inglesas y escocesas.

Dos usos:
  - temporada en curso, las 22 divisiones: se refresca si hay una copia más
    nueva (datos frescos con la misma definición de tiro/tarjeta que el CSV);
  - temporadas pasadas de las 9 ligas inglesas/escocesas: una sola vez, solo
    por la columna `Referee` (factor árbitro de tarjetas).

Se usan solo copias ya archivadas (no se piden capturas nuevas).

Uso:
    python -m ml.ingestion.import_football_data_archive
"""
import json
import time
from datetime import date
from pathlib import Path

import httpx

ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "football_data_archive"
MANIFEST = ARCHIVE_DIR / "manifest.json"
CDX = "https://web.archive.org/cdx/search/cdx"
DIVISIONS = ("E0", "E1", "E2", "E3", "EC", "SC0", "SC1", "SC2", "SC3", "D1", "D2", "I1", "I2",
             "SP1", "SP2", "F1", "F2", "N1", "B1", "P1", "T1", "G1")
REFEREE_DIVISIONS = ("E0", "E1", "E2", "E3", "EC", "SC0", "SC1", "SC2", "SC3")
PAST_SEASONS = ("1718", "1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526")
PAUSE_SECONDS = 1.0   # cortesía con archive.org


def season_code(today: date) -> str:
    """Temporada europea en curso: desde julio es la que empieza ese año."""
    start = today.year if today.month >= 7 else today.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def latest_snapshot(client: httpx.Client, season: str, div: str) -> tuple[str, str] | None:
    """(timestamp, url original) de la copia más reciente con estado 200."""
    best = None
    for host in ("football-data.co.uk", "www.football-data.co.uk"):
        try:
            r = client.get(CDX, params={"url": f"{host}/mmz4281/{season}/{div}.csv", "matchType": "exact",
                                        "filter": "statuscode:200", "output": "txt", "fl": "timestamp,original"})
        except httpx.HTTPError:
            continue
        for line in r.text.splitlines():
            parts = line.split()
            if len(parts) == 2 and (best is None or parts[0] > best[0]):
                best = (parts[0], parts[1])
        time.sleep(PAUSE_SECONDS)
    return best


def _download(client: httpx.Client, timestamp: str, original: str) -> bytes | None:
    try:
        r = client.get(f"https://web.archive.org/web/{timestamp}id_/{original}")
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or b"HomeTeam" not in r.content[:2000]:
        return None
    return r.content


def sync(today: date | None = None, include_past: bool = True) -> dict:
    today = today or date.today()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    current = season_code(today)
    targets = [(current, d) for d in DIVISIONS]
    if include_past:
        # Pasadas: solo las que falten (no cambian una vez terminada la temporada).
        targets += [(s, d) for s in PAST_SEASONS for d in REFEREE_DIVISIONS if f"{s}/{d}" not in manifest]
    updated = unchanged = missing = 0
    with httpx.Client(timeout=90, follow_redirects=True, headers={"User-Agent": "apuesta-segura/1.0"}) as client:
        for season, div in targets:
            snap = latest_snapshot(client, season, div)
            key = f"{season}/{div}"
            if snap is None:
                missing += 1
                continue
            if manifest.get(key) == snap[0]:
                unchanged += 1
                continue
            content = _download(client, *snap)
            time.sleep(PAUSE_SECONDS)
            if content is None:
                missing += 1
                continue
            path = ARCHIVE_DIR / season / f"{div}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            manifest[key] = snap[0]
            updated += 1
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return {"updated": updated, "unchanged": unchanged, "missing": missing, "season": current}


def main():
    s = sync()
    print(f"Archivo de football-data (temporada {s['season']} + árbitros de temporadas pasadas): "
          f"{s['updated']} actualizados, {s['unchanged']} sin cambios, {s['missing']} sin copia.")


if __name__ == "__main__":
    main()
