"""Calibración isotónica de los modelos de conteo (una curva por línea y
estadística): córners, tiros, tiros a puerta, tarjetas, faltas.

Por qué existe: la Poisson cruda está bien calibrada en promedio pero es
sobreconfiada en los extremos, y el sistema apuesta precisamente en los
extremos (donde más discrepa de la casa). Medido en córners fuera de
muestra sobre 6,926 partidos: cuando decía >=65% de Over 9.5 acertaba 61%.
Con eso, el "EV +13%" de un pick era mayormente error de modelo.

Cómo: predicciones walk-forward (cada partido con solo su pasado) sobre una
ventana de ajuste; isotónica P(over) -> frecuencia real. Antes de guardar se
reporta el efecto en un holdout posterior (nunca se ajusta y evalúa sobre lo
mismo). Luego se reajusta con todo y se guarda en
models/<stat>_calibration.joblib, que CountStatModel carga automáticamente.

Uso:
    python -m ml.training.calibrate_count_stats                 # todas
    python -m ml.training.calibrate_count_stats --stat shots    # una
    python -m ml.training.calibrate_count_stats --dry-run       # solo reporta
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from ml.features.count_stats import ALL_SPECS, SPECS_BY_KEY, CountStatModel, StatSpec

FIT_START = "2024-09-01"
HOLDOUT_START = "2025-09-01"
# La isotónica no extrapola: fuera del rango visto devuelve el borde. Los
# límites evitan 0/1 exactos que romperían el EV/Kelly.
P_MIN, P_MAX = 0.02, 0.98


def walk_forward_predictions(model: CountStatModel, start: str, end: str | None = None) -> pd.DataFrame:
    spec = model.spec
    df = model.df[model.df["MatchDate"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["MatchDate"] < pd.Timestamp(end)]
    rows = []
    for m in df.itertuples():
        exp = model.expected_values(m.HomeTeam, m.AwayTeam, m.MatchDate)
        if exp is None:
            continue
        probs = model.raw_probabilities(*exp)
        total = m.home_value + m.away_value
        row = {"date": m.MatchDate, "lambda": sum(exp), "total": total}
        for line in spec.lines:
            row[f"p_{line}"] = probs[f"{spec.market(line)}_OVER"]
            row[f"y_{line}"] = float(total > line)
        rows.append(row)
    return pd.DataFrame(rows)


def fit_calibrators(preds: pd.DataFrame, spec: StatSpec) -> dict[float, IsotonicRegression]:
    return {
        line: IsotonicRegression(out_of_bounds="clip", y_min=P_MIN, y_max=P_MAX).fit(preds[f"p_{line}"], preds[f"y_{line}"])
        for line in spec.lines
    }


def _report(preds: pd.DataFrame, calibrators: dict[float, IsotonicRegression], spec: StatSpec) -> None:
    print(f"  sesgo esperado - real: {preds['lambda'].mean() - preds['total'].mean():+.2f}")
    for line in spec.lines:
        raw = preds[f"p_{line}"].to_numpy()
        y = preds[f"y_{line}"].to_numpy()
        cal = calibrators[line].predict(raw)
        brier = lambda p: float(np.mean((p - y) ** 2))  # noqa: E731
        print(f"  --- Línea {line} (n={len(y)}, base over {y.mean():.3f}) ---")
        print(f"    Brier: crudo {brier(raw):.4f} | calibrado {brier(cal):.4f} | tasa base {brier(np.full_like(y, y.mean())):.4f}")
        for thr in (0.55, 0.65):
            sel = raw >= thr
            if sel.sum() >= 30:
                print(f"    Over  p_cruda>={thr:.2f}: n={int(sel.sum()):5d} crudo {raw[sel].mean():.3f}"
                      f" calibrado {cal[sel].mean():.3f} real {y[sel].mean():.3f}")
            sel = raw <= 1 - thr
            if sel.sum() >= 30:
                print(f"    Under p_cruda>={thr:.2f}: n={int(sel.sum()):5d} crudo {1 - raw[sel].mean():.3f}"
                      f" calibrado {1 - cal[sel].mean():.3f} real {1 - y[sel].mean():.3f}")


def calibrate(spec: StatSpec, fit_start: str, holdout_start: str, dry_run: bool, out_dir: Path | None) -> None:
    model = CountStatModel(spec, calibration_path=None)  # crudo: no calibrar sobre lo ya calibrado
    print(f"\n===== {spec.label} ({spec.market_prefix}) =====")
    print(f"Ajuste {fit_start} -> {holdout_start}; holdout {holdout_start} -> fin del CSV "
          f"({model.df['MatchDate'].max().date()})")
    fit = walk_forward_predictions(model, fit_start, holdout_start)
    holdout = walk_forward_predictions(model, holdout_start)
    print(f"Partidos: ajuste {len(fit)}, holdout {len(holdout)}")

    print("== Efecto en el HOLDOUT (calibrador ajustado solo con el periodo anterior) ==")
    _report(holdout, fit_calibrators(fit, spec), spec)

    if dry_run:
        return
    final = fit_calibrators(pd.concat([fit, holdout], ignore_index=True), spec)
    out = spec.calibration_path if out_dir is None else Path(out_dir) / spec.calibration_path.name
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(final, out)
    print(f"Calibrador final (ajustado con todo, n={len(fit) + len(holdout)}) guardado en {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stat", default="all", choices=["all", *SPECS_BY_KEY])
    parser.add_argument("--fit-start", default=FIT_START)
    parser.add_argument("--holdout-start", default=HOLDOUT_START)
    parser.add_argument("--dry-run", action="store_true", help="Solo reporta; no guarda el calibrador")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    specs = ALL_SPECS if args.stat == "all" else (SPECS_BY_KEY[args.stat],)
    for spec in specs:
        calibrate(spec, args.fit_start, args.holdout_start, args.dry_run, args.out_dir)


if __name__ == "__main__":
    main()
