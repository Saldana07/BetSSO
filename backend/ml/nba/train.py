"""Entrena y valida el modelo NBA contra las líneas de cierre reales.

Protocolo (nada se ajusta con los datos con los que se evalúa):
  1. Parámetros del modelo (ml/nba/ratings.py): se ajustan minimizando el
     error de margen y total en 2015-16 a 2020-21 (2014-15 solo calienta).
  2. Cuánto aporta el modelo sobre el mercado: en 2021-22 a 2023-24 se mide
     cuánto del desacuerdo modelo-vs-línea de cierre se cumple
     (resultado - línea = w · (modelo - línea)). w = 0: el modelo no sabe
     nada que la línea no sepa; w = 1: el modelo es mejor que la línea.
  3. Prueba final en 2024-25 y 2025-26, nunca vistas: ¿la mezcla con ese w
     predice mejor que la línea sola? ¿Apostar con ella gana dinero a las
     cuotas reales de cierre (y de apertura)?

Solo si el modelo aporta en (2) y lo confirma en (3), sus picks usan ese w;
si no, w = 0 y los picks solo nacen de diferencias de precio (una casa como
Betano pagando más que lo que el resto del mercado considera justo). Esa
estrategia también se prueba: en los partidos con 3+ casas en ESPN (sobre
todo 2021-22 y 2022-23), apostando donde una casa se aparta del consenso.

Uso:
    python -m ml.nba.train [--reusar-ajuste]
"""
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from app.database import SessionLocal
from ml.nba.dataset import closing_lines, load_games
from ml.nba.markets import MAX_LINE_GAP, blend, line_gap, market_expectation, probability, reference_probability
from ml.nba.ratings import Params, replay
from ml.value_betting.settlement import UnknownMarketError, bet_won

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "nba"
PARAMS_PATH = MODEL_DIR / "params.json"
VALIDATION_PATH = MODEL_DIR / "validation.json"

TUNE_SEASONS = ["2015-16", "2016-17", "2017-18", "2018-19", "2019-20", "2020-21"]
FIT_SEASONS = ["2021-22", "2022-23", "2023-24"]
TEST_SEASONS = ["2024-25", "2025-26"]

BOUNDS = {"k": (0.005, 0.12), "k_new": (0.0, 0.15), "k_pace": (0.005, 0.2), "carry": (0.2, 1.0),
          "home": (0.0, 5.0), "b2b": (0.0, 4.0), "k_league": (0.0, 0.02), "k_home": (0.0, 0.01)}
NAMES = list(BOUNDS)

# Mismo filtro que los picks de verdad (app/core/config.py).
ODDS_MIN, ODDS_MAX, MIN_EV, MAX_EV = 1.50, 2.00, 0.02, 0.20
MIN_BOOKS = 3   # para probar la compra de precio hace falta con quién comparar


def _params(x) -> Params:
    return Params(**{n: float(min(max(v, BOUNDS[n][0]), BOUNDS[n][1])) for n, v in zip(NAMES, x)})


def _joined(games: pd.DataFrame, preds: pd.DataFrame) -> pd.DataFrame:
    df = games.merge(preds, on="id")
    df = df[df["home_pts"].notna()].copy()
    df["actual_margin"] = df["home_pts"].astype(float) - df["away_pts"].astype(float)
    df["actual_total"] = df["home_pts"].astype(float) + df["away_pts"].astype(float)
    return df


def tune(games: pd.DataFrame) -> Params:
    sub = games[games["season"] <= TUNE_SEASONS[-1]]

    def loss(x):
        preds, _ = replay(sub, _params(x))
        df = _joined(sub, preds)
        df = df[df["season"].isin(TUNE_SEASONS)]
        em = df["actual_margin"] - df["margin"]
        et = df["actual_total"] - df["total"]
        return float((em ** 2).mean() / 144 + (et ** 2).mean() / 361)

    x0 = [getattr(Params(), n) for n in NAMES]
    res = minimize(loss, x0, method="Nelder-Mead", options={"maxiter": 350, "xatol": 1e-4, "fatol": 1e-6})
    print(f"  ajuste: pérdida {loss(x0):.4f} -> {res.fun:.4f} ({res.nit} iteraciones)")
    return _params(res.x)


