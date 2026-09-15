"""Backtest contra CUOTAS REALES de cierre — la pregunta que importa:
¿el modelo le gana a las casas de apuestas?

Hasta ahora todo backtest fue contra un mercado simulado (probabilidades de
538 + un margen inventado). Este usa las cuotas reales de cierre de
football-data.co.uk (`Matches.csv`: OddHome/OddDraw/OddAway = promedio de
casas; MaxHome/... = mejor precio disponible; Over25/Under25 y sus Max).

Diseño, para que el resultado sea creíble:
  - Fuera de muestra en el TIEMPO: solo partidos posteriores al final de los
    datos de entrenamiento (538 termina en junio 2023).
  - Fuera de muestra en la FUENTE: los features se calculan replayando el
    propio CSV con `FeatureReplayer` (la misma lógica exacta que el
    entrenamiento, verificada bit a bit), sin tocar la BD.
  - El modelo se usa TAL CUAL está en producción (models/v1), sin reentrenar.
  - Mismos filtros que el job real: cuota 1.50-2.00, techo de EV plausible.
  - Se reporta con el promedio de casas (conservador) Y con el mejor precio
    (lo que consigue quien compara casas). Una ventaja que solo aparece con
    el mejor precio es frágil.

Además del backtest de apuestas, compara la calidad de las probabilidades
del modelo contra las del mercado sin margen (Brier): si el mercado tiene
mejor Brier, sabe más que nosotros, y eso explica cualquier resultado.

Uso:
    python -m ml.backtesting.real_odds_backtest [--since 2023-07-01] [--persist]
"""
import argparse
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd

from ml.backtesting.backtest import BacktestConfig, passes_production_gate, run_backtest
from ml.features.corners import CSV_PATH
from ml.features.poisson_xg import market_probabilities
from ml.features.replay import FeatureReplayer, MatchRecord
from ml.training.train_classifier import FEATURE_COLUMNS

MIN_HISTORY = 10  # mismo guarda que producción: sin historial, el modelo solo repite la tasa base


def build_frame(since: str) -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, low_memory=False)
    df["MatchDate"] = pd.to_datetime(df["MatchDate"])
    df = df.dropna(subset=["FTHome", "FTAway"]).sort_values(["Division", "MatchDate"]).reset_index(drop=True)

    replayer = FeatureReplayer()
    rows = []
    since_ts = pd.Timestamp(since)

    for m in df.itertuples(index=False):
        record = MatchRecord(
            league_id=m.Division,
            home_id=(m.Division, m.HomeTeam),
            away_id=(m.Division, m.AwayTeam),
            kickoff=m.MatchDate.to_pydatetime(),
            home_goals=int(m.FTHome),
            away_goals=int(m.FTAway),
        )
        # Replay completo desde 2000 (el estado necesita historia), pero solo
        # se evalúan los partidos posteriores al entrenamiento.
        if m.MatchDate >= since_ts:
            feats = replayer.features(record)
            if min(feats["_home_history"], feats["_away_history"]) >= MIN_HISTORY:
                rows.append({
                    "date": m.MatchDate, "division": m.Division,
                    "home": m.HomeTeam, "away": m.AwayTeam,
                    "home_goals": int(m.FTHome), "away_goals": int(m.FTAway),
                    "odd_h": m.OddHome, "odd_d": m.OddDraw, "odd_a": m.OddAway,
                    "max_h": m.MaxHome, "max_d": m.MaxDraw, "max_a": m.MaxAway,
                    "over25": m.Over25, "under25": m.Under25,
                    "max_over25": m.MaxOver25, "max_under25": m.MaxUnder25,
                    **{k: v for k, v in feats.items() if not k.startswith("_")},
                })
        replayer.update(record)

    return pd.DataFrame(rows)


def predict(frame: pd.DataFrame, model_dir: str, version: str) -> pd.DataFrame:
    clf = joblib.load(f"{model_dir}/classifier_{version}.joblib")
    enc = joblib.load(f"{model_dir}/label_encoder_{version}.joblib")
    gh = joblib.load(f"{model_dir}/goals_home_{version}.joblib")
    ga = joblib.load(f"{model_dir}/goals_away_{version}.joblib")

    X = frame[FEATURE_COLUMNS].values
    proba = clf.predict_proba(X)
    idx = {label: i for i, label in enumerate(enc.classes_)}
    frame = frame.copy()
    frame["p_home"], frame["p_draw"], frame["p_away"] = proba[:, idx["H"]], proba[:, idx["D"]], proba[:, idx["A"]]

    lam_h = np.clip(gh.predict(X), 0.05, None)
    lam_a = np.clip(ga.predict(X), 0.05, None)
    frame["p_over25"] = [market_probabilities(h, a)["OU_2.5_OVER"] for h, a in zip(lam_h, lam_a)]
    return frame


