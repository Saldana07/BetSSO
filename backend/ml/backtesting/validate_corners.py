"""Validación cronológica del modelo de córners contra resultados reales.

Recorre partidos recientes del CSV en orden de fecha; para cada uno predice
P(total > línea) usando SOLO partidos anteriores (sin fuga), y compara con lo
que pasó. Responde tres preguntas, en orden de importancia:

  1. ¿Tiene señal? Accuracy y Brier del modelo vs. los de la tasa base
     (predecir siempre el promedio). Si no la supera, el modelo no aporta
     nada y NO debe usarse para apostar.
  2. ¿Está calibrado? Cuando dice 60%, ¿acierta ~60%? Sin esto el EV/Kelly
     no significan nada.
  3. ¿Cuánto? Persiste la accuracy en backtest_results para que
     generate_daily_pick la use como historical_accuracy del mercado.

Uso:
    python -m ml.backtesting.validate_corners --since 2025-01-01 [--persist]
"""
import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ml.features.corners import LINES, CornerModel, _load, apply_calibration, corner_market_probabilities


def run(since: str, max_matches: int) -> dict:
    df = _load()
    model = CornerModel(df)
    test = df[df["MatchDate"] >= pd.Timestamp(since)].head(max_matches)

    rows = []
    for _, m in test.iterrows():
        exp = model.expected_corners(m["HomeTeam"], m["AwayTeam"], m["MatchDate"])
        if exp is None:
            continue
        # Misma tubería que producción: Poisson cruda + calibración si existe.
        probs = apply_calibration(corner_market_probabilities(*exp), model.calibrators)
        total = m["HomeCorners"] + m["AwayCorners"]
        row = {"total": total, "lambda": sum(exp)}
        for line in LINES:
            row[f"p_over_{line}"] = probs[f"CORNERS_OU_{line}_OVER"]
            row[f"over_{line}"] = int(total > line)
        rows.append(row)

    res = pd.DataFrame(rows)
    print(f"Partidos evaluados: {len(res)} (de {len(test)} candidatos; el resto sin historial suficiente)")
    print(f"Calibración isotónica: {'aplicada' if model.calibrators else 'NO (probabilidades crudas)'}\n")

    summary = {}
    for line in LINES:
        p = res[f"p_over_{line}"].values
        y = res[f"over_{line}"].values
        base_rate = y.mean()

        model_acc = ((p > 0.5) == y).mean()
        base_acc = max(base_rate, 1 - base_rate)          # predecir siempre la clase mayoritaria
        model_brier = np.mean((p - y) ** 2)
        base_brier = np.mean((base_rate - y) ** 2)         # predecir siempre la tasa base

        print(f"--- Línea {line} (tasa base Over = {base_rate:.3f}) ---")
        print(f"  Accuracy: modelo {model_acc:.3f}  vs  base {base_acc:.3f}   -> {'MEJOR' if model_acc > base_acc else 'NO mejora'}")
        print(f"  Brier:    modelo {model_brier:.4f}  vs  base {base_brier:.4f}   -> {'MEJOR' if model_brier < base_brier else 'NO mejora'}")

        # Calibración por deciles de probabilidad predicha
        bins = pd.cut(p, bins=[0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0], include_lowest=True)
        calib = pd.DataFrame({"p": p, "y": y, "bin": bins}).groupby("bin", observed=True).agg(pred=("p", "mean"), real=("y", "mean"), n=("y", "size"))
        print("  Calibración (predicho -> real, n):")
        for b, r in calib.iterrows():
            print(f"    {b}: {r['pred']:.3f} -> {r['real']:.3f}  (n={int(r['n'])})")
        print()

        summary[line] = {"n": len(res), "accuracy": float(model_acc), "brier": float(model_brier),
                         "base_accuracy": float(base_acc), "base_brier": float(base_brier)}
    return summary


def persist(summary: dict, since: str):
    from app.database import SessionLocal
    from app.models import BacktestResult

    db = SessionLocal()
    try:
        for line, s in summary.items():
            db.add(BacktestResult(
                model_version="v1",
                market=f"CORNERS_OU_{line}",
                season_start=since[:4],
                season_end=str(datetime.now(timezone.utc).year),
                n_bets=s["n"],
                accuracy=round(s["accuracy"], 4),
                base_accuracy=round(s["base_accuracy"], 4),
                brier_score=round(s["brier"], 5),
            ))
        db.commit()
        print(f"Persistidas {len(summary)} filas en backtest_results.")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2025-01-01")
    parser.add_argument("--max-matches", type=int, default=4000)
    parser.add_argument("--persist", action="store_true", help="Guarda la accuracy en backtest_results")
    args = parser.parse_args()

    summary = run(args.since, args.max_matches)
    if args.persist:
        persist(summary, args.since)


if __name__ == "__main__":
    main()
