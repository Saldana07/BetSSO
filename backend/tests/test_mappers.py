import pytest

from ml.ingestion.mappers import _map_bet


@pytest.mark.parametrize(
    "bet,value,expected",
    [
        ("Goals Over/Under", "Over 2.5", ("OU_2.5", "OVER")),
        ("Goals Over/Under", "Under 0.5", ("OU_0.5", "UNDER")),
        ("Goals Over/Under", "Over 4.5", ("OU_4.5", "OVER")),
        ("Goals Over/Under", "Over 8.5", None),              # fuera del modelo
        ("Goals Over/Under", "Over 2", None),                # línea entera: push
        ("Goals Over/Under First Half", "Over 1.5", None),   # mitad: no se modela
        ("Corners Over Under", "Under 9.5", ("CORNERS_OU_9.5", "UNDER")),
        ("Corners Over Under", "Over 10.5", None),           # línea no validada
        ("Total - Home", "Over 1.5", ("TEAM_OU_HOME_1.5", "OVER")),
        ("Total - Away", "Under 0.5", ("TEAM_OU_AWAY_0.5", "UNDER")),
        ("Total - Home", "Over 3.5", None),
        ("Asian Handicap", "Home -1.5", ("AH_-1.5", "HOME")),
        # El número es siempre la línea del LOCAL; 'Away' es el otro lado de esa
        # misma línea (visitante +1.5). Leerlo como "visitante -1.5" invertía el
        # mercado y daba EVs de +1000%.
        ("Asian Handicap", "Away -1.5", ("AH_-1.5", "AWAY")),
        ("Asian Handicap", "Home +0.5", ("AH_+0.5", "HOME")),
        ("Asian Handicap", "Home -1", None),                 # entera: devolución
        ("Asian Handicap", "Home -0.75", None),              # cuarto: devolución parcial
        ("Exact Score", "2:1", ("EXACT_SCORE", "2-1")),
        ("Exact Score", "0:0", ("EXACT_SCORE", "0-0")),
        ("Exact Score", "5:0", None),                        # fuera de lo derivado
        ("Correct Score - First Half", "1:0", None),
    ],
)
def test_map_bet(bet, value, expected):
    assert _map_bet(bet, value) == expected
