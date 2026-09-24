"""Acciones bajo demanda: lo único que gasta cuota de API-Football.

Nada corre solo (no hay tareas programadas). La página lanza "actualizar"
la primera vez que se abre cada mañana (desde AUTO_UPDATE_HOUR, hora de
Colombia) y el resto con sus botones; daily.bat y settle.bat las lanzan a mano.

  actualizar  partidos, cuotas y picks de hoy y mañana (~2 + 1 por partido con historial)
  cuotas      cuotas de los partidos de las próximas 12 h y sus picks (1 por partido)
  verificar   resultado + estadísticas de los partidos con picks y liquidación
  completar   medición modelo-vs-casa e historial, con lo que sobre (deja reserva)
  nba         apartado NBA: calendario, resultados, líneas de ESPN, picks y liquidación
              (gratis: nba.com y ESPN; se lanza sola al abrir la pestaña si pasaron 3 h)
  nba_cuotas  lo mismo + cuotas de Betano/Pinnacle de la API de baloncesto (su propia cuota)

Cada acción corre en su propio proceso (la API la lanza y responde al
instante) con el candado .daily_lock, y deja su progreso en
data/actions_state.json para que la página lo muestre. La salida completa
de cada paso queda en actions.log.

Uso:
    python -m jobs.on_demand verificar
"""
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import MarketEvaluation, Match, NbaOdds, Odds, Prediction
from jobs.fetch_match_data import (
    DAILY_MARKER, _pending_clause, candidate_matches, daily_flow_done, request_budget,
)
from jobs.refresh_odds import DEFAULT_HOURS, FRESH_ODDS_HOURS, upcoming_needing_odds
from jobs.settle_predictions import CLOSING_WINDOW
from ml.ingestion import api_usage

BACKEND_DIR = Path(__file__).resolve().parents[1]
STATE_PATH = BACKEND_DIR / "data" / "actions_state.json"
LOCK_DIR = BACKEND_DIR / ".daily_lock"      # el mismo candado que usaban los .bat
LOG_PATH = BACKEND_DIR / "actions.log"
STALE_LOCK = timedelta(hours=2)
LOG_MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class Step:
    label: str
    module: str
    args: tuple[str, ...] = ()
    fatal: bool = True


TITLES = {
    "actualizar": "Actualizar partidos, cuotas y picks",
    "cuotas": f"Refrescar cuotas (próximas {DEFAULT_HOURS:g} h)",
    "verificar": "Verificar resultados de mis picks",
    "completar": "Completar datos con lo que sobre",
    "nba": "Actualizar NBA",
    "nba_cuotas": "Cuotas Betano/Pinnacle (NBA)",
}
NBA_ACTIONS = ("nba", "nba_cuotas")
NBA_AUTO_EVERY = timedelta(hours=3)   # gratis, pero sin repetir en cada visita


def steps_for(action: str, today: date) -> list[Step]:
    hoy, manana = today.isoformat(), (today + timedelta(days=1)).isoformat()
    fresh = ("--fresh-hours", f"{FRESH_ODDS_HOURS:g}")
    if action == "actualizar":
        # Los pasos de API no son fatales: si uno falla, los picks se generan
        # igual con lo que haya en la base de datos.
        return [
            Step("Datos gratuitos de football-data (no gasta cuota)", "ml.ingestion.import_football_data_archive", fatal=False),
            Step("Partidos de hoy", "ml.ingestion.sync_fixtures", ("--date", hoy), fatal=False),
            Step("Partidos de mañana", "ml.ingestion.sync_fixtures", ("--date", manana), fatal=False),
            Step("Emparejando equipos con el histórico", "ml.ingestion.reconcile_teams", ("--apply",), fatal=False),
            Step("Cuotas de hoy", "ml.ingestion.sync_odds", ("--date", hoy, "--snapshot-type", "LIVE", *fresh), fatal=False),
            Step("Cuotas de mañana", "ml.ingestion.sync_odds", ("--date", manana, "--snapshot-type", "LIVE", *fresh), fatal=False),
            Step("Generando los picks de hoy y mañana", "jobs.generate_daily_pick", ("--date", hoy, "--date", manana)),
        ]
    if action == "cuotas":
        return [Step("Cuotas de los próximos partidos y sus picks", "jobs.refresh_odds", ("--hours", f"{DEFAULT_HOURS:g}"))]
    if action == "verificar":
        return [
            Step("Resultado y estadísticas de los partidos con picks", "jobs.fetch_match_data", ("--tiers", "1", "--reserve", "0")),
            Step("Liquidando picks (y cuotas de cierre)", "jobs.settle_predictions"),
        ]
    if action == "completar":
        # Primero la medición modelo-vs-casa (la prueba que decide qué
        # mercados siguen); luego el historial colombiano y, al final, las
        # estadísticas de ese historial y los huecos del histórico europeo.
        return [
            Step("Partidos de la medición modelo vs casa", "jobs.fetch_match_data", ("--tiers", "2")),
            Step("Historial colombiano", "ml.ingestion.import_colombia", fatal=False),
            Step("Árbitros", "ml.ingestion.import_referees", fatal=False),
            Step("Estadísticas del historial", "jobs.fetch_match_data", ("--tiers", "3,4")),
            Step("Liquidando lo que llegó", "jobs.settle_predictions"),
        ]
    if action in NBA_ACTIONS:
        from ml.nba.train import PARAMS_PATH
        train = [] if PARAMS_PATH.exists() else [
            Step("Entrenando y validando el modelo NBA (solo la primera vez, ~15 min)", "ml.nba.train")]
        return [
            Step("Historial de la NBA (solo lo que falte)", "ml.nba.import_history", fatal=False),
            *train,
            Step("Calendario, resultados, líneas y picks" + (" con Betano/Pinnacle" if action == "nba_cuotas" else ""),
                 "jobs.nba_update", ("--apisports",) if action == "nba_cuotas" else ()),
        ]
    raise ValueError(f"Acción desconocida: {action}")


