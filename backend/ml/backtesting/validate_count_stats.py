"""Validación cronológica de los modelos de conteo (córners, tiros, tiros a
puerta, tarjetas, faltas) contra resultados reales, con la MISMA tubería que
producción (Poisson + calibración si existe).

Recorre partidos recientes del CSV en orden de fecha; para cada uno predice
P(total > línea) usando SOLO partidos anteriores (sin fuga), y compara con lo
que pasó. Responde tres preguntas, en orden de importancia:

  1. ¿Tiene señal? Accuracy y Brier del modelo vs. los de la tasa base
     (predecir siempre el promedio). Si no la supera, el modelo no aporta
     nada y NO debe usarse para apostar.
  2. ¿Está calibrado? Cuando dice 60%, ¿acierta ~60%? Sin esto el EV/Kelly
     no significan nada.
  3. ¿Cuánto? Persiste la accuracy en backtest_results SOLO para las líneas
     donde el modelo supera la tasa base en Brier Y en accuracy. Las demás
     quedan sin fila -> SIN_VALIDAR -> el job no las apuesta. Persistir una
     línea desbalanceada (casi siempre Under) haría que el sistema la
     etiquetara como alta confianza por una ilusión estadística.

Uso:
    python -m ml.backtesting.validate_count_stats --since 2025-09-01 [--stat shots] [--persist]
"""
import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ml.features.count_stats import ALL_SPECS, SPECS_BY_KEY, CountStatModel, StatSpec

MIN_BRIER_GAIN = 0.0005  # por debajo de esto la "mejora" es ruido


def run(spec: StatSpec, since: str, max_matches: int) -> dict:
    model = CountStatModel(spec)
    df = model.df
    test = df[df["MatchDate"] >= pd.Timestamp(since)].head(max_matches)

    rows = []
    for m in test.itertuples():
        probs = model.market_probabilities(m.HomeTeam, m.AwayTeam, m.MatchDate)
        if probs is None:
            continue
        total = m.home_value + m.away_value
        row = {"total": total}
        for line in spec.lines:
            row[f"p_over_{line}"] = probs[f"{spec.market(line)}_OVER"]
            row[f"over_{line}"] = int(total > line)
        rows.append(row)

    res = pd.DataFrame(rows)
    print(f"\n===== {spec.label} ({spec.market_prefix}) =====")
    print(f"Partidos evaluados: {len(res)} (de {len(test)} candidatos; el resto sin historial suficiente)")
    print(f"Calibración isotónica: {'aplicada' if model.calibrators else 'NO (probabilidades crudas)'}\n")

    summary = {}
    for line in spec.lines:
        p = res[f"p_over_{line}"].values
        y = res[f"over_{line}"].values
        base_rate = y.mean()

        model_acc = ((p > 0.5) == y).mean()
        base_acc = max(base_rate, 1 - base_rate)          # predecir siempre la clase mayoritaria
        model_brier = np.mean((p - y) ** 2)
        base_brier = np.mean((base_rate - y) ** 2)         # predecir siempre la tasa base
        validated = model_acc > base_acc and model_brier < base_brier - MIN_BRIER_GAIN

        print(f"--- Línea {line} (tasa base Over = {base_rate:.3f}) -> {'VALIDADA' if validated else 'no validada'} ---")
        print(f"  Accuracy: modelo {model_acc:.3f}  vs  base {base_acc:.3f}   -> {'MEJOR' if model_acc > base_acc else 'NO mejora'}")
        print(f"  Brier:    modelo {model_brier:.4f}  vs  base {base_brier:.4f}   -> {'MEJOR' if model_brier < base_brier else 'NO mejora'}")

        bins = pd.cut(p, bins=[0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0], include_lowest=True)
        calib = pd.DataFrame({"p": p, "y": y, "bin": bins}).groupby("bin", observed=True).agg(pred=("p", "mean"), real=("y", "mean"), n=("y", "size"))
        print("  Calibración (predicho -> real, n):")
        for b, r in calib.iterrows():
            print(f"    {b}: {r['pred']:.3f} -> {r['real']:.3f}  (n={int(r['n'])})")

        summary[spec.market(line)] = {"n": len(res), "accuracy": float(model_acc), "brier": float(model_brier),
                                      "base_accuracy": float(base_acc), "base_brier": float(base_brier),
                                      "validated": validated}
    return summary


def persist(summary: dict, since: str):
    from app.database import SessionLocal
    from app.models import BacktestResult

    db = SessionLocal()
    try:
        n = 0
        for market, s in summary.items():
            if not s["validated"]:
                continue
            db.add(BacktestResult(
                model_version="v1",
                market=market,
                season_start=since[:4],
                season_end=str(datetime.now(timezone.utc).year),
                n_bets=s["n"],
                accuracy=round(s["accuracy"], 4),
                base_accuracy=round(s["base_accuracy"], 4),
                brier_score=round(s["brier"], 5),
            ))
            n += 1
        db.commit()
        skipped = [m for m, s in summary.items() if not s["validated"]]
        print(f"Persistidas {n} líneas validadas en backtest_results. Sin validar (no se apuestan): {skipped or 'ninguna'}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stat", default="all", choices=["all", *SPECS_BY_KEY])
    parser.add_argument("--since", default="2025-09-01")
    parser.add_argument("--max-matches", type=int, default=20000)
    parser.add_argument("--persist", action="store_true", help="Guarda la accuracy de las líneas validadas en backtest_results")
    args = parser.parse_args()

    specs = ALL_SPECS if args.stat == "all" else (SPECS_BY_KEY[args.stat],)
    for spec in specs:
        summary = run(spec, args.since, args.max_matches)
        if args.persist:
            persist(summary, args.since)


if __name__ == "__main__":
    main()
