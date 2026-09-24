"""Contador local de peticiones: el de la API va con retraso."""
import pytest

from ml.ingestion import api_usage
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(api_usage, "USAGE_PATH", tmp_path / "usage.json")
    return api_usage


def test_cuenta_por_proposito(ledger):
    ledger.record("/odds", {"fixture": 1})
    ledger.record("/fixtures", {"id": 5})
    ledger.record("/fixtures", {"id": 6})
    ledger.record("/fixtures/headtohead", {"h2h": "1-2"})
    ledger.record("/status")                      # no gasta cuota: no se anota
    assert ledger.used_today() == 4
    b = ledger.breakdown()
    assert b["resultado + estadísticas de un partido"] == 2 and b["cuotas"] == 1


@pytest.mark.parametrize("endpoint,params,expected", [
    ("/fixtures", {"date": "2026-09-24"}, "partidos del día"),
    ("/fixtures", {"league": 239, "season": 2024}, "temporada completa (historial / árbitros)"),
    ("/fixtures/headtohead", {"h2h": "1-2"}, "historial (enfrentamientos)"),
])
def test_propositos(endpoint, params, expected):
    assert api_usage.purpose(endpoint, params) == expected


def test_quota_usa_el_mayor_contador(ledger, monkeypatch):
    for _ in range(60):
        ledger.record("/odds", {})
    client = APIFootballClient.__new__(APIFootballClient)
    client.rate_limit = type("R", (), {"per_day_remaining": None})()
    monkeypatch.setattr(client, "get_status", lambda: {"requests": {"current": 42, "limit_day": 100}}, raising=False)
    assert client.quota() == (60, 100)            # el de la API (42) se había quedado atrás


def test_quota_agotada_no_tumba_el_presupuesto(ledger, monkeypatch):
    client = APIFootballClient.__new__(APIFootballClient)
    client.rate_limit = type("R", (), {"per_day_remaining": None})()

    def boom():
        raise APIFootballError("errores en /status: {'requests': 'You have reached the request limit for the day'}")
    monkeypatch.setattr(client, "get_status", boom, raising=False)
    assert client.quota() == (100, 100)
