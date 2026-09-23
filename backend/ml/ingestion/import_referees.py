"""Descarga, liga por liga y temporada por temporada, los partidos con su
árbitro (API-Football, plan gratuito: temporadas 2022-2024) para el factor
árbitro de tarjetas (ml/features/referees.py).

Una petición por (liga, temporada): 22 divisiones x 3 temporadas = 66, una
sola vez. Respeta el mismo presupuesto que jobs/fetch_match_data.py (reserva
para el flujo de las 08:00), así que se completa solo en 2-3 días. Cada vez
que baja algo nuevo, reconstruye y revalida la tabla de factores.

Uso:
    python -m ml.ingestion.import_referees
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from jobs.fetch_match_data import daily_flow_done, request_budget
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "referees"
SEASONS = (2022, 2023, 2024)
# División del CSV -> id de liga en API-Football. Orden = prioridad de descarga.
DIVISION_API_LEAGUE = {
    "E0": 39, "SP1": 140, "I1": 135, "D1": 78, "F1": 61, "N1": 88, "P1": 94, "B1": 144, "T1": 203, "G1": 197,
    "SC0": 179, "E1": 40, "D2": 79, "I2": 136, "SP2": 141, "F2": 62, "E2": 41, "E3": 42, "EC": 43,
    "SC1": 180, "SC2": 183, "SC3": 184,
}


def missing() -> list[tuple[str, int]]:
    return [(div, season) for div in DIVISION_API_LEAGUE for season in SEASONS
            if not (RAW_DIR / f"{div}_{season}.json").exists()]


def main() -> None:
    todo = missing()
    if not todo:
        print("Árbitros: histórico completo (22 divisiones x 3 temporadas).")
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    with APIFootballClient() as client:
        used, limit = client.quota()
        budget = request_budget(used, limit, daily_flow_done(datetime.now(timezone.utc).date()))
        for div, season in todo[:budget]:
            try:
                fixtures = client._get("/fixtures", {"league": DIVISION_API_LEAGUE[div], "season": season})
            except APIFootballError as exc:
                print(f"  [!] {div} {season}: {exc}")
                continue
            (RAW_DIR / f"{div}_{season}.json").write_text(json.dumps(fixtures), encoding="utf-8")
            downloaded += 1
    left = len(todo) - downloaded
    print(f"Árbitros: {downloaded} ligas-temporada descargadas, faltan {left}"
          + (" (se siguen bajando en las próximas corridas)." if left else "."))
    if downloaded:
        from ml.training.build_referee_factors import main as rebuild
        rebuild()


if __name__ == "__main__":
    main()