# --- Estado compartido con la API -------------------------------------------------

def read_state() -> dict:
    for _ in range(5):
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            time.sleep(0.05)   # a mitad de un reemplazo
    return {}


def write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    for _ in range(20):
        try:
            os.replace(tmp, STATE_PATH)
            return
        except PermissionError:   # Windows: la API lo está leyendo en ese instante
            time.sleep(0.05)


def update_state(**changes) -> dict:
    state = read_state()
    state.update(changes)
    write_state(state)
    return state


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) en Windows TERMINA el proceso: se pregunta al sistema.
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def current_run() -> dict | None:
    """La acción en curso, o None. Si su proceso murió (PC apagado, proceso
    cerrado), limpia el estado y el candado para que no quede bloqueado."""
    running = read_state().get("running")
    if not running:
        return None
    if pid_alive(int(running.get("pid", 0))):
        return running
    update_state(running=None)
    shutil.rmtree(LOCK_DIR, ignore_errors=True)
    return None


def lock_held() -> bool:
    """¿Hay candado vigente? Uno de más de STALE_LOCK es huérfano y se borra."""
    try:
        age = datetime.now() - datetime.fromtimestamp(LOCK_DIR.stat().st_mtime)
    except OSError:
        return False
    if age > STALE_LOCK:
        shutil.rmtree(LOCK_DIR, ignore_errors=True)
        return False
    return True


def acquire_lock() -> bool:
    lock_held()   # limpia un candado huérfano
    try:
        LOCK_DIR.mkdir()   # atómico: si ya existe, hay otra acción corriendo
        return True
    except FileExistsError:
        return False


def _parse_moment(value) -> datetime | None:
    """Fecha y hora con zona de un campo del estado; lo que no lo sea (el
    formato viejo de `auto_attempted` era solo la fecha) no cuenta."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else None


def morning_start(now: datetime) -> datetime:
    """Hoy a la hora de la actualización automática, en hora local."""
    settings = get_settings()
    local = now.astimezone(ZoneInfo(settings.local_timezone))
    return local.replace(hour=settings.auto_update_hour, minute=0, second=0, microsecond=0)


def update_due(now: datetime) -> bool:
    """¿Toca la actualización automática al abrir la página? Una vez cada
    mañana: desde AUTO_UPDATE_HOUR (hora local) si desde esa hora de hoy no se
    ha actualizado el día (a mano o sola), aunque la última haya fallado:
    reintentar en cada visita gastaría la cuota a ciegas.

    La cuota se renueva a las 19:00 de Colombia; con esto las consultas del día
    se gastan por la mañana, no al renovarse (antes la página actualizaba la
    primera vez que se abría después de las 19:00)."""
    start = morning_start(now)
    if now < start:
        return False
    state = read_state()
    moments = [_parse_moment((state.get("last") or {}).get("actualizar", {}).get("finished_at")),
               _parse_moment(state.get("auto_attempted"))]
    moments = [m for m in moments if m is not None]
    return not moments or max(moments) < start


def nba_update_due(now: datetime) -> bool:
    """La actualización NBA gratuita toca al abrir la pestaña si la última
    (manual o automática) terminó hace más de NBA_AUTO_EVERY."""
    last = [(read_state().get("last") or {}).get(a, {}).get("finished_at") for a in NBA_ACTIONS]
    last = [datetime.fromisoformat(t) for t in last if t]
    attempted = read_state().get("nba_auto_attempted")
    if attempted:
        last.append(datetime.fromisoformat(attempted))
    return not last or now - max(last) > NBA_AUTO_EVERY


# --- Ejecución --------------------------------------------------------------------

def _trim_log() -> None:
    try:
        if LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.write_bytes(LOG_PATH.read_bytes()[-LOG_MAX_BYTES // 2:])
    except OSError:
        pass


def _summary(output: str) -> str:
    """La última línea con contenido: los jobs terminan con su resumen."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip() and not ln.startswith(("Traceback", "  File"))]
    return lines[-1][:300] if lines else ""


