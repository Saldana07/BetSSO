"""Simulación de Montecarlo de un partido con incertidumbre sobre la fuerza
de los equipos.

Por qué NO es una Poisson simple muestreada 10,000 veces: eso daría, salvo
ruido de muestreo (±0.5%), exactamente las mismas probabilidades que ya
calcula market_probabilities() de forma analítica. Comparar eso con el
modelo base y llamar "valor" a la diferencia sería una ilusión estadística.

Lo que sí aporta esta simulación:
  - Incertidumbre de parámetros. Los goles esperados (λ) son estimaciones
    con error; con poco historial, mucho error. Cada corrida muestrea λ de
    una Gamma centrada en la estimación cuya dispersión decrece con el
    historial (mezcla Gamma-Poisson = Binomial Negativa, el modelo estándar
    de goles sobredispersos). Resultado: distribuciones más anchas y más
    honestas cuanto menos sabemos.
  - Intervalos de confianza sobre P(local/empate/visitante), que la fórmula
    cerrada no da.
  - Distribución completa de marcadores y de goles totales.

Cómo leer la comparación con otros modelos (ver compare_with):
  - Montecarlo vs. clasificador XGBoost: dos modelos DISTINTOS. Que
    coincidan da confianza; que discrepen señala incertidumbre. No es valor.
  - Modelos vs. MERCADO: ahí y solo ahí vive la "apuesta de valor".
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import poisson

N_SIMULATIONS = 10_000
MAX_GOALS_TRACKED = 7  # marcadores por encima se agrupan como "7+"
# Confianza en λ: shape k de la Gamma = historial efectivo (acotado). CV = 1/sqrt(k):
# con 10 partidos ±32%, con 50 ±14%. Refleja que un λ estimado con poca
# evidencia es mucho menos fiable.
MIN_EFFECTIVE_HISTORY = 5
MAX_EFFECTIVE_HISTORY = 50


@dataclass
class SimulationResult:
    n_simulations: int
    p_home: float
    p_draw: float
    p_away: float
    # Intervalos al 90% que reflejan la incertidumbre sobre la FUERZA de los
    # equipos: para cada λ muestreado se calcula P(resultado | λ) y se toman
    # los percentiles 5-95 de esa distribución. Se estrechan con más historial.
    # (Un bootstrap de los resultados simulados solo mediría el ruido de
    # muestreo de Montecarlo, ~0.5%, que no es lo que interesa.)
    ci_home: tuple[float, float]
    ci_draw: tuple[float, float]
    ci_away: tuple[float, float]
    expected_home_goals: float
    expected_away_goals: float
    total_goals_distribution: dict[int, float] = field(default_factory=dict)   # {0: 0.07, 1: 0.19, ...}
    top_scorelines: list[tuple[str, float]] = field(default_factory=list)      # [("1-1", 0.12), ...]
    p_over_2_5: float = 0.0
    p_btts: float = 0.0


def simulate_match(
    lambda_home: float,
    lambda_away: float,
    home_history: int,
    away_history: int,
    n_simulations: int = N_SIMULATIONS,
    seed: int | None = None,
) -> SimulationResult:
    rng = np.random.default_rng(seed)

    k_home = float(np.clip(home_history, MIN_EFFECTIVE_HISTORY, MAX_EFFECTIVE_HISTORY))
    k_away = float(np.clip(away_history, MIN_EFFECTIVE_HISTORY, MAX_EFFECTIVE_HISTORY))

    # λ ~ Gamma(shape=k, scale=λ̂/k): media λ̂, varianza λ̂²/k.
    lam_h = rng.gamma(shape=k_home, scale=lambda_home / k_home, size=n_simulations)
    lam_a = rng.gamma(shape=k_away, scale=lambda_away / k_away, size=n_simulations)
    goals_h = rng.poisson(lam_h)
    goals_a = rng.poisson(lam_a)

    home_wins = goals_h > goals_a
    draws = goals_h == goals_a
    away_wins = goals_h < goals_a

    # P(resultado | λ) exacta para cada par de λ muestreado (matriz de marcadores
    # Poisson vectorizada). Su dispersión entre corridas ES la incertidumbre
    # epistémica sobre los equipos.
    ks = np.arange(MAX_GOALS_TRACKED + 4)
    pmf_h = poisson.pmf(ks[None, :], lam_h[:, None])   # (n, K)
    pmf_a = poisson.pmf(ks[None, :], lam_a[:, None])
    joint = pmf_h[:, :, None] * pmf_a[:, None, :]       # (n, K, K): P(home=i, away=j | λ)
    lower = np.tril(np.ones((len(ks), len(ks))), -1)    # i > j -> gana local
    p_home_given_lam = (joint * lower).sum(axis=(1, 2))
    p_draw_given_lam = np.einsum("nii->n", joint)
    p_away_given_lam = (joint * lower.T).sum(axis=(1, 2))

    def ci(p_given_lam: np.ndarray) -> tuple[float, float]:
        return float(np.percentile(p_given_lam, 5)), float(np.percentile(p_given_lam, 95))

    totals = goals_h + goals_a
    capped = np.minimum(totals, MAX_GOALS_TRACKED)
    total_dist = {int(g): float((capped == g).mean()) for g in range(MAX_GOALS_TRACKED + 1)}

    score_keys = np.minimum(goals_h, MAX_GOALS_TRACKED) * 100 + np.minimum(goals_a, MAX_GOALS_TRACKED)
    uniq, counts = np.unique(score_keys, return_counts=True)
    order = np.argsort(-counts)[:6]
    top = [(f"{int(uniq[i]) // 100}-{int(uniq[i]) % 100}", float(counts[i] / n_simulations)) for i in order]

    return SimulationResult(
        n_simulations=n_simulations,
        p_home=float(home_wins.mean()),
        p_draw=float(draws.mean()),
        p_away=float(away_wins.mean()),
        ci_home=ci(p_home_given_lam),
        ci_draw=ci(p_draw_given_lam),
        ci_away=ci(p_away_given_lam),
        expected_home_goals=float(goals_h.mean()),
        expected_away_goals=float(goals_a.mean()),
        total_goals_distribution=total_dist,
        top_scorelines=top,
        p_over_2_5=float((totals > 2.5).mean()),
        p_btts=float(((goals_h > 0) & (goals_a > 0)).mean()),
    )


@dataclass
class Comparison:
    """Tres estimaciones de P(local/empate/visitante) y la conclusión honesta."""
    monte_carlo: dict[str, float]
    classifier: dict[str, float]
    market_fair: dict[str, float] | None       # prob. implícita sin margen; None si no hay cuotas
    model_disagreement: float                   # máx |MC - clasificador| entre los 3 resultados
    value_candidates: list[dict]                # resultados donde AMBOS modelos superan al mercado
    conclusion: str


# Umbrales (puntos de probabilidad). Un desacuerdo entre modelos por encima de
# DISAGREEMENT_HIGH invalida cualquier "valor": no sabemos cuál tiene razón.
DISAGREEMENT_HIGH = 0.08
VALUE_MARGIN = 0.03


def compare_with(sim: SimulationResult, classifier: dict[str, float], market_odds: dict[str, float] | None) -> Comparison:
    mc = {"HOME": sim.p_home, "DRAW": sim.p_draw, "AWAY": sim.p_away}
    disagreement = max(abs(mc[k] - classifier[k]) for k in mc)

    market_fair = None
    if market_odds and all(k in market_odds for k in mc):
        raw = {k: 1.0 / market_odds[k] for k in mc}
        overround = sum(raw.values())
        market_fair = {k: v / overround for k, v in raw.items()}  # quita el margen de la casa

    value_candidates = []
    if market_fair is not None and disagreement <= DISAGREEMENT_HIGH:
        for k in mc:
            edge_mc = mc[k] - market_fair[k]
            edge_clf = classifier[k] - market_fair[k]
            if edge_mc >= VALUE_MARGIN and edge_clf >= VALUE_MARGIN:
                value_candidates.append({
                    "outcome": k,
                    "market_fair": market_fair[k],
                    "monte_carlo": mc[k],
                    "classifier": classifier[k],
                    "edge_min": min(edge_mc, edge_clf),
                })

    labels = {"HOME": "victoria local", "DRAW": "empate", "AWAY": "victoria visitante"}
    if disagreement > DISAGREEMENT_HIGH:
        worst = max(mc, key=lambda k: abs(mc[k] - classifier[k]))
        conclusion = (
            f"Los dos modelos discrepan {disagreement * 100:.0f} puntos en {labels[worst]} "
            f"(Montecarlo {mc[worst] * 100:.0f}% vs. clasificador {classifier[worst] * 100:.0f}%). "
            "Cuando nuestros propios modelos no coinciden, la incertidumbre es alta y no hay base "
            "para hablar de apuesta de valor, aunque uno de ellos supere al mercado."
        )
    elif market_fair is None:
        conclusion = (
            f"Los modelos coinciden (desacuerdo máximo {disagreement * 100:.0f} pts), pero no hay cuotas "
            "1X2 para este partido, así que no se puede evaluar valor contra el mercado."
        )
    elif value_candidates:
        best = max(value_candidates, key=lambda c: c["edge_min"])
        conclusion = (
            f"Ambos modelos superan al mercado en {labels[best['outcome']]}: mercado {best['market_fair'] * 100:.0f}% "
            f"(sin margen), Montecarlo {best['monte_carlo'] * 100:.0f}%, clasificador {best['classifier'] * 100:.0f}%. "
            f"Ventaja mínima de {best['edge_min'] * 100:.1f} pts con los modelos de acuerdo. "
            # Sin cifras fijas: un número de habilidad escrito aquí caduca en
            # silencio (el "+1.8 pts" de antes dejó de ser cierto al corregir la
            # fuga de la calibración). La evidencia vive en "Modelo vs casa".
            "Pero en 1X2 el modelo no le gana a la casa (backtest con cuotas reales y tabla "
            "\"Modelo vs casa\"): esta discrepancia es más probablemente error del modelo que valor. "
            "Por eso el 1X2 no genera picks."
        )
    else:
        conclusion = (
            f"Los modelos coinciden entre sí (desacuerdo {disagreement * 100:.0f} pts) y también con el mercado: "
            "ninguna de las tres salidas ofrece una ventaja de al menos 3 pts respaldada por ambos modelos. "
            "Sin valor identificable en el 1X2 de este partido."
        )

    return Comparison(mc, classifier, market_fair, disagreement, value_candidates, conclusion)
