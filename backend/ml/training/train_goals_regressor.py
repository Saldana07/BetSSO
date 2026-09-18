"""Entrenamiento de regresores LightGBM para goles esperados (home/away),
usados como complemento/blend del modelo de Poisson puro en poisson_xg.py.

Se entrenan dos regresores independientes (home_goals, away_goals) sobre
las mismas features, con validación temporal igual que el clasificador.
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_poisson_deviance
from sklearn.model_selection import TimeSeriesSplit

from ml.training.train_classifier import DATE_COLUMN, FEATURE_COLUMNS, HOLDOUT_SPLIT, load_dataset


def train_goals_model(df: pd.DataFrame, target_col: str, n_splits: int = 5) -> tuple[LGBMRegressor, list[float]]:
    X = df[FEATURE_COLUMNS].values
    y = df[target_col].values

    tscv = TimeSeriesSplit(n_splits=n_splits)
    deviances = []
    model = None

    for train_idx, val_idx in tscv.split(X):
        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        model = LGBMRegressor(
            objective="poisson",         # asume conteo de goles ~ Poisson
            n_estimators=400,
            max_depth=5,
            num_leaves=16,               # bajo, para limitar complejidad/overfitting
            learning_rate=0.02,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.3,
            reg_lambda=1.0,
            min_child_samples=30,
            verbose=-1,
            random_state=42,
        )
        model.fit(X_train, y_train, eval_X=X_val, eval_y=y_val)
        preds = np.clip(model.predict(X_val), 1e-3, None)
        deviances.append(mean_poisson_deviance(y_val, preds))

    return model, deviances


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--output-dir", default="models")
    parser.add_argument("--model-version", default="v1")
    args = parser.parse_args()

    df = load_dataset(args.features)
    # Mismo holdout que el clasificador, fuera del CV: el backtest se evalúa
    # sobre ese tramo y el early stopping del último fold no debe haberlo visto.
    df = df.iloc[: int(len(df) * HOLDOUT_SPLIT)]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for target, label in [("home_goals", "home"), ("away_goals", "away")]:
        model, deviances = train_goals_model(df, target)
        joblib.dump(model, output_dir / f"goals_{label}_{args.model_version}.joblib")
        print(f"[{label}] Poisson deviance por fold: {deviances}")


if __name__ == "__main__":
    main()
