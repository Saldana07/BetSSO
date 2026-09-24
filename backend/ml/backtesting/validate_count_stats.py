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
     donde el modelo supera la tasa base en Brier Y en accuracy, las dos con
     al menos 2 errores estándar (comparación partido a partido). Las demás
     quedan sin fila -> SIN_VALIDAR -> el job no las apuesta; su fila de una
     validación anterior se borra. Persistir una línea desbalanceada (casi
     siempre Under) o una "mejora" de 0.07 puntos haría que el sistema la
     etiquetara como validada por una ilusión estadística.

Sin fuga: el calibrador guardado en models/ se reajusta con TODO (incluido
este periodo de prueba), así que aquí se usa uno ajustado solo con lo
anterior a --since. El ajuste por el desequilibrio del mercado (2 números,
solo tarjetas) sí es el guardado: su capacidad de sobreajuste es mínima.

Uso:
    python -m ml.backtesting.validate_count_stats --since 2025-09-01 [--stat shots] [--persist]
"""
import argparse
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ml.features.count_stats import ALL_SPECS, SPECS_BY_KEY, CountStatModel, StatSpec

T_REQUIRED = 2.0   # errores estándar: por debajo, la "mejora" puede ser ruido


def paired_t(diffs: np.ndarray) -> float:
    """Media de las diferencias partido a partido / su error estándar."""
    sd = diffs.std(ddof=1)
    return float(diffs.mean() / (sd / math.sqrt(len(diffs)))) if len(diffs) > 1 and sd > 0 else 0.0


def run(spec: StatSpec, since: str, max_matches: int) -> dict:
    from ml.features.market_adjustment import MarketAdjustment
    from ml.training.calibrate_count_stats import FIT_START, fit_calibrators, walk_forward_predictions

    model = CountStatModel(spec, calibration_path=None)
    if pd.Timestamp(since) > pd.Timestamp(FIT_START):
        model.calibrators = fit_calibrators(walk_forward_predictions(model, FIT_START, since), spec)
    market = MarketAdjustment.load()   # misma tubería que producción
    df = model.df
    test = df[df["MatchDate"] >= pd.Timestamp(since)].head(max_matches)

    rows = []
    for m in test.itertuples():
        scale = market.factor(spec.key, getattr(m, "p_home", None), getattr(m, "p_away", None))
        probs = model.market_probabilities(m.HomeTeam, m.AwayTeam, m.MatchDate, scale=scale)
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
    print(f"Calibración isotónica: {f'ajustada con {FIT_START} -> {since}' if model.calibrators else 'NO (probabilidades crudas)'}\n")

    summary = {}
    for line in spec.lines:
        p = res[f"p_over_{line}"].values
        y = res[f"over_{line}"].values
        base_rate = y.mean()

        majority = float(base_rate >= 0.5)
        correct_model = ((p > 0.5) == y).astype(float)
        correct_base = (y == majority).astype(float)       # predecir siempre la clase mayoritaria
        model_acc, base_acc = correct_model.mean(), correct_base.mean()
        model_brier = np.mean((p - y) ** 2)
        base_brier = np.mean((base_rate - y) ** 2)         # predecir siempre la tasa base
        t_acc = paired_t(correct_model - correct_base)
        t_brier = paired_t((p - y) ** 2 - (base_rate - y) ** 2)
        validated = t_acc >= T_REQUIRED and t_brier <= -T_REQUIRED

        print(f"--- Línea {line} (tasa base Over = {base_rate:.3f}) -> {'VALIDADA' if validated else 'no validada'} ---")
        print(f"  Accuracy: modelo {model_acc:.3f}  vs  base {base_acc:.3f}   (t = {t_acc:+.1f})")
        print(f"  Brier:    modelo {model_brier:.4f}  vs  base {base_brier:.4f}   (t = {t_brier:+.1f})")

        bins = pd.cut(p, bins=[0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0], include_lowest=True)
        calib = pd.DataFrame({"p": p, "y": y, "bin": bins}).groupby("bin", observed=True).agg(pred=("p", "mean"), real=("y", "mean"), n=("y", "size"))
        print("  Calibración (predicho -> real, n):")
        for b, r in calib.iterrows():
            print(f"    {b}: {r['pred']:.3f} -> {r['real']:.3f}  (n={int(r['n'])})")

        summary[spec.market(line)] = {"n": len(res), "accuracy": float(model_acc), "brier": float(model_brier),
                                      "base_accuracy": float(base_acc), "base_brier": float(base_brier),
                                      "t_accuracy": t_acc, "t_brier": t_brier, "validated": validated}
    return summary


def persist(summary: dict, since: str):
    from sqlalchemy import delete

    from app.database import SessionLocal
    from app.models import BacktestResult

    db = SessionLocal()
    try:
        # Una línea que dejó de validarse no puede seguir apostándose por su
        # fila vieja (el job usa la más reciente de cada mercado).
        db.execute(delete(BacktestResult).where(BacktestResult.model_version == "v1",
                                                BacktestResult.market.in_(list(summary))))
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
