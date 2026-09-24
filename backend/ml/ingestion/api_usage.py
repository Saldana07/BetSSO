"""Registro local de las peticiones hechas a API-Football, por día UTC.

Por qué: el contador de la propia API (`/status`) va con retraso. El 23/09
marcaba 42 de 100 cuando ya se habían gastado las 100, y cada paso que
calculaba su presupuesto con esa cifra creía tener más cuota de la real.
Todas las peticiones salen de este proyecto, así que contarlas aquí da el
número exacto; el presupuesto usa el mayor de los dos contadores.

También responde "¿en qué se gastó la cuota?":
    python -m ml.ingestion.api_usage
"""
import json
from datetime import datetime, timezone
from pathlib import Path

USAGE_PATH = Path(__file__).resolve().parents[2] / "data" / "api_usage.json"
KEEP_DAYS = 14
DAILY_LIMIT = 100   # plan gratuito; se renueva a las 00:00 UTC (19:00 en Colombia)

# Endpoint + parámetros -> para qué sirve (lo que se muestra en el informe).
def purpose(endpoint: str, params: dict) -> str:
    if endpoint == "/odds":
        return "cuotas"
    if endpoint == "/fixtures/headtohead":
        return "historial (enfrentamientos)"
    if endpoint == "/fixtures":
        if "id" in params:
            return "resultado + estadísticas de un partido"
        if "date" in params:
            return "partidos del día"
        if "season" in params:
            return "temporada completa (historial / árbitros)"
    if endpoint == "/fixtures/statistics":
        return "estadísticas de un partido"
    return endpoint.strip("/") or "otro"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _read() -> dict:
    try:
        return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(endpoint: str, params: dict | None = None) -> None:
    """Anota una petición (las de /status no gastan cuota y no se anotan)."""
    if endpoint == "/status":
        return
    data = _read()
    day = data.setdefault(_today(), {})
    key = purpose(endpoint, params or {})
    day[key] = day.get(key, 0) + 1
    for old in sorted(data)[:-KEEP_DAYS]:
        del data[old]
    try:
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass   # el registro nunca debe tumbar una petición


def used_today() -> int:
    return sum(_read().get(_today(), {}).values())


def breakdown(day: str | None = None) -> dict[str, int]:
    return dict(sorted(_read().get(day or _today(), {}).items(), key=lambda kv: -kv[1]))


def main():
    today = _today()
    usage = breakdown(today)
    print(f"Peticiones a API-Football hoy ({today} UTC; la cuota se renueva a las 19:00 de Colombia): {sum(usage.values())}")
    for key, n in usage.items():
        print(f"  {n:4d}  {key}")


if __name__ == "__main__":
    main()