def ols_weight(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """y = w·x sin constante; (w, error estándar)."""
    sxx = float((x ** 2).sum())
    if sxx == 0 or len(x) < 3:
        return 0.0, float("inf")
    w = float((x * y).sum() / sxx)
    resid = y - w * x
    se = math.sqrt(float((resid ** 2).sum()) / (len(x) - 1) / sxx)
    return w, se


def _paired_t(d: np.ndarray) -> float | None:
    if len(d) < 3 or d.std(ddof=1) == 0:
        return None
    return float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))


def simulate_bets(df: pd.DataFrame, lines: pd.DataFrame, snapshot: str, w_m: float, w_t: float,
                  sd_m: float, sd_t: float, price_mode: bool = False,
                  gap_range: tuple[float, float] | None = None) -> dict:
    """Apuesta plana de 1 unidad, como los picks: por partido y familia, la
    mejor apuesta con EV > 2% y cuota 1.50-2.00 entre las casas de ESPN.

    price_mode: la regla en vivo con w = 0: cada precio se compara con la
    probabilidad justa de las DEMÁS casas en esa línea (reference_probability).
    gap_range: solo hándicaps/totales cuya línea está a esa distancia (pts) de
    lo que espera el mercado; el ganador (ML) cuenta solo si el rango empieza en 0."""
    by_game = {r.id: r.markets for r in lines[lines["snapshot"] == snapshot].itertuples()}
    n = wins = 0
    profit = 0.0
    fam_stats: dict[str, list] = {}
    for r in df.itertuples():
        markets = by_game.get(r.id)
        if not markets:
            continue
        expect = market_expectation(markets, sd_m, sd_t)
        mu_m = blend(r.margin, expect["margin"], w_m)
        mu_t = blend(r.total, expect["total"], w_t)
        best: dict[str, tuple] = {}
        for market, books in markets.items():
            family = market.split("_")[0]
            if gap_range is not None:
                if family == "ML":
                    if gap_range[0] > 0:
                        continue
                else:
                    gap = line_gap(market, expect["margin"], expect["total"])
                    if gap is None or not gap_range[0] <= gap <= gap_range[1]:
                        continue
            for book, sels in books.items():
                for sel, odds in sels.items():
                    if not ODDS_MIN <= odds <= ODDS_MAX:
                        continue
                    p = (reference_probability(market, sel, books, mu_m, mu_t, sd_m, sd_t, exclude=book)
                         if price_mode else probability(market, sel, mu_m, mu_t, sd_m, sd_t))
                    if p is None:
                        continue
                    ev = p * odds - 1
                    if MIN_EV < ev <= MAX_EV and (family not in best or ev > best[family][0]):
                        best[family] = (ev, market, sel, odds)
        for family, (ev, market, sel, odds) in best.items():
            try:
                won = bet_won(market, sel, int(r.home_pts), int(r.away_pts))
            except UnknownMarketError:
                continue   # línea entera: con devolución, no se cuenta
            gain = (odds - 1) if won else -1
            n += 1
            wins += won
            profit += gain
            fs = fam_stats.setdefault(family, [0, 0, 0.0])
            fs[0] += 1
            fs[1] += won
            fs[2] += gain
    return {"bets": n, "wins": wins, "profit": round(profit, 2), "roi": round(profit / n, 4) if n else None,
            "by_family": {f: {"bets": b, "wins": w, "roi": round(pr / b, 4)} for f, (b, w, pr) in fam_stats.items()}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reusar-ajuste", action="store_true",
                        help="Usa los parámetros ya ajustados (models/nba/params.json) y solo repite la validación")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        games = load_games(db)
        print(f"NBA: {len(games)} partidos en la base de datos.")
        if args.reusar_ajuste and PARAMS_PATH.exists():
            params = Params(**json.loads(PARAMS_PATH.read_text(encoding="utf-8"))["params"])
            print("1) Parámetros ya ajustados con 2015-16 a 2020-21 (no se repite el ajuste).")
        else:
            print("1) Ajustando el modelo con 2015-16 a 2020-21...")
            params = tune(games)
        print("   ", {k: round(v, 4) for k, v in params.to_dict().items()})
        preds, _ = replay(games, params)
        df = _joined(games, preds)

        recent = df[df["season"].isin(TUNE_SEASONS[-3:])]
        sd_m = float((recent["actual_margin"] - recent["margin"]).std())
        sd_t = float((recent["actual_total"] - recent["total"]).std())

        lines = closing_lines(db, FIT_SEASONS + TEST_SEASONS)
    finally:
        db.close()

    close = lines[lines["snapshot"] == "CLOSE"][["id", "spread", "total", "ml_home"]].rename(
        columns={"spread": "c_spread", "total": "c_total", "ml_home": "c_ml"})
    h = df.merge(close, on="id", how="inner")
    h = h[h["c_spread"].notna() & h["c_total"].notna()].copy()
    h["mkt_margin"] = -h["c_spread"]
    h["mkt_total"] = h["c_total"]
    fit, test = h[h["season"].isin(FIT_SEASONS)], h[h["season"].isin(TEST_SEASONS)]
    print(f"2) Partidos con línea de cierre: {len(fit)} para medir el aporte, {len(test)} de prueba final.")

    def mae(a, b):
        return float(np.abs(a - b).mean())

    report = {"trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "tune_seasons": TUNE_SEASONS, "fit_seasons": FIT_SEASONS, "test_seasons": TEST_SEASONS,
              "n_fit": len(fit), "n_test": len(test)}
    weights = {}
    for kind, actual, model, market in (("margin", "actual_margin", "margin", "mkt_margin"),
                                        ("total", "actual_total", "total", "mkt_total")):
        w, se = ols_weight((fit[actual] - fit[market]).to_numpy(), (fit[model] - fit[market]).to_numpy())
        t = w / se if se > 0 else None
        mse_mkt = float(((test[actual] - test[market]) ** 2).mean())
        mse_blend = float(((test[actual] - (test[market] + w * (test[model] - test[market]))) ** 2).mean())
        mse_model = float(((test[actual] - test[model]) ** 2).mean())
        useful = t is not None and t >= 2 and w > 0 and mse_blend < mse_mkt
        weights[kind] = round(w, 3) if useful else 0.0
        report[kind] = {
            "mae_model": round(mae(h[actual], h[model]), 2), "mae_market": round(mae(h[actual], h[market]), 2),
            "w": round(w, 3), "w_se": round(se, 3), "t": round(t, 2) if t is not None else None,
            "test_rmse_market": round(math.sqrt(mse_mkt), 3), "test_rmse_blend": round(math.sqrt(mse_blend), 3),
            "test_rmse_model": round(math.sqrt(mse_model), 3), "used_weight": weights[kind],
        }
        print(f"   {kind}: error medio modelo {report[kind]['mae_model']} vs línea {report[kind]['mae_market']} pts; "
              f"aporte w={w:.3f} (t={t:.1f}); prueba: mezcla {math.sqrt(mse_blend):.3f} vs línea {math.sqrt(mse_mkt):.3f}"
              f" -> {'APORTA' if useful else 'no aporta'}")

    # Dispersión alrededor de lo que se usará en vivo (mezcla con el peso elegido).
    live_m = fit["mkt_margin"] + weights["margin"] * (fit["margin"] - fit["mkt_margin"])
    live_t = fit["mkt_total"] + weights["total"] * (fit["total"] - fit["mkt_total"])
    sd_m_live = float((fit["actual_margin"] - live_m).std())
    sd_t_live = float((fit["actual_total"] - live_t).std())

    # Ganador: Brier del modelo contra la probabilidad sin margen de la casa.
    ml = test[test["c_ml"].notna()]
    p_model = np.array([probability("ML", "HOME", m, 0, sd_m, sd_t) for m in ml["margin"]])
    won = (ml["actual_margin"] > 0).astype(float).to_numpy()
    d = (p_model - won) ** 2 - (ml["c_ml"].to_numpy() - won) ** 2
    t_ml = _paired_t(d)
    report["ml"] = {"n": int(len(ml)), "brier_model": round(float(((p_model - won) ** 2).mean()), 4),
                    "brier_market": round(float(((ml["c_ml"].to_numpy() - won) ** 2).mean()), 4),
                    "t": round(t_ml, 2) if t_ml is not None else None}

    # Apostar con el modelo solo (w = 1) en las temporadas de prueba.
    report["betting"] = {snapshot.lower(): {"modelo_solo": simulate_bets(test, lines, snapshot, 1.0, 1.0, sd_m, sd_t)}
                         for snapshot in ("CLOSE", "OPEN")}
    # La estrategia en vivo con w = 0: comprar el precio de una casa que se
    # aparta del consenso. Solo se puede probar donde ESPN tiene varias casas.
    multi_ids = set(lines[(lines["snapshot"] == "CLOSE") & (lines["n_books"] >= MIN_BOOKS)]["id"])
    multi = h[h["id"].isin(multi_ids)]
    shop = lambda gaps: simulate_bets(multi, lines, "CLOSE", 0.0, 0.0, sd_m_live, sd_t_live,  # noqa: E731
                                      price_mode=True, gap_range=gaps)
    report["price_shopping"] = {
        "n_games": int(len(multi)), "min_books": MIN_BOOKS, "seasons": sorted(multi["season"].unique().tolist()),
        "near": shop((0.0, MAX_LINE_GAP)),            # la regla en vivo
        "far": shop((MAX_LINE_GAP + 0.01, 99.0)),     # líneas apartadas del consenso
        # Las casas de ESPN se registran cada una a su hora: una línea "vieja"
        # parece un regalo que nunca estuvo disponible. Si las líneas apartadas
        # ganan mucho más que las cercanas, es eso y no una ventaja real.
        "trusted": False,
        "reason": "Cada casa de ESPN se registra a una hora distinta: una línea vieja parece un precio "
                  "regalado que en realidad ya no estaba. En vivo las casas de la API de baloncesto se "
                  "leen en la misma consulta, pero la única prueba válida será el contador propio.",
    }
    report["weights_live"] = weights
    print("3) Apuestas simuladas en la prueba final (cuotas reales de ESPN):")
    for snap, sims in report["betting"].items():
        for name, sim in sims.items():
            print(f"   {snap:5s} {name:11s}: {sim['bets']} apuestas, {sim['wins']} ganadas, ROI {sim['roi']} {sim['by_family']}")
    ps = report["price_shopping"]
    print(f"   comprar precio entre casas ({ps['n_games']} partidos con {MIN_BOOKS}+ casas, cierre):")
    print(f"      líneas cercanas (regla en vivo): {ps['near']}")
    print(f"      líneas apartadas (> {MAX_LINE_GAP} pts): {ps['far']}")
    print(f"   ganador (Brier): modelo {report['ml']['brier_model']} vs casa {report['ml']['brier_market']} (t={report['ml']['t']})")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps({
        "params": params.to_dict(), "sd_margin_model": sd_m, "sd_total_model": sd_t,
        "sd_margin": sd_m_live, "sd_total": sd_t_live, "w_margin": weights["margin"], "w_total": weights["total"],
        "trained_at": report["trained_at"],
    }, indent=1), encoding="utf-8")
    VALIDATION_PATH.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Guardado: {PARAMS_PATH.name}, {VALIDATION_PATH.name}")


if __name__ == "__main__":
    main()
