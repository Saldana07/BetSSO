"""Acciones bajo demanda: nada gasta cuota salvo lo que el usuario lanza."""
import subprocess
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from jobs import fetch_match_data, on_demand
from jobs.fetch_match_data import request_budget
from ml.ingestion import api_usage


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(on_demand, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(on_demand, "LOCK_DIR", tmp_path / ".daily_lock")
    monkeypatch.setattr(on_demand, "LOG_PATH", tmp_path / "actions.log")
    monkeypatch.setattr(fetch_match_data, "DAILY_MARKER", tmp_path / ".daily_done")
    monkeypatch.setattr(on_demand, "DAILY_MARKER", tmp_path / ".daily_done")
    monkeypatch.setattr(api_usage, "USAGE_PATH", tmp_path / "usage.json")
    return tmp_path


def test_verificar_solo_pide_partidos_con_picks_y_puede_usar_toda_la_cuota():
    fetch, settle = on_demand.steps_for("verificar", date(2026, 9, 24))
    assert fetch.module == "jobs.fetch_match_data" and fetch.args == ("--tiers", "1", "--reserve", "0")
    assert settle.module == "jobs.settle_predictions"
    assert request_budget(used=95, limit=100, daily_done=False, reserve=0) == 5


def test_actualizar_hoy_y_manana_con_un_solo_generado():
    steps = on_demand.steps_for("actualizar", date(2026, 9, 24))
    fixtures = [s.args for s in steps if s.module == "ml.ingestion.sync_fixtures"]
    assert fixtures == [("--date", "2026-09-24"), ("--date", "2026-09-25")]
    odds = [s for s in steps if s.module == "ml.ingestion.sync_odds"]
    assert all("--fresh-hours" in s.args for s in odds)         # actualizar dos veces no gasta el doble
    generate = [s for s in steps if s.module == "jobs.generate_daily_pick"]
    assert len(generate) == 1 and generate[0].args == ("--date", "2026-09-24", "--date", "2026-09-25")
    assert not any(s.fatal for s in steps)                    # un paso que falle no corta los demás


def test_la_actualizacion_del_dia_lo_hace_todo():
    """Una sola consulta grande: primero los resultados pendientes (con la
    reserva para el resto), luego el día y al final la NBA."""
    modules = [s.module for s in on_demand.steps_for("actualizar", date(2026, 9, 24))]
    assert modules[:2] == ["jobs.fetch_match_data", "jobs.settle_predictions"]
    first = on_demand.steps_for("actualizar", date(2026, 9, 24))[0]
    assert first.args == ("--tiers", "1")                      # sin --reserve 0: deja cuota para el resto
    assert modules.index("jobs.generate_daily_pick") < modules.index("jobs.nba_update")
    assert on_demand.steps_for("actualizar", date(2026, 9, 24))[-1].args == ("--apisports",)


def test_accion_desconocida():
    with pytest.raises(ValueError):
        on_demand.steps_for("borrar", date(2026, 9, 24))


def _bogota(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=ZoneInfo("America/Bogota"))


def test_actualizacion_automatica_por_la_manana_una_vez(sandbox):
    # Desde las 6:00 de Colombia; antes no (tampoco a las 20:00 recién renovada la cuota).
    assert not on_demand.update_due(_bogota(24, 5, 59))
    assert on_demand.update_due(_bogota(24, 6, 0))
    on_demand.update_state(auto_attempted=_bogota(24, 7, 30).isoformat())
    assert not on_demand.update_due(_bogota(24, 9, 0))         # ya se intentó esta mañana, aunque falle
    assert not on_demand.update_due(_bogota(24, 20, 0))        # la cuota se renueva a las 19:00: no se gasta ahí
    assert not on_demand.update_due(_bogota(25, 1, 0))         # madrugada: aún no es la mañana
    assert on_demand.update_due(_bogota(25, 6, 30))            # mañana siguiente, sí


def test_actualizacion_a_mano_cuenta_como_la_de_la_manana(sandbox):
    on_demand.update_state(last={"actualizar": {"finished_at": _bogota(24, 6, 45).isoformat()}})
    assert not on_demand.update_due(_bogota(24, 10, 0))
    on_demand.update_state(auto_attempted="2026-09-24")        # formato viejo (solo fecha): no cuenta
    assert on_demand.update_due(_bogota(25, 8, 0))


def test_una_sola_actualizacion_por_dia_de_cuota(sandbox):
    """Lo del 24/09: la de la mañana no pudo (sin cuota) y se lanzó al entrar a
    las 19:30 con la cuota nueva; a la mañana siguiente tocó otra vez."""
    assert on_demand.update_due(_bogota(24, 18, 59))           # pendiente todo el día hasta la renovación
    assert not on_demand.update_due(_bogota(24, 19, 30))       # renovada: ya no, la siguiente es la de la mañana
    assert on_demand.update_due(_bogota(25, 8, 0))
    # Si a las 19:30 se actualiza a mano, esa ya es la del día de cuota nuevo.
    on_demand.update_state(last={"actualizar": {"finished_at": _bogota(24, 19, 40).isoformat()}})
    assert not on_demand.update_due(_bogota(25, 8, 0))
    assert on_demand.update_due(_bogota(26, 8, 0))


def test_ventana_de_la_verificacion_automatica(sandbox):
    assert not on_demand.auto_verify_due(_bogota(24, 18, 29))
    assert on_demand.auto_verify_due(_bogota(24, 18, 30))     # 30 min antes de la renovación (19:00)
    assert on_demand.auto_verify_due(_bogota(24, 18, 59))
    assert not on_demand.auto_verify_due(_bogota(24, 19, 0))   # renovada: ya no tiene sentido
    on_demand.update_state(auto_verify_day=on_demand.last_reset(_bogota(24, 18, 40)).date().isoformat())
    assert not on_demand.auto_verify_due(_bogota(24, 18, 45))  # una vez por día de cuota
    assert on_demand.auto_verify_due(_bogota(25, 18, 40))


class _Db:
    def close(self):
        pass


def _tick(monkeypatch, now, pending, used=40):
    from app.api import actions
    launched = []
    monkeypatch.setattr(actions, "SessionLocal", _Db)
    monkeypatch.setattr(actions, "candidate_matches", lambda db, now, tiers: pending)
    monkeypatch.setattr(actions.api_usage, "used_today", lambda *a, **k: used)
    return actions.evening_tick(now, spawn=launched.append), launched


def test_verificacion_automatica_gasta_lo_que_sobra(sandbox, monkeypatch):
    result, launched = _tick(monkeypatch, _bogota(24, 18, 35), pending=[(1, object())])
    assert result == "lanzada" and launched == ["verificar_auto"]
    result, launched = _tick(monkeypatch, _bogota(24, 18, 40), pending=[(1, object())])
    assert result is None and launched == []                    # ya se hizo en este día de cuota


def test_verificacion_automatica_sin_nada_o_sin_cuota_no_gasta(sandbox, monkeypatch):
    assert _tick(monkeypatch, _bogota(24, 18, 35), pending=[]) == ("nada pendiente", [])
    assert _tick(monkeypatch, _bogota(25, 18, 35), pending=[(1, object())], used=100) == ("sin cuota", [])
    assert _tick(monkeypatch, _bogota(26, 12, 0), pending=[(1, object())]) == (None, [])   # fuera de la ventana


def test_verificacion_automatica_espera_si_hay_otra_accion(sandbox, monkeypatch):
    monkeypatch.setattr(on_demand, "lock_held", lambda: True)
    assert _tick(monkeypatch, _bogota(24, 18, 35), pending=[(1, object())]) == ("ocupado", [])
    monkeypatch.setattr(on_demand, "lock_held", lambda: False)
    assert _tick(monkeypatch, _bogota(24, 18, 36), pending=[(1, object())])[0] == "lanzada"


def test_verificacion_automatica_trae_picks_y_mercados_con_toda_la_cuota():
    fetch, settle = on_demand.steps_for("verificar_auto", date(2026, 9, 24))
    assert fetch.args == ("--tiers", "1,2", "--reserve", "0") and settle.module == "jobs.settle_predictions"


def test_candado(sandbox):
    assert on_demand.acquire_lock()
    assert not on_demand.acquire_lock()
    assert on_demand.lock_held()


def test_accion_de_proceso_muerto_no_bloquea(sandbox):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    on_demand.acquire_lock()
    on_demand.update_state(running={"action": "verificar", "title": "x", "pid": proc.pid})
    assert on_demand.current_run() is None
    assert not on_demand.lock_held()
    assert on_demand.read_state()["running"] is None


def test_run_action_registra_pasos_y_libera_el_candado(sandbox, monkeypatch):
    monkeypatch.setattr(on_demand, "steps_for", lambda action, today: [
        on_demand.Step("bien", "platform"),
        on_demand.Step("falla sin cortar", "modulo_que_no_existe_xyz", fatal=False),
        on_demand.Step("falla y corta", "modulo_que_no_existe_xyz"),
        on_demand.Step("no llega", "platform"),
    ])
    assert on_demand.run_action("verificar") == 1
    state = on_demand.read_state()
    assert state["running"] is None
    last = state["last"]["verificar"]
    assert [s["ok"] for s in last["steps"]] == [True, False, False]
    assert last["ok"] is False and last["spent"] == 0
    assert not on_demand.lock_held()
    assert "modulo_que_no_existe_xyz" in (sandbox / "actions.log").read_text(encoding="utf-8")


def test_run_action_no_corre_con_candado_ajeno(sandbox, monkeypatch):
    monkeypatch.setattr(on_demand, "steps_for", lambda action, today: [on_demand.Step("bien", "platform")])
    on_demand.acquire_lock()
    assert on_demand.run_action("verificar") == 2
    assert on_demand.lock_held()                               # no borra el candado de otro
    assert "last" not in on_demand.read_state()


def test_actualizar_bien_marca_el_dia(sandbox, monkeypatch):
    monkeypatch.setattr(on_demand, "steps_for", lambda action, today: [on_demand.Step("bien", "platform")])
    assert on_demand.run_action("actualizar") == 0
    assert fetch_match_data.daily_flow_done(datetime.now(timezone.utc).date())
