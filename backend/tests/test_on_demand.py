"""Acciones bajo demanda: nada gasta cuota salvo lo que el usuario lanza."""
import subprocess
import sys
from datetime import date, datetime, timezone

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
    assert steps[-1] is generate[0] and generate[0].fatal     # los picks se generan aunque falle la API


def test_accion_desconocida():
    with pytest.raises(ValueError):
        on_demand.steps_for("borrar", date(2026, 9, 24))


def test_actualizacion_automatica_una_vez_por_dia_aunque_falle(sandbox):
    today = date(2026, 9, 24)
    assert on_demand.update_due(today)
    on_demand.update_state(auto_attempted=today.isoformat())
    assert not on_demand.update_due(today)                     # ya se intentó hoy: no reintenta en cada visita
    assert on_demand.update_due(date(2026, 9, 25))             # nueva cuota, nuevo intento
    (sandbox / ".daily_done").write_text("2026-09-25")
    assert not on_demand.update_due(date(2026, 9, 25))         # hecha a mano: tampoco


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
