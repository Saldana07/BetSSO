"""Botones de la página: lo único que gasta cuota de API-Football.

Cada acción se lanza en su propio proceso (jobs/on_demand.py) y la respuesta
vuelve al instante; la página consulta /api/actions/status para ver el
progreso. Leer la página (picks, rendimiento, etc.) nunca gasta cuota.

Dos cosas automáticas, nada más:
  - la actualización del día, una por día de cuota: la lanza la página la
    primera vez que se abre desde la mañana (POST /actualizar?auto=true);
  - la verificación de antes de la renovación: un hilo del backend mira cada
    minuto y, en los últimos minutos del día de cuota, si quedan consultas y
    hay partidos jugados sin resultado, lanza "verificar_auto" (no hace falta
    tener la página abierta, sí el backend).
"""
import os
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import SessionLocal, get_db
from jobs import on_demand
from jobs.fetch_match_data import candidate_matches
from ml.ingestion import api_usage

router = APIRouter(prefix="/api/actions", tags=["actions"])

Action = Literal["actualizar", "cuotas", "verificar", "completar", "nba", "nba_cuotas"]
_launch_lock = threading.Lock()


def _status() -> dict:
    now = datetime.now(timezone.utc)
    state = on_demand.read_state()
    minutes = get_settings().auto_verify_minutes_before_reset
    return {
        "quota": {
            "used": api_usage.used_today(),
            "limit": api_usage.DAILY_LIMIT,
            "breakdown": api_usage.breakdown(),
            "resets_at": datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc).isoformat(),
        },
        # La API de baloncesto (NBA): misma clave, cuota aparte.
        "quota_basketball": {
            "used": api_usage.used_today(api_usage.BASKETBALL_USAGE_PATH),
            "limit": api_usage.DAILY_LIMIT,
            "breakdown": api_usage.breakdown(path=api_usage.BASKETBALL_USAGE_PATH),
        },
        "running": on_demand.current_run(),
        "last": state.get("last") or {},
        "update_due": on_demand.update_due(now),
        "auto_update_hour": get_settings().auto_update_hour,
        # Verificación automática de hoy (None si está apagada) y si ya pasó.
        "auto_verify_at": (on_demand.next_reset(now) - timedelta(minutes=minutes)).isoformat() if minutes > 0 else None,
        "auto_verify_done": state.get("auto_verify_day") == on_demand.last_reset(now).date().isoformat(),
        "titles": on_demand.TITLES,
    }


@router.get("/status")
def status():
    """Cuota usada hoy, acción en curso y resultado de la última de cada tipo."""
    return _status()


@router.get("/estimates")
def estimates(db: Session = Depends(get_db)):
    """Cuántas consultas gastaría cada botón ahora mismo (aproximado)."""
    return on_demand.estimates(db, datetime.now(timezone.utc))


@router.post("/{action}", status_code=202)
def launch(action: Action, auto: bool = Query(False, description="Actualización automática al abrir la página")):
    with _launch_lock:
        if auto:
            if action != "actualizar":
                raise HTTPException(400, "Solo la actualización del día se lanza sola.")
            if not on_demand.update_due(datetime.now(timezone.utc)):
                return {"started": False, "reason": "La actualización de hoy ya se hizo (una por día de cuota)."}
            # Sin consultas no se lanza (ni se marca como intentada): queda
            # pendiente para la próxima visita con cuota, antes de la renovación.
            if api_usage.used_today() >= api_usage.DAILY_LIMIT:
                return {"started": False, "reason": "Sin consultas de fútbol hasta las 19:00."}
        running = on_demand.current_run()
        if running or on_demand.lock_held():
            raise HTTPException(409, f"Ya está corriendo: {running['title'] if running else 'otra acción'}.")
        if auto:
            on_demand.update_state(auto_attempted=datetime.now(timezone.utc).isoformat())
        _spawn(action)
    return {"started": True, **_status()}


def _spawn(action: str) -> None:
    today = datetime.now(timezone.utc).date()
    proc = subprocess.Popen(
        [sys.executable, "-m", "jobs.on_demand", action], cwd=on_demand.BACKEND_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        # Sin ventana, y en su propia consola: cerrar la ventana del backend no la corta.
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    # Visible de inmediato (el proceso tarda ~1 s en arrancar y escribir su estado).
    on_demand.update_state(running={
        "action": action, "title": on_demand.TITLES[action], "pid": proc.pid,
        "started_at": datetime.now(timezone.utc).isoformat(), "step": "Arrancando...", "step_index": 0,
        "steps": len(on_demand.steps_for(action, today)),
    })


# --- Verificación automática antes de la renovación de la cuota ------------------

def evening_tick(now: datetime, spawn=_spawn) -> str | None:
    """Una pasada del vigilante. Devuelve lo que hizo (None: nada que mirar).

    Si hay otra acción corriendo, reintenta en la siguiente pasada (sigue
    dentro de la ventana). Sin cuota o sin nada pendiente no gasta nada y no
    vuelve a mirar hasta el día de cuota siguiente."""
    if not on_demand.auto_verify_due(now):
        return None
    with _launch_lock:
        if on_demand.current_run() or on_demand.lock_held():
            return "ocupado"
        day = on_demand.last_reset(now).date().isoformat()
        if api_usage.used_today() >= api_usage.DAILY_LIMIT:
            on_demand.update_state(auto_verify_day=day)
            return "sin cuota"
        db = SessionLocal()
        try:
            pending = candidate_matches(db, now, tiers=frozenset({1, 2}))
        finally:
            db.close()
        on_demand.update_state(auto_verify_day=day)
        if not pending:
            return "nada pendiente"
        spawn("verificar_auto")
        return "lanzada"


def start_evening_verifier(every_seconds: float = 60) -> threading.Event:
    """Hilo que mira cada minuto si toca la verificación automática. Devuelve
    el evento que lo detiene (al apagar el backend)."""
    stop = threading.Event()

    def loop():
        while True:
            try:
                done = evening_tick(datetime.now(timezone.utc))
                if done and done != "ocupado":
                    print(f"[verificación automática] {done}", flush=True)
            except Exception as exc:   # un fallo (BD caída) no debe tumbar el backend
                print(f"[verificación automática] error: {exc}", flush=True)
            if stop.wait(every_seconds):
                return

    threading.Thread(target=loop, name="verificacion-automatica", daemon=True).start()
    return stop
