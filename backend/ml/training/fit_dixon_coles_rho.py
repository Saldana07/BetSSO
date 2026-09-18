"""Ajusta rho de Dixon-Coles por máxima verosimilitud y lo verifica en holdout.

Para cada partido del dataset, lambda_home/lambda_away salen del regresor de
goles entrenado; la verosimilitud del marcador real bajo la matriz
Poisson x tau(rho) se maximiza sobre una rejilla de rho en el tramo de
entrenamiento y se reporta también en el holdout (que el regresor no vio).
Si el mejor rho del holdout no mejora a rho=0, la corrección no ayuda y
DIXON_COLES_RHO debe quedarse en 0.

Uso:
    python -m ml.training.fit_dixon_coles_rho --features data/processed/training_features.parquet
"""
import argparse

import joblib
import numpy as np

from ml.features.poisson_xg import MAX_GOALS, scoreline_matrix
from ml.serving.predict import DEFAULT_MODEL_DIR
from ml.training.train_classifier import FEATURE_COLUMNS, HOLDOUT_SPLIT, load_dataset

GRID = np.round(np.arange(-0.30, 0.11, 0.01), 2)


def log_likelihood(lh: np.ndarray, la: np.ndarray, hg: np.ndarray, ag: np.ndarray, rho: float) -> float:
    ll = 0.0
    for h, a, x, y in zip(lh, la, hg, ag):
        x, y = min(int(x), MAX_GOALS), min(int(y), MAX_GOALS)
        ll += np.log(scoreline_matrix(float(h), float(a), rho=rho)[x, y])
    return ll / len(lh)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--model-version", default="v1")
    args = parser.parse_args()

    df = load_dataset(args.features)
    home_model = joblib.load(f"{args.model_dir}/goals_home_{args.model_version}.joblib")
    away_model = joblib.load(f"{args.model_dir}/goals_away_{args.model_version}.joblib")
    X = df[FEATURE_COLUMNS].values
    lh = np.clip(home_model.predict(X), 0.05, None)
    la = np.clip(away_model.predict(X), 0.05, None)
    hg, ag = df["home_goals"].values, df["away_goals"].values

    split = int(len(df) * HOLDOUT_SPLIT)
    tr, ho = slice(0, split), slice(split, None)

    print(f"Partidos: entrenamiento {split}, holdout {len(df) - split}")
    ll_tr = {rho: log_likelihood(lh[tr], la[tr], hg[tr], ag[tr], rho) for rho in GRID}
    best = max(ll_tr, key=ll_tr.get)
    print(f"rho óptimo (entrenamiento): {best:+.2f}   LL/partido {ll_tr[best]:.5f}  vs rho=0: {ll_tr[0.0]:.5f}")

    for rho in (0.0, best, -0.10, -0.13, -0.15):
        print(f"  holdout rho={rho:+.2f}: LL/partido {log_likelihood(lh[ho], la[ho], hg[ho], ag[ho], rho):.5f}")

    # Frecuencias reales vs modelo en las 4 celdas afectadas (holdout).
    hgh, agh = hg[ho], ag[ho]
    for rho in (0.0, best):
        pred = np.zeros(4)
        for h, a in zip(lh[ho], la[ho]):
            m = scoreline_matrix(float(h), float(a), rho=rho)
            pred += [m[0, 0], m[1, 0], m[0, 1], m[1, 1]]
        pred /= len(hgh)
        real = [np.mean((hgh == 0) & (agh == 0)), np.mean((hgh == 1) & (agh == 0)),
                np.mean((hgh == 0) & (agh == 1)), np.mean((hgh == 1) & (agh == 1))]
        print(f"  rho={rho:+.2f}  0-0 {pred[0]:.4f} (real {real[0]:.4f})  1-0 {pred[1]:.4f} ({real[1]:.4f})"
              f"  0-1 {pred[2]:.4f} ({real[2]:.4f})  1-1 {pred[3]:.4f} ({real[3]:.4f})")


if __name__ == "__main__":
    main()
