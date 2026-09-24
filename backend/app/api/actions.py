"""Botones de la página: lo único que gasta cuota de API-Football.

Cada acción se lanza en su propio proceso (jobs/on_demand.py) y la respuesta
vuelve al instante; la página consulta /api/actions/status para ver el
progreso. Leer la página (picks, rendimiento, etc.) nunca gasta cuota.
"""
import os
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from jobs import on_demand
from ml.ingestion import api_usage

router = APIRouter(prefix="/api/actions", tags=["actions"])

Action = Literal["actualizar", "cuotas", "verificar", "completar"]
_launch_lock = threading.Lock()


def _status() -> dict:
    now = datetime.now(timezone.utc)
    state = on_demand.read_state()
    return {
        "quota": {
            "used": api_usage.used_today(),
            "limit": api_usage.DAILY_LIMIT,
            "breakdown": api_usage.breakdown(),
            "resets_at": datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc).isoformat(),
        },
        "running": on_demand.current_run(),
        "last": state.get("last") or {},
        "update_due": on_demand.update_due(now.date()),
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
        today = datetime.now(timezone.utc).date()
        if auto:
            if action != "actualizar":
                raise HTTPException(400, "Solo la actualización del día se lanza sola.")
            if not on_demand.update_due(today):
                return {"started": False, "reason": "La actualización de hoy ya se hizo."}
        running = on_demand.current_run()
        if running or on_demand.lock_held():
            raise HTTPException(409, f"Ya está corriendo: {running['title'] if running else 'otra acción'}.")
        if auto:
            on_demand.update_state(auto_attempted=today.isoformat())
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
    return {"started": True, **_status()}