def run_action(action: str) -> int:
    steps = steps_for(action, datetime.now(timezone.utc).date())
    if not acquire_lock():
        print("Ya hay una acción corriendo (candado .daily_lock). No se lanza otra.")
        return 2
    started = datetime.now(timezone.utc)
    used_before = api_usage.used_today()
    results: list[dict] = []
    ok = True
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        _trim_log()
        with LOG_PATH.open("a", encoding="utf-8") as log:
            log.write(f"\n===== {TITLES[action]}  [{started.isoformat(timespec='seconds')}] =====\n")
            for i, step in enumerate(steps):
                update_state(running={
                    "action": action, "title": TITLES[action], "pid": os.getpid(),
                    "started_at": started.isoformat(), "step": step.label, "step_index": i + 1, "steps": len(steps),
                })
                print(f"[{i + 1}/{len(steps)}] {step.label}...", flush=True)
                log.write(f"--- [{i + 1}/{len(steps)}] {step.label}\n")
                log.flush()
                proc = subprocess.run(
                    [sys.executable, "-m", step.module, *step.args], cwd=BACKEND_DIR, env=env,
                    capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags,
                )
                output = (proc.stdout or "") + (proc.stderr or "")
                log.write(output)
                summary = _summary(output)
                if summary:
                    print(f"      {summary}", flush=True)
                results.append({"label": step.label, "ok": proc.returncode == 0, "summary": summary})
                if proc.returncode != 0:
                    ok = False
                    if step.fatal:
                        break
    finally:
        finished = datetime.now(timezone.utc)
        spent = max(0, api_usage.used_today() - used_before) if finished.date() == started.date() else None
        state = read_state()
        last = state.get("last") or {}
        last[action] = {"finished_at": finished.isoformat(), "ok": ok, "spent": spent, "steps": results}
        state.update(running=None, last=last)
        write_state(state)
        shutil.rmtree(LOCK_DIR, ignore_errors=True)
    if action == "actualizar" and ok:
        DAILY_MARKER.write_text(started.date().isoformat(), encoding="utf-8")
    print(f"{'Listo' if ok else 'Terminó con errores (ver actions.log)'}; consultas gastadas: {'?' if spent is None else spent}")
    return 0 if ok else 1


# --- Cuánto costaría cada acción (para mostrarlo en los botones) -----------------

def estimates(db: Session, now: datetime) -> dict:
    today = now.date()
    used = api_usage.used_today()
    limit = api_usage.DAILY_LIMIT

    # actualizar: 2 (partidos de hoy y mañana) + cuotas no recientes de hoy y
    # mañana. Aproximado: los partidos de mañana que aún no están en la base
    # de datos no se cuentan.
    horizon = datetime.combine(today + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    odds_today_tomorrow = len(upcoming_needing_odds(db, now, (horizon - now).total_seconds() / 3600))

    verify = candidate_matches(db, now, tiers=frozenset({1}))
    has_closing = exists().where(Odds.match_id == Match.id, Odds.snapshot_type == "CLOSING")
    closing = db.execute(
        select(func.count(func.distinct(Match.id))).join(Prediction, Prediction.match_id == Match.id).where(
            Prediction.closing_odds.is_(None), Match.status == "FINISHED",
            Match.kickoff_utc > now - CLOSING_WINDOW, ~has_closing)
    ).scalar_one()

    pending = _pending_clause(now)
    measurement = db.execute(
        select(func.count(func.distinct(Match.id))).where(
            pending, exists().where(MarketEvaluation.match_id == Match.id, MarketEvaluation.settled_at.is_(None)))
    ).scalar_one()

    from jobs.nba_update import APISPORTS_FRESH, DEFAULT_HOURS as NBA_HOURS, upcoming_games
    nba_games = upcoming_games(db, now, NBA_HOURS)
    nba_fresh = set(db.execute(select(NbaOdds.game_id).where(
        NbaOdds.source == "APISPORTS", NbaOdds.recorded_at > now - APISPORTS_FRESH,
        NbaOdds.game_id.in_([g.id for g in nba_games]))).scalars())
    nba_todo = [g for g in nba_games if g.id not in nba_fresh]
    nba_days = {g.tipoff_utc.date() for g in nba_todo if g.apisports_game_id is None}

    return {
        "nba": {"requests": 0, "games": len(nba_games)},
        "nba_cuotas": {"requests": len(nba_todo) + len(nba_days), "games": len(nba_games)},
        "actualizar": {"requests": 2 + odds_today_tomorrow, "approximate": True},
        "cuotas": {"requests": len(upcoming_needing_odds(db, now, DEFAULT_HOURS))},
        # Cada partido: 1 para resultado + estadísticas y 1 para la cuota de cierre.
        "verificar": {"requests": 2 * len(verify) + closing, "matches": len(verify)},
        "completar": {"requests": request_budget(used, limit, daily_flow_done(today)), "measurement_matches": measurement},
    }


def main():
    # Lanzado por la API la salida va a DEVNULL con la codificación de Windows
    # (cp1252): un carácter fuera de ella tumbaría el proceso a mitad de acción.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    if len(sys.argv) != 2 or sys.argv[1] not in TITLES:
        print(__doc__)
        sys.exit(2)
    sys.exit(run_action(sys.argv[1]))


if __name__ == "__main__":
    main()