def brier_vs_market(frame: pd.DataFrame) -> dict:
    """¿Quién sabe más, el modelo o el mercado? Brier multiclase de cada uno."""
    f = frame.dropna(subset=["odd_h", "odd_d", "odd_a"])
    inv = np.stack([1 / f.odd_h, 1 / f.odd_d, 1 / f.odd_a], axis=1)
    market = inv / inv.sum(axis=1, keepdims=True)  # quita el margen
    model = f[["p_home", "p_draw", "p_away"]].values
    y = np.stack([f.home_goals > f.away_goals, f.home_goals == f.away_goals, f.home_goals < f.away_goals], axis=1).astype(float)
    return {
        "n": len(f),
        "brier_model": float(((model - y) ** 2).sum(axis=1).mean()),
        "brier_market": float(((market - y) ** 2).sum(axis=1).mean()),
        "acc_model": float((model.argmax(1) == y.argmax(1)).mean()),
        "acc_market": float((market.argmax(1) == y.argmax(1)).mean()),
    }


def bets_for(frame: pd.DataFrame, market: str, price: str) -> pd.DataFrame:
    """Construye el DataFrame que espera run_backtest para un mercado y tipo de precio."""
    if market == "1X2_HOME":
        col = "max_h" if price == "best" else "odd_h"
        return pd.DataFrame({"model_probability": frame.p_home, "bookmaker_odds": frame[col],
                             "actual_result": (frame.home_goals > frame.away_goals).astype(int)}).dropna()
    if market == "1X2_AWAY":
        col = "max_a" if price == "best" else "odd_a"
        return pd.DataFrame({"model_probability": frame.p_away, "bookmaker_odds": frame[col],
                             "actual_result": (frame.home_goals < frame.away_goals).astype(int)}).dropna()
    if market == "OU_2.5_OVER":
        col = "max_over25" if price == "best" else "over25"
        return pd.DataFrame({"model_probability": frame.p_over25, "bookmaker_odds": frame[col],
                             "actual_result": (frame.home_goals + frame.away_goals > 2.5).astype(int)}).dropna()
    if market == "OU_2.5_UNDER":
        col = "max_under25" if price == "best" else "under25"
        return pd.DataFrame({"model_probability": 1 - frame.p_over25, "bookmaker_odds": frame[col],
                             "actual_result": (frame.home_goals + frame.away_goals < 2.5).astype(int)}).dropna()
    raise ValueError(market)


MARKETS = ["1X2_HOME", "1X2_AWAY", "OU_2.5_OVER", "OU_2.5_UNDER"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2023-07-01", help="Inicio del período fuera de muestra (538 termina 2023-06)")
    parser.add_argument("--model-dir", default="models")
    parser.add_argument("--model-version", default="v1")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()

    print("*** BACKTEST CONTRA CUOTAS REALES DE CIERRE (fuera de muestra en tiempo y fuente) ***\n")
    frame = predict(build_frame(args.since), args.model_dir, args.model_version)
    print(f"Partidos evaluables desde {args.since}: {len(frame)} ({frame.division.nunique()} ligas, ambos equipos con >= {MIN_HISTORY} partidos)\n")

    b = brier_vs_market(frame)
    print("--- ¿Quién sabe más? (1X2, probabilidades sin margen) ---")
    print(f"  Brier   modelo {b['brier_model']:.4f}   mercado {b['brier_market']:.4f}   -> {'el MODELO' if b['brier_model'] < b['brier_market'] else 'el MERCADO'} está mejor calibrado")
    print(f"  Acierto modelo {b['acc_model']:.3f}   mercado {b['acc_market']:.3f}\n")

    config = BacktestConfig()
    results = {}
    for market in MARKETS:
        print(f"--- {market} ---")
        for price, label in (("avg", "promedio de casas"), ("best", "mejor precio")):
            r = run_backtest(bets_for(frame, market, price), config)
            skill = r.accuracy - r.base_accuracy
            gate, why = passes_production_gate(r, config)
            print(f"  [{label:18}] n={r.n_bets:5d}  acierto {r.accuracy:.3f} vs base {r.base_accuracy:.3f} (hab. {skill:+.3f})  "
                  f"yield {r.yield_pct:+6.2f}%  drawdown {r.max_drawdown_pct:5.1f}%  -> {'PASA' if gate else 'NO PASA: ' + why}")
            results[(market, price)] = r
        print()

    if args.persist:
        from app.database import SessionLocal
        from app.models import BacktestResult as Row

        # Se persiste con el precio PROMEDIO (conservador) y el nombre de
        # mercado que usa el job (sin sufijo de selección).
        db = SessionLocal()
        try:
            for market, name in (("1X2_HOME", "1X2"), ("OU_2.5_OVER", "OU_2.5")):
                r = results[(market, "avg")]
                db.add(Row(model_version=args.model_version, market=name,
                           season_start=args.since[:4], season_end=str(datetime.now(timezone.utc).year),
                           n_bets=r.n_bets, accuracy=round(r.accuracy, 4), base_accuracy=round(r.base_accuracy, 4),
                           yield_pct=round(r.yield_pct, 3), roi_pct=round(r.roi_pct, 3),
                           max_drawdown_pct=round(r.max_drawdown_pct, 3)))
            db.commit()
            print("Persistido en backtest_results (1X2 y OU_2.5, precio promedio): sustituye la evidencia simulada.")
        finally:
            db.close()


if __name__ == "__main__":
    main()
