"""Apartado de fútbol colombiano: separado de Europa y con modelo propio."""
from types import SimpleNamespace

import pandas as pd
import pytest

from app.api.predictions import _version
from app.core.config import Settings
from ml.features.colombia import ColombiaCountModels, is_colombian


def _match(league_name, country):
    return SimpleNamespace(league=SimpleNamespace(name=league_name, country=country))


@pytest.mark.parametrize("league,country,expected", [
    ("Primera A", "Colombia", True),
    ("Primera B", "Colombia", True),
    ("Copa Colombia", "Colombia", True),
    ("Liga Femenina", "Colombia", False),      # femenino: fuera
    ("Premier League", "England", False),
    ("Primera B", "Chile", False),             # mismo nombre, otro país
])
def test_que_es_futbol_colombiano(league, country, expected):
    assert is_colombian(_match(league, country)) is expected


def _raw(n_rounds):
    rows = []
    for i in range(n_rounds):
        day = pd.Timestamp("2026-07-01") + pd.Timedelta(days=7 * i)
        for home, away in (("Millonarios", "Santa Fe"), ("Nacional", "Junior")):
            rows.append({"Division": "COL1", "MatchDate": day, "HomeTeam": home, "AwayTeam": away,
                         "home_key": home.lower(), "away_key": away.lower(),
                         "home_corners": 6.0, "away_corners": 4.0,
                         "home_yellow": 2.0, "away_yellow": 3.0, "home_red": 0.0, "away_red": 0.0,
                         "home_shots": None, "away_shots": None, "home_shots_on_target": None,
                         "away_shots_on_target": None, "home_fouls": None, "away_fouls": None})
    return pd.DataFrame(rows)


def test_modelo_colombiano_necesita_historial():
    few = ColombiaCountModels(_raw(5))
    assert few.market_probabilities("Millonarios", "Santa Fe", pd.Timestamp("2026-09-01")) == {}
    enough = ColombiaCountModels(_raw(12))
    probs = enough.market_probabilities("Millonarios", "Santa Fe", pd.Timestamp("2026-10-01"))
    assert "CORNERS_OU_9.5_OVER" in probs and "CARDS_OU_4.5_OVER" in probs
    assert not any(k.startswith(("SHOTS", "FOULS")) for k in probs)   # sin cuotas en Colombia
    assert enough.n_matches == {"corners": 24, "cards": 24}


def test_sin_estadisticas_no_hay_modelo():
    assert ColombiaCountModels(_raw(0)).models == {}


def test_cada_apartado_su_version():
    s = Settings()
    assert _version("europa", s) == s.model_version
    assert _version("colombia", s) == s.colombia_model_version
    assert s.model_version != s.colombia_model_version
