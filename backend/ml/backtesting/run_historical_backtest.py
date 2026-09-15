"""Backtest de nuestro modelo entrenado usando el histórico de FiveThirtyEight.

*** ADVERTENCIA METODOLÓGICA IMPORTANTE ***
Este dataset NO trae cuotas reales de casas de apuestas. Trae, en cambio,
las probabilidades del PROPIO modelo de 538 (prob1/prob2/probtie), guardadas
en `matches.ext_prob_*` durante la importación. Usamos esas probabilidades
como sustituto de "opinión de mercado": se convierten en una cuota simulada
aplicándoles un margen (overround) típico de casa de apuestas (~6%), y
comparamos la probabilidad de NUESTRO modelo contra esa cuota simulada.

Importante: la cuota simulada se construye a partir de la probabilidad de
538, NUNCA de la probabilidad de nuestro propio modelo — si se usara la
probabilidad de nuestro propio modelo para construir su propia "cuota",
el EV sería negativo por construcción (exactamente `1/overround - 1`,
sin ningún desacuerdo real que explotar). Con esta comparación sí puede
haber +EV genuino, exactamente cuando nuestro modelo predice una
probabilidad de victoria local sensiblemente mayor a la que asignaba 538
para ese mismo partido.

Esto SOLO valida que la mecánica del pipeline (features -> modelo calibrado
-> comparación de probabilidad vs. cuota -> EV -> Kelly -> gate de
backtest) funciona correctamente de punta a punta, y que tan a menudo nuestro
modelo "le gana" al de 538. NO es una prueba de que la estrategia sea
rentable contra cuotas reales de mercado — el resultado de este script debe
leerse como "el modelo bate al modelo de 538 antes de comisión" en el mejor
de los casos, nunca como ROI esperado en producción. El backtest real contra
cuotas de mercado requiere datos de `ml/ingestion/sync_odds.py` acumulados
con el tiempo.

Uso:
    python -m ml.backtesting.run_historical_backtest --features data/processed/training_features.parquet --model-dir models
"""
import argparse

import joblib
import numpy as np
import pandas as pd

from ml.backtesting.backtest import BacktestConfig, passes_production_gate, run_backtest
from ml.training.train_classifier import DATE_COLUMN, FEATURE_COLUMNS, TARGET_COLUMN

OVERROUND = 1.06  # margen típico de casa de apuestas (~6%), aplicado sobre las prob. de 538


def simulate_bookmaker_odds(reference_probability: float, overround: float = OVERROUND) -> float:
    """Cuota simulada a partir de la probabilidad de un modelo EXTERNO (538) = 1 / (prob * overround).
    Nunca usar en producción real ni con la probabilidad del propio modelo evaluado."""
    reference_probability = max(reference_probability, 1e-6)
    return 1.0 / (reference_probability * overround)


# Mercados que SÍ se pueden derivar de las probabilidades 1X2 de 538
# (prob1/probtie/prob2): 1X2 puro y Doble Oportunidad. OU_1.5/2.5 y BTTS
# necesitarían una probabilidad de referencia de líneas de goles que 538 no
# provee — esos mercados quedan sin backtest hasta acumular cuotas reales
# vía ml/ingestion/sync_odds.py.
MARKET_SPECS = {
    "1X2": {
        "our_prob": lambda p_home, p_draw, p_away: p_home,
        "ref_prob": lambda row: row["ext_prob_home"],
        "actual": lambda row: 1 if row[TARGET_COLUMN] == "H" else 0,
    },
    "DOUBLE_CHANCE_1X": {
        "our_prob": lambda p_home, p_draw, p_away: p_home + p_draw,
        "ref_prob": lambda row: row["ext_prob_home"] + row["ext_prob_draw"],
        "actual": lambda row: 1 if row[TARGET_COLUMN] in ("H", "D") else 0,
    },
}


def build_backtest_frame(features_path: str, model_dir: str, model_version: str, market: str) -> pd.DataFrame:
    spec = MARKET_SPECS[market]

    df = pd.read_parquet(features_path).sort_values(DATE_COLUMN).reset_index(drop=True)
    df = df.dropna(subset=["ext_prob_home", "ext_prob_draw", "ext_prob_away"]).reset_index(drop=True)

    # Igual que en el entrenamiento: el último 15% cronológico es holdout,
    # así que el backtest corre exclusivamente sobre datos que el modelo
    # jamás vio durante el entrenamiento ni la calibración.
    split_idx = int(len(df) * 0.85)
    holdout = df.iloc[split_idx:].reset_index(drop=True)

    model = joblib.load(f"{model_dir}/classifier_{model_version}.joblib")
    encoder = joblib.load(f"{model_dir}/label_encoder_{model_version}.joblib")

    X_holdout = holdout[FEATURE_COLUMNS].values
    proba = model.predict_proba(X_holdout)  # columnas en el orden de encoder.classes_
    class_index = {label: i for i, label in enumerate(encoder.classes_)}
    h, d, a = class_index["H"], class_index["D"], class_index["A"]

    rows = []
    for i, row in holdout.iterrows():
        p_home, p_draw, p_away = proba[i, h], proba[i, d], proba[i, a]
        model_prob = spec["our_prob"](p_home, p_draw, p_away)
        bookmaker_odds = simulate_bookmaker_odds(spec["ref_prob"](row))
        actual_result = spec["actual"](row)

        rows.append({
            "model_probability": model_prob,
            "bookmaker_odds": bookmaker_odds,
            "actual_result": actual_result,
        })

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--model-dir", default="models")
    parser.add_argument("--model-version", default="v1")
    parser.add_argument("--market", default="1X2", choices=list(MARKET_SPECS.keys()))
    args = parser.parse_args()

    print("*** SIMULACIÓN: cuotas derivadas de prob. de 538 + margen sintético, NO cuotas reales de mercado ***")
    print(f"*** Mercado: {args.market} ***\n")

    predictions_df = build_backtest_frame(args.features, args.model_dir, args.model_version, args.market)
    result = run_backtest(predictions_df, BacktestConfig())

    print(f"Apuestas evaluadas (dentro de cuota 1.50-2.00 y EV>0): {result.n_bets}")
    print(f"Accuracy: {result.accuracy:.3f}")
    print(f"Yield: {result.yield_pct:.2f}%")
    print(f"Drawdown máximo: {result.max_drawdown_pct:.2f}%")

    passed, reason = passes_production_gate(result)
    print(f"\nGate de producción: {'PASA' if passed else 'NO PASA'} — {reason}")


if __name__ == "__main__":
    main()
