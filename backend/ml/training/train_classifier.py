"""Entrenamiento del modelo de clasificación 1X2 con XGBoost + calibración.

Uso típico:
    python -m ml.training.train_classifier --features data/features.parquet

El dataset de entrada debe tener, como mínimo, las columnas de features
(Elo, Poisson/xG, forma ponderada) más la columna objetivo `result`
('H','D','A') y una columna `match_date` para el split temporal.
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

FEATURE_COLUMNS = [
    "elo_diff",
    "home_win_prob_elo",
    "poisson_home_win_prob",
    "poisson_draw_prob",
    "poisson_away_win_prob",
    "home_weighted_points_pct",
    "away_weighted_points_pct",
    "form_points_pct_diff",
    "form_xg_diff_delta",
    # home/away_injuries_impact quedan fuera: el plan gratuito de la API no da
    # lesiones y eran 0 en el 100% del dataset. Un árbol nunca parte por una
    # constante, así que no metían ruido, pero tampoco aportaban nada. Se
    # reincorporan cuando haya datos reales (features() ya las calcula).
    "home_rest_days",
    "away_rest_days",
    "rest_days_diff",
]
TARGET_COLUMN = "result"
DATE_COLUMN = "match_date"


HOLDOUT_SPLIT = 0.85  # fracción cronológica de entrenamiento; el resto es holdout (calibración + backtest)


def load_dataset(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df = df.sort_values(DATE_COLUMN).reset_index(drop=True)
    return df


def time_series_cv_train(df: pd.DataFrame, n_splits: int = 5) -> tuple[XGBClassifier, LabelEncoder, dict]:
    """Entrena con validación temporal (walk-forward), NUNCA k-fold aleatorio.

    Esto evita fuga de información del futuro hacia el pasado, algo crítico
    en series temporales de partidos donde la forma evoluciona con el tiempo.
    """
    X = df[FEATURE_COLUMNS].values
    encoder = LabelEncoder()
    y = encoder.fit_transform(df[TARGET_COLUMN].values)

    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_metrics = []
    model = None

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_train, X_val = X[train_idx], X[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        model = XGBClassifier(
            n_estimators=300,
            max_depth=4,               # profundidad baja: reduce sobreajuste en datos ruidosos
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.5,
            reg_lambda=1.5,
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            early_stopping_rounds=30,
            random_state=42,           # reproducible: sin semilla, dos corridas iguales daban +0.1 y +2.8 pts de "habilidad"
        )
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

        preds_proba = model.predict_proba(X_val)
        preds = np.argmax(preds_proba, axis=1)

        fold_metrics.append({
            "fold": fold,
            "accuracy": accuracy_score(y_val, preds),
            "log_loss": log_loss(y_val, preds_proba, labels=[0, 1, 2]),
        })

    return model, encoder, {"cv_folds": fold_metrics}


def calibrate_probabilities(model: XGBClassifier, X_val: np.ndarray, y_val: np.ndarray) -> CalibratedClassifierCV:
    """Calibración isotónica: las probabilidades crudas de XGBoost no siempre
    son fiables como probabilidades reales; esto es esencial para que el
    cálculo de EV/Kelly (que depende de probabilidades bien calibradas) sea válido.

    `cv="prefit"` (el patrón clásico para calibrar un modelo ya entrenado sin
    reentrenarlo) fue removido en versiones recientes de scikit-learn;
    `FrozenEstimator` es el reemplazo oficial desde sklearn 1.6.
    """
    calibrated = CalibratedClassifierCV(FrozenEstimator(model), method="isotonic")
    calibrated.fit(X_val, y_val)
    return calibrated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True, help="Ruta al parquet de features")
    parser.add_argument("--output-dir", default="models")
    parser.add_argument("--model-version", default="v1")
    args = parser.parse_args()

    df = load_dataset(args.features)

    # Reserva el último 15% cronológico como holdout final para calibración +
    # backtest ANTES del CV. Si el CV viera todo el dataset, su último fold
    # usaría ese mismo tramo para el early stopping y la calibración se haría
    # sobre datos que ya moldearon el modelo: probabilidades optimistas.
    split_idx = int(len(df) * HOLDOUT_SPLIT)
    train_df, holdout = df.iloc[:split_idx], df.iloc[split_idx:]
    model, encoder, metrics = time_series_cv_train(train_df)
    X_holdout = holdout[FEATURE_COLUMNS].values
    y_holdout = encoder.transform(holdout[TARGET_COLUMN].values)

    calibrated_model = calibrate_probabilities(model, X_holdout, y_holdout)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(calibrated_model, output_dir / f"classifier_{args.model_version}.joblib")
    joblib.dump(encoder, output_dir / f"label_encoder_{args.model_version}.joblib")

    with open(output_dir / f"metrics_{args.model_version}.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"Modelo guardado en {output_dir}. CV folds: {metrics['cv_folds']}")


if __name__ == "__main__":
    main()
