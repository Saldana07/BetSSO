"""Ajusta y valida el ajuste por desequilibrio del mercado 1X2 (ver
ml/features/market_adjustment.py) para cada estadística de conteo.

Sin fuga: el total esperado de cada partido usa solo partidos anteriores; los
coeficientes se ajustan (máxima verosimilitud Poisson con λ como offset) en
[FIT_START, HOLDOUT_START) y se evalúan en [HOLDOUT_START, fin). Se compara el
Brier medio de las 3 líneas centrales por partido con y sin ajuste; solo se
activa si t <= -2. El ajuste activado se reajusta con todo el periodo.

Uso:
    python -m ml.training.fit_market_adjustment
"""
import math

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ml.features.count_stats import ALL_SPECS, CountStatModel, over_under_probabilities
from ml.features.market_adjustment import GAP_CENTER, MARKET_ADJUSTMENT_PATH

FIT_START = pd.Timestamp("2024-09-01")
HOLDOUT_START = pd.Timestamp("2025-09-01")
T_REQUIRED = -2.0


def _frame(spec) -> pd.DataFrame:
    model = CountStatModel(spec, calibration_path=None)
    df = model.df[(model.df["MatchDate"] >= FIT_START)].dropna(subset=["p_home", "p_away"]).copy()
    lam = []
    for r in df.itertuples():
        e = model.expected_values(r.HomeTeam, r.AwayTeam, r.MatchDate)
        lam.append(sum(e) if e else np.nan)
    df["lam"] = lam
    df["total"] = df["home_value"] + df["away_value"]
    df["gap"] = (df["p_home"] - df["p_away"]).abs() - GAP_CENTER
    return df.dropna(subset=["lam"])


def _fit(d: pd.DataFrame) -> tuple[float, float]:
    def nll(b):
        mu = d["lam"] * np.exp(b[0] + b[1] * d["gap"])
        return float(np.mean(mu - d["total"] * np.log(mu)))
    b = minimize(nll, [0.0, 0.0]).x
    return float(b[0]), float(b[1])


def _central_lines(spec) -> tuple[float, ...]:
    i = len(spec.lines) // 2
    return tuple(spec.lines[max(0, i - 1): i + 2])


def main():
    params = {}
    for spec in ALL_SPECS:
        df = _frame(spec)
        fit, test = df[df["MatchDate"] < HOLDOUT_START], df[df["MatchDate"] >= HOLDOUT_START]
        b0, b1 = _fit(fit)
        lines = _central_lines(spec)
        diffs = []
        for r in test.itertuples():
            adj = r.lam * math.exp(b0 + b1 * r.gap)
            p0, p1 = over_under_probabilities(r.lam, spec), over_under_probabilities(adj, spec)
            d = np.mean([(p1[f"{spec.market(l)}_OVER"] - (r.total > l)) ** 2 - (p0[f"{spec.market(l)}_OVER"] - (r.total > l)) ** 2
                         for l in lines])
            diffs.append(d)
        diffs = np.array(diffs)
        t = float(diffs.mean() / (diffs.std(ddof=1) / math.sqrt(len(diffs))))
        validated = t <= T_REQUIRED
        if validated:
            b0, b1 = _fit(df)   # reajuste con todo el periodo
        params[spec.key] = {"intercept": b0, "coef_gap": b1, "validated": validated,
                            "report": {"n_test": int(len(test)), "delta_brier": float(diffs.mean()), "t_stat": round(t, 2)}}
        print(f"{spec.label:15s} coef desequilibrio {b1:+.2f} | prueba {len(test)} partidos: "
              f"Brier {diffs.mean():+.4f}, t = {t:+.1f} -> {'ACTIVADO' if validated else 'no se usa'}")
    joblib.dump(params, MARKET_ADJUSTMENT_PATH)
    print(f"Guardado en {MARKET_ADJUSTMENT_PATH}")


if __name__ == "__main__":
    main()
