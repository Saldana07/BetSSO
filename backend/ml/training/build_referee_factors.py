"""Construye la tabla de factores de árbitro para tarjetas (ver
ml/features/referees.py) y la valida antes de activarla.

Fuentes (sin duplicar partidos; manda la primera):
  - columna Referee del histórico: copias de football-data en Internet
    Archive (ligas inglesas y escocesas, 2017-18 a hoy; misma fuente que las
    tarjetas);
  - data/raw/referees/{división}_{temporada}.json: API-Football, las 22
    divisiones, 2022-2024 (ml/ingestion/import_referees.py);
  - partidos de la BD con árbitro y estadísticas (temporada en curso).

El total esperado de cada partido ya incluye el ajuste validado por
desequilibrio del mercado (ml/features/market_adjustment.py): el árbitro
explica lo que queda, no lo que ya explican las cuotas.

Validación (sin fuga): partidos ordenados por fecha; para cada partido de las
temporadas 2024-25 y 2025-26 el factor usa solo partidos ANTERIORES de ese
árbitro. Se compara el Brier de las líneas 3.5 / 4.5 / 5.5 con y sin factor,
por partido, y se exige t <= -2 (mejora que no sea ruido). Si no, la tabla se
guarda con validated=False y el sistema no la usa.

Uso:
    python -m ml.training.build_referee_factors
"""
import glob
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import select

from ml.features.count_stats import (
    CARDS, DIVISION_COUNTRY, CountStatModel, competition_country, over_under_probabilities,
)
from ml.features.market_adjustment import MarketAdjustment
from ml.features.referees import REFEREE_PATH, referee_key, shrunk_factor

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "referees"
TEST_FROM = pd.Timestamp("2024-07-01")
TEST_TO = pd.Timestamp("2026-07-01")
VALIDATION_LINES = (3.5, 4.5, 5.5)
T_REQUIRED = -2.0


def _historical_rows(resolver) -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(str(RAW_DIR / "*.json"))):
        div = Path(f).stem.split("_")[0]
        country = DIVISION_COUNTRY.get(div)
        if country is None:
            continue
        for fx in json.load(open(f, encoding="utf-8")):
            ref = referee_key(fx["fixture"].get("referee"))
            if not ref:
                continue
            hk = resolver.resolve(fx["teams"]["home"]["name"], country)
            ak = resolver.resolve(fx["teams"]["away"]["name"], country)
            if hk and ak:
                rows.append({"MatchDate": pd.Timestamp(fx["fixture"]["date"][:10]), "home_key": hk, "away_key": ak,
                             "country": country, "ref": ref})
    return pd.DataFrame(rows, columns=["MatchDate", "home_key", "away_key", "country", "ref"])


def _db_rows(resolver) -> pd.DataFrame:
    """Temporada en curso: partidos de la BD con árbitro y tarjetas."""
    from app.database import SessionLocal
    from app.models import Match

    db = SessionLocal()
    try:
        matches = db.execute(select(Match).where(
            Match.status == "FINISHED", Match.referee.isnot(None),
            Match.home_yellow.isnot(None), Match.home_red.isnot(None),
        )).scalars().all()
        rows = []
        for m in matches:
            country = competition_country(m.league.name, m.league.country)
            hk = resolver.resolve(m.home_team.name, country)
            ak = resolver.resolve(m.away_team.name, country)
            ref = referee_key(m.referee)
            if hk and ak and ref:
                rows.append({"MatchDate": pd.Timestamp(m.kickoff_utc).tz_convert("UTC").tz_localize(None).normalize(),
                             "home_key": hk, "away_key": ak, "country": country, "ref": ref,
                             "HomeTeam": m.home_team.name, "AwayTeam": m.away_team.name,
                             "total": float(m.home_yellow + m.away_yellow + 2 * (m.home_red + m.away_red))})
        return pd.DataFrame(rows)
    finally:
        db.close()


