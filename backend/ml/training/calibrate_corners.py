"""Calibración isotónica del modelo de córners (una curva por línea).

Por qué existe: la Poisson cruda está bien calibrada en promedio pero es
sobreconfiada en los extremos, y el sistema apuesta precisamente en los
extremos (donde más discrepa de la casa). Medido fuera de muestra sobre
6,926 partidos: cuando decía >=65% de Over 9.5 acertaba 61%; cuando decía
>=60%, 58%. Con eso, el "EV +13%" de un pick era mayormente error de modelo.

Cómo: predicciones walk-forward (cada partido con solo su pasado) sobre una
ventana de ajuste; isotónica P(over) -> frecuencia real. Antes de guardar se
reporta el efecto en un holdout posterior (nunca se ajusta y evalúa sobre lo
mismo). Luego se reajusta con todo y se guarda en
models/corners_calibration.joblib, que CornerModel carga automáticamente.

Uso:
    python -m ml.training.calibrate_corners            # ajusta y guarda
    python -m ml.training.calibrate_corners --dry-run  # solo reporta
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from ml.features.corners import CALIBRATION_PATH, LINES, CornerModel, corner_market_probabilities

FIT_START = "2024-09-01"
HOLDOUT_START = "2025-09-01"
# La isotónica no extrapola: fuera del rango visto devuelve el borde. Los
# límites evitan 0/1 exactos que romperían el EV/Kelly.
P_MIN, P_MAX = 0.02, 0.98


def walk_forward_predictions(model: CornerModel, start: str, end: str | None = None) -> pd.DataFrame:
    df = model.df[model.df["MatchDate"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["MatchDate"] < pd.Timestamp(end)]
    rows = []
    for m in df.itertuples():
        exp = model.expected_corners(m.HomeTeam, m.AwayTeam, m.MatchDate)
        if exp is None:
            continue
        probs = corner_market_probabilities(*exp)
        total = m.HomeCorners + m.AwayCorners
        row = {"date": m.MatchDate}
        for line in LINES:
            row[f"p_{line}"] = probs[f"CORNERS_OU_{line}_OVER"]
            row[f"y_{line}"] = float(total > line)
        rows.append(row)
    return pd.DataFrame(rows)


def fit_calibrators(preds: pd.DataFrame) -> dict[float, IsotonicRegression]:
    return {
        line: IsotonicRegression(out_of_bounds="clip", y_min=P_MIN, y_max=P_MAX).fit(preds[f"p_{line}"], preds[f"y_{line}"])
        for line in LINES
    }


def _report(preds: pd.DataFrame, calibrators: dict[float, IsotonicRegression]) -> None:
    for line in LINES:
        raw = preds[f"p_{line}"].to_numpy()
        y = preds[f"y_{line}"].to_numpy()
        cal = calibrators[line].predict(raw)
        brier = lambda p: float(np.mean((p - y) ** 2))  # noqa: E731
        print(f"--- Línea {line} (n={len(y)}) ---")
        print(f"  Brier: crudo {brier(raw):.4f} | calibrado {brier(cal):.4f} | tasa base {brier(np.full_like(y, y.mean())):.4f}")
        for thr in (0.55, 0.60, 0.65):
            sel = raw >= thr
            if sel.sum() == 0:
                continue
            print(f"  Over con p_cruda>={thr:.2f}: n={int(sel.sum())}  crudo dice {raw[sel].mean():.3f}"
                  f"  calibrado dice {cal[sel].mean():.3f}  real {y[sel].mean():.3f}")
        for thr in (0.55, 0.60, 0.65):
            sel = raw <= 1 - thr
            if sel.sum() == 0:
                continue
            print(f"  Under con p_cruda>={thr:.2f}: n={int(sel.sum())}  crudo dice {1 - raw[sel].mean():.3f}"
                  f"  calibrado dice {1 - cal[sel].mean():.3f}  real {1 - y[sel].mean():.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit-start", default=FIT_START)
    parser.add_argument("--holdout-start", default=HOLDOUT_START)
    parser.add_argument("--dry-run", action="store_true", help="Solo reporta; no guarda el calibrador")
    parser.add_argument("--out", default=str(CALIBRATION_PATH))
    args = parser.parse_args()

    model = CornerModel(calibration_path=None)  # crudo: no calibrar sobre lo ya calibrado
    print(f"Ajuste {args.fit_start} -> {args.holdout_start}; holdout {args.holdout_start} -> fin del CSV "
          f"({model.df['MatchDate'].max().date()})")
    fit = walk_forward_predictions(model, args.fit_start, args.holdout_start)
    holdout = walk_forward_predictions(model, args.holdout_start)
    print(f"Partidos: ajuste {len(fit)}, holdout {len(holdout)}\n")

    print("== Efecto en el HOLDOUT (calibrador ajustado solo con el periodo anterior) ==")
    _report(holdout, fit_calibrators(fit))

    if args.dry_run:
        return

    final = fit_calibrators(pd.concat([fit, holdout], ignore_index=True))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(final, args.out)
    print(f"\nCalibrador final (ajustado con todo, n={len(fit) + len(holdout)}) guardado en {args.out}")


if __name__ == "__main__":
    main()
