"""Backtesting obligatorio: simula apostar según la estrategia de EV/Kelly
sobre temporadas pasadas (fuera del set de entrenamiento) y calcula yield/ROI.

Ningún modelo pasa a producción sin superar este backtest con yield positivo
y un mínimo de volumen de apuestas (para que el resultado sea estadísticamente
significativo y no un artefacto de varianza en pocas muestras).
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ml.value_betting.ev_kelly import evaluate_bet, is_safe_bet_candidate


@dataclass
class BacktestConfig:
    odds_min: float = 1.50
    odds_max: float = 2.00
    min_ev: float = 0.0
    starting_bankroll: float = 100.0  # capital inicial normalizado
    stake_unit: float = 1.0  # 1% del bankroll inicial por apuesta (staking plano) — nunca igual al bankroll total
    kelly_fraction_cap: float = 0.25
    min_bets_for_significance: int = 100


@dataclass
class BacktestResult:
    n_bets: int
    accuracy: float
    # Tasa de acierto que obtendría un apostador SIN habilidad tomando exactamente
    # las mismas cuotas: el promedio de la probabilidad implícita de las apuestas
    # colocadas. accuracy - base_accuracy = habilidad real, comparable entre
    # mercados (ver ml/value_betting/confidence.py).
    base_accuracy: float
    yield_pct: float
    roi_pct: float
    max_drawdown_pct: float
    equity_curve: list[float] = field(default_factory=list)


def run_backtest(
    predictions_df: pd.DataFrame,
    config: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    """
    predictions_df debe tener, por fila (una predicción de mercado ya generada
    por el modelo sobre datos históricos, ordenada por fecha):
      - model_probability, bookmaker_odds, actual_result (1 si ganó la selección, 0 si no)
    """
    bets = []
    equity = config.starting_bankroll
    equity_curve = [equity]
    peak = equity
    max_drawdown = 0.0
    wins = 0

    for _, row in predictions_df.iterrows():
        bet = evaluate_bet(row["model_probability"], row["bookmaker_odds"], config.kelly_fraction_cap)

        if not is_safe_bet_candidate(
            odds=row["bookmaker_odds"],
            expected_val=bet.expected_value,
            historical_accuracy=None,  # el backtest ES el que produce la accuracy histórica
            odds_min=config.odds_min,
            odds_max=config.odds_max,
            min_ev=config.min_ev,
        ):
            continue

        stake = config.stake_unit  # stake plano para medir yield; usar bet.kelly_fraction para simular bankroll real
        won = bool(row["actual_result"])
        pnl = stake * (row["bookmaker_odds"] - 1.0) if won else -stake

        equity += pnl
        equity_curve.append(equity)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)

        wins += int(won)
        bets.append({"stake": stake, "pnl": pnl, "won": won, "implied": bet.implied_probability})

    n_bets = len(bets)
    if n_bets == 0:
        return BacktestResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, equity_curve)

    total_staked = sum(b["stake"] for b in bets)
    total_pnl = sum(b["pnl"] for b in bets)

    return BacktestResult(
        n_bets=n_bets,
        accuracy=wins / n_bets,
        base_accuracy=sum(b["implied"] for b in bets) / n_bets,
        yield_pct=(total_pnl / total_staked) * 100 if total_staked else 0.0,
        roi_pct=(total_pnl / total_staked) * 100 if total_staked else 0.0,
        max_drawdown_pct=max_drawdown * 100,
        equity_curve=equity_curve,
    )


def passes_production_gate(result: BacktestResult, config: BacktestConfig = BacktestConfig()) -> tuple[bool, str]:
    """Gate obligatorio antes de desplegar un modelo a producción."""
    if result.n_bets < config.min_bets_for_significance:
        return False, f"Volumen insuficiente: {result.n_bets} < {config.min_bets_for_significance} apuestas"
    if result.yield_pct <= 0:
        return False, f"Yield no positivo: {result.yield_pct:.2f}%"
    if result.max_drawdown_pct > 30:
        return False, f"Drawdown máximo excesivo: {result.max_drawdown_pct:.2f}%"
    return True, "OK"