def build() -> dict:
    model = CountStatModel(CARDS, calibration_path=None)
    market = MarketAdjustment.load()
    resolver = model.resolver
    df = model.df
    key = ["MatchDate", "home_key", "away_key"]
    cols = ["MatchDate", "home_key", "away_key", "HomeTeam", "AwayTeam", "country", "ref", "total", "p_home", "p_away"]

    archive = df[df["Referee"].notna()].copy() if "Referee" in df.columns else df.iloc[0:0].copy()
    archive["country"] = archive["Division"].map(DIVISION_COUNTRY)
    archive["ref"] = archive["Referee"].map(referee_key)
    archive["total"] = archive["home_value"] + archive["away_value"]

    api = df.merge(_historical_rows(resolver), on=key, how="inner")
    api["total"] = api["home_value"] + api["away_value"]

    frames = [archive[cols], api[cols]]
    db_rows = _db_rows(resolver)
    if len(db_rows):
        db_rows["p_home"] = None
        db_rows["p_away"] = None
        frames.append(db_rows[cols])
    data = (pd.concat(frames, ignore_index=True).dropna(subset=["ref", "country"])
            .drop_duplicates(key).sort_values("MatchDate", kind="stable").reset_index(drop=True))

    lam = []
    for r in data.itertuples():
        e = model.expected_values(r.HomeTeam, r.AwayTeam, r.MatchDate, r.country)
        lam.append(sum(e) * market.factor("cards", r.p_home, r.p_away) if e else np.nan)
    data["lam"] = lam
    data = data.dropna(subset=["lam"]).reset_index(drop=True)
    print(f"Partidos con árbitro y tarjetas: {len(data)} (archivo football-data {len(archive)}, API {len(api)}, "
          f"temporada en curso BD {len(db_rows)}); árbitros: {data.groupby(['country', 'ref']).ngroups}")

    # --- Validación walk-forward ---
    acc: dict[tuple[str, str], tuple[float, float]] = {}
    diffs, b0s, b1s = [], [], []
    for r in data.itertuples():
        a, l = acc.get((r.country, r.ref), (0.0, 0.0))
        if TEST_FROM <= r.MatchDate < TEST_TO:
            f = shrunk_factor(a, l, r.lam)
            p0 = over_under_probabilities(r.lam, CARDS)
            p1 = over_under_probabilities(r.lam * f, CARDS)
            d0 = np.mean([(p0[f"{CARDS.market(x)}_OVER"] - (r.total > x)) ** 2 for x in VALIDATION_LINES])
            d1 = np.mean([(p1[f"{CARDS.market(x)}_OVER"] - (r.total > x)) ** 2 for x in VALIDATION_LINES])
            diffs.append(d1 - d0)
            b0s.append(d0)
            b1s.append(d1)
        acc[(r.country, r.ref)] = (a + r.total, l + r.lam)

    n = len(diffs)
    report = {"n_test": n}
    if n >= 100:
        mean = float(np.mean(diffs))
        se = float(np.std(diffs, ddof=1) / math.sqrt(n))
        t = mean / se if se > 0 else 0.0
        report.update(brier_without=round(float(np.mean(b0s)), 4), brier_with=round(float(np.mean(b1s)), 4),
                      t_stat=round(t, 2))
        validated = t <= T_REQUIRED
    else:
        validated = False
    report["validated"] = validated

    table = {}
    for (country, ref), g in data.groupby(["country", "ref"]):
        table[(country, ref)] = (float(g["total"].sum()), float(g["lam"].sum()), int(len(g)))
    REFEREE_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"table": table, "validated": validated, "report": report}, REFEREE_PATH)
    return report


def main():
    report = build()
    if report["n_test"] < 100:
        print(f"Validación: solo {report['n_test']} partidos de prueba (< 100). Tabla guardada SIN activar.")
        return
    print(f"Validación en {report['n_test']} partidos de 2024-25 y 2025-26 (factor solo con partidos anteriores): "
          f"Brier {report['brier_without']} -> {report['brier_with']}, t = {report['t_stat']}")
    print("Factor árbitro ACTIVADO para tarjetas." if report["validated"]
          else "Mejora no significativa: tabla guardada SIN activar.")
    print(f"Guardada en {REFEREE_PATH}")


if __name__ == "__main__":
    main()
