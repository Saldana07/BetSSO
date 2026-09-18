"""Recorre los partidos históricos (FINISHED, con marcador) en orden
cronológico por liga y materializa el parquet de features que consumen
train_classifier.py / train_goals_regressor.py.

Crítico contra el overfitting/leakage: todas las features de un partido se
calculan con el ESTADO DE LOS EQUIPOS ANTES de jugarse ese partido (Elo,
forma, promedios de goles); el resultado del partido actualiza el estado
DESPUÉS de haber registrado la fila. Esa lógica vive en
ml/features/replay.py, compartida con el backtest sobre CSV externo, para que
ninguna fuente compute features distintas a las del entrenamiento.

Simplificaciones explícitas (dataset histórico de FiveThirtyEight, sin datos
de lesiones): `home_injuries_impact` / `away_injuries_impact` quedan en 0
para todas las filas.

Uso:
    python -m ml.training.build_dataset --output data/processed/training_features.parquet
"""
import argparse
import os

import pandas as pd
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Match
from ml.features.replay import (  # noqa: F401 — re-exportados: ml/serving/predict.py los importa de aquí
    DEFAULT_ELO,
    FALLBACK_LEAGUE_AVG_GOALS,
    FORM_HISTORY_WINDOW,
    LEAGUE_PRIOR_WEIGHT,
    REST_DAYS_CAP,
    REST_DAYS_DEFAULT,
    ROLLING_GOALS_WINDOW,
    FeatureReplayer,
    MatchRecord,
)


def _result_label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "H"
    if home_goals < away_goals:
        return "A"
    return "D"


def build_dataset() -> pd.DataFrame:
    db = SessionLocal()
    try:
        matches = db.execute(
            select(Match)
            .where(Match.status == "FINISHED", Match.home_goals.isnot(None), Match.away_goals.isnot(None))
            # Orden cronológico GLOBAL, no por liga: el estado (Elo, forma,
            # goles) es por equipo y viaja entre competiciones. Ordenar por
            # liga primero hacía que un equipo llegara a su partido de copa
            # de 2017 con el Elo de su liga de 2023 (fuga del futuro).
            # Mismo orden que compute_pre_match_features en predict.py.
            .order_by(Match.kickoff_utc, Match.id)
        ).scalars().all()
    finally:
        db.close()

    replayer = FeatureReplayer()
    rows = []

    for match in matches:
        record = MatchRecord(
            league_id=match.league_id,
            home_id=match.home_team_id,
            away_id=match.away_team_id,
            kickoff=match.kickoff_utc,
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            home_xg=match.home_xg,
            away_xg=match.away_xg,
        )
        feats = replayer.features(record)
        rows.append({
            "match_date": match.kickoff_utc,
            "league_id": match.league_id,
            "season": match.season,
            **{k: v for k, v in feats.items() if not k.startswith("_")},
            "result": _result_label(match.home_goals, match.away_goals),
            "home_goals": match.home_goals,
            "away_goals": match.away_goals,
            # Probabilidad de un modelo externo (ej. FiveThirtyEight SPI) para el
            # MISMO partido, cuando la fuente la trae. Nunca se usa como feature
            # de entrenamiento (sería fuga de un modelo ajeno); solo sirve para
            # simular una "cuota de mercado" en el backtest histórico cuando no
            # hay cuotas reales disponibles (ver ml/backtesting/run_historical_backtest.py).
            "ext_prob_home": float(match.ext_prob_home) if match.ext_prob_home is not None else None,
            "ext_prob_draw": float(match.ext_prob_draw) if match.ext_prob_draw is not None else None,
            "ext_prob_away": float(match.ext_prob_away) if match.ext_prob_away is not None else None,
        })
        replayer.update(record)

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    df = build_dataset()
    df = df.sort_values("match_date").reset_index(drop=True)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    df.to_parquet(args.output, index=False)

    print(f"Dataset generado: {len(df)} filas -> {args.output}")
    print(df["result"].value_counts(normalize=True))


if __name__ == "__main__":
    main()
