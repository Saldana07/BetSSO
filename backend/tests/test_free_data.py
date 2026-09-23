"""Datos gratuitos nuevos: copias de football-data en Internet Archive y el
ajuste de tarjetas por desequilibrio del mercado 1X2."""
import math
from datetime import date

import joblib
import pytest

import ml.features.count_stats as cs
from ml.features.market_adjustment import GAP_CENTER, MarketAdjustment
from ml.ingestion.import_football_data_archive import season_code


def test_temporada_en_curso():
    assert season_code(date(2026, 9, 23)) == "2627"
    assert season_code(date(2027, 3, 1)) == "2627"
    assert season_code(date(2027, 7, 1)) == "2728"
    assert season_code(date(1999, 8, 1)) == "9900"


def test_lectura_del_archivo(tmp_path, monkeypatch):
    season = tmp_path / "2627"
    season.mkdir()
    (season / "E0.csv").write_text(
        "﻿Div,Date,HomeTeam,AwayTeam,Referee,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A\n"
        "E0,21/08/2026,Arsenal,Chelsea,M Oliver,15,9,6,3,10,12,7,4,2,3,0,1,2.0,3.5,3.8\n"
        ",,,,,,,,,,,,,,,,,,,\n",   # fila vacía al final, como en los CSV reales
        encoding="utf-8",
    )
    monkeypatch.setattr(cs, "ARCHIVE_DIR", tmp_path)
    df = cs._read_archive()
    assert len(df) == 1
    r = df.iloc[0]
    assert r.Division == "E0" and r.MatchDate.date() == date(2026, 8, 21)   # día primero
    assert (r.HomeShots, r.AwayShots, r.HomeYellow, r.AwayRed) == (15, 9, 2, 1)
    assert r.Referee == "M Oliver" and r.OddHome == 2.0


def test_ajuste_solo_si_esta_validado(tmp_path):
    path = tmp_path / "adj.joblib"
    joblib.dump({"cards": {"intercept": 0.0, "coef_gap": -0.31, "validated": True},
                 "shots": {"intercept": 0.0, "coef_gap": 0.5, "validated": False}}, path)
    adj = MarketAdjustment.load(path)
    # Partido parejo (desequilibrio 0) -> más tarjetas; favorito claro -> menos.
    parejo = adj.factor("cards", 0.35, 0.35)
    desigual = adj.factor("cards", 0.80, 0.08)
    assert parejo == pytest.approx(math.exp(-0.31 * (0 - GAP_CENTER)))
    assert parejo > 1.0 > desigual
    assert adj.factor("shots", 0.8, 0.1) == 1.0          # no validado
    assert adj.factor("cards", None, 0.3) == 1.0         # sin cuotas
    assert adj.factor("cards", float("nan"), 0.3) == 1.0


def test_sin_archivo_de_ajuste_todo_es_uno(tmp_path):
    assert MarketAdjustment.load(tmp_path / "no_existe.joblib").factor("cards", 0.8, 0.1) == 1.0
