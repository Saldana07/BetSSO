import pytest

from ml.ingestion.mappers import MAX_ODDS, _map_bet, upsert_odds_for_match


class _FakeDb:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)


def test_cuotas_absurdas_se_descartan():
    # Un marcador exacto de un partido con favorito 1.17 llega a cuota 1000+:
    # no cabe en NUMERIC(6,3) y es ruido para cualquier estrategia.
    payload = {"bookmakers": [{"name": "X", "bets": [{"name": "Exact Score", "values": [
        {"value": "1:0", "odd": "9.00"},
        {"value": "0:4", "odd": "1001.00"},
        {"value": "4:4", "odd": str(MAX_ODDS + 1)},
        {"value": "2:2", "odd": "0.5"},   # <= 1 no es una cuota
    ]}]}]}
    db = _FakeDb()
    n = upsert_odds_for_match(db, match_id=1, odds_payload=payload, snapshot_type="LIVE", recorded_at=None)
    assert n == 1
    assert db.rows[0].selection == "1-0"


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
