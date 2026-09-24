"""Modelo de líneas de jugadores de la NBA: puntos, rebotes, asistencias,
triples y sus combinados.

Proyección de un jugador para un partido (solo con partidos ANTERIORES):

    esperado = minutos esperados × producción por minuto × ritmo × defensa rival

  - minutos esperados: media con más peso a lo reciente (vida media
    `hl_minutes` partidos): capta cambios de rol y rotación.
  - producción por minuto: estadística / minutos con vida media más larga
    (`hl_rate`): es más estable que los minutos.
  - ritmo: posesiones esperadas del partido / las de un partido típico de su
    equipo (del modelo de equipos, ml/nba/ratings.py). Más posesiones, más de
    todo.
  - defensa rival (puntos y triples): puntos por 100 posesiones que permite el
    rival respecto a la media.

El resultado se reparte con una binomial negativa (varianza = media +
alpha·media²), con alpha medido por estadística: un jugador de 20 puntos de
media hace 12 o 30 con frecuencia, más que lo que diría una Poisson.

Validación (main): parámetros en 2022-23 y 2023-24; prueba contra resultados
en 2024-25 y 2025-26; y la prueba que decide, contra las líneas REALES de
cierre de DraftKings de los playoffs 2026 (las que ESPN guarda). Sin esa
prueba superada, los picks de jugadores no usan la opinión del modelo: solo
las diferencias de precio entre casas.

Uso:
    python -m ml.nba.props [--reusar-ajuste]
"""
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import nbinom
from sqlalchemy import select
from sqlalchemy.orm import Session

from ml.nba.dataset import MODEL_SEASON_TYPES, load_games
from ml.nba.player_sources import BASE_STATS, COMBOS
from ml.nba.ratings import Params, replay

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "nba"
PROPS_PATH = MODEL_DIR / "props.json"

TRAIN_SEASONS = ["2022-23", "2023-24"]            # 2021-22 solo calienta las medias
TEST_SEASONS = ["2024-25", "2025-26"]
ALL_STATS = BASE_STATS + tuple(COMBOS)
ODDS_MIN, ODDS_MAX, MIN_EV, MAX_EV = 1.50, 2.00, 0.02, 0.20


@dataclass
class PropParams:
    hl_minutes: float = 5.0
    hl_rate: float = 15.0
    min_games: int = 8          # menos partidos previos: no se proyecta
    pace: bool = True
    defense: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# --- Datos --------------------------------------------------------------------

def load_player_frame(db: Session) -> pd.DataFrame:
    from app.models import NbaGame, NbaPlayerGame
    q = (select(NbaPlayerGame.game_id, NbaPlayerGame.player_id, NbaPlayerGame.team_id, NbaPlayerGame.minutes,
                NbaPlayerGame.pts, NbaPlayerGame.reb, NbaPlayerGame.ast, NbaPlayerGame.fg3m, NbaGame.game_date,
                NbaGame.season, NbaGame.season_type, NbaGame.home_team_id, NbaGame.away_team_id)
         .join(NbaGame, NbaGame.id == NbaPlayerGame.game_id)
         .where(NbaGame.season_type.in_(MODEL_SEASON_TYPES)))
    df = pd.DataFrame(db.execute(q).all(), columns=["game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast",
                                                    "fg3m", "game_date", "season", "season_type", "home_team_id",
                                                    "away_team_id"])
    df["minutes"] = df["minutes"].astype(float)
    df["is_home"] = df["team_id"] == df["home_team_id"]
    return df.sort_values(["player_id", "game_date", "game_id"]).reset_index(drop=True)


def team_context(db: Session) -> pd.DataFrame:
    """Por partido, antes de jugarse: posesiones esperadas, ritmo y defensa de
    cada equipo (del modelo de equipos ya entrenado)."""
    from ml.nba.train import PARAMS_PATH
    params = Params(**json.loads(PARAMS_PATH.read_text(encoding="utf-8"))["params"])
    preds, _ = replay(load_games(db), params)
    return preds.set_index("id")


def stat_series(df: pd.DataFrame, stat: str) -> pd.Series:
    return sum(df[p.lower()] for p in COMBOS.get(stat, (stat,))).astype(float)


def add_projections(df: pd.DataFrame, ctx: pd.DataFrame, p: PropParams) -> pd.DataFrame:
    """Proyección de cada jugador en cada partido con SOLO sus partidos
    anteriores (medias exponenciales desplazadas un partido)."""
    out = df.copy()
    g = out.groupby("player_id", sort=False)
    out["n_prev"] = g.cumcount()
    out["m_proj"] = g["minutes"].transform(lambda s: s.ewm(halflife=p.hl_minutes).mean().shift())
    out["m_rate"] = g["minutes"].transform(lambda s: s.ewm(halflife=p.hl_rate).mean().shift())
    for stat in BASE_STATS:
        col = stat.lower()
        out[f"r_{col}"] = g[col].transform(lambda s: s.ewm(halflife=p.hl_rate).mean().shift()) / out["m_rate"]
    c = ctx.reindex(out["game_id"])
    home = out["is_home"].to_numpy()
    team_pace = np.where(home, c["home_pace"].to_numpy(), c["away_pace"].to_numpy())
    opp_dfn = np.where(home, c["away_dfn"].to_numpy(), c["home_dfn"].to_numpy())
    pace_f = c["poss"].to_numpy() / (c["league_pace"].to_numpy() + team_pace) if p.pace else 1.0
    def_f = (c["league_ortg"].to_numpy() + opp_dfn) / c["league_ortg"].to_numpy() if p.defense else 1.0
    out["pace_f"] = np.nan_to_num(np.asarray(pace_f, dtype=float) * np.ones(len(out)), nan=1.0)
    out["def_f"] = np.nan_to_num(np.asarray(def_f, dtype=float) * np.ones(len(out)), nan=1.0)
    for stat in BASE_STATS:
        col = stat.lower()
        factor = out["pace_f"] * (out["def_f"] if stat in ("PTS", "FG3M") else 1.0)
        out[f"mu_{col}"] = out["m_proj"] * out[f"r_{col}"] * factor
    for combo, parts in COMBOS.items():
        out[f"mu_{combo.lower()}"] = sum(out[f"mu_{x.lower()}"] for x in parts)
    out.loc[out["n_prev"] < p.min_games, [f"mu_{s.lower()}" for s in ALL_STATS]] = np.nan
    return out


# --- Distribución ---------------------------------------------------------------

def fit_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    """Sobredispersión de la binomial negativa por momentos: Var = mu + a·mu²."""
    ok = np.isfinite(mu) & (mu > 0)
    y, mu = y[ok], mu[ok]
    return float(max(0.0, np.mean((y - mu) ** 2 - mu) / np.mean(mu ** 2))) if len(mu) else 0.0


def prob_over(mu: float, line: float, alpha: float) -> float:
    """P(estadística > línea) con línea .5."""
    if not np.isfinite(mu) or mu <= 0:
        return float("nan")
    k = math.floor(line)
    if alpha <= 1e-6:
        from scipy.stats import poisson
        return float(1 - poisson.cdf(k, mu))
    n = 1 / alpha
    return float(1 - nbinom.cdf(k, n, n / (n + mu)))


def prob(mu: float, line: float, side: str, alpha: float) -> float:
    po = prob_over(mu, line, alpha)
    return po if side == "OVER" else 1 - po


# --- Estado en vivo ---------------------------------------------------------------

def latest_states(df: pd.DataFrame, p: PropParams) -> pd.DataFrame:
    """Para el próximo partido de cada jugador: sus medias con TODOS sus
    partidos jugados (sin desplazar), equipo y fecha del último."""
    g = df.groupby("player_id", sort=False)
    last = g.tail(1).set_index("player_id")
    st = pd.DataFrame(index=last.index)
    st["team_id"] = last["team_id"]
    st["last_date"] = last["game_date"]
    st["n_games"] = g.size()
    st["m_proj"] = g["minutes"].apply(lambda s: s.ewm(halflife=p.hl_minutes).mean().iloc[-1])
    m_rate = g["minutes"].apply(lambda s: s.ewm(halflife=p.hl_rate).mean().iloc[-1])
    for stat in BASE_STATS:
        col = stat.lower()
        st[f"r_{col}"] = g[col].apply(lambda s: s.ewm(halflife=p.hl_rate).mean().iloc[-1]) / m_rate
    # ¿Sus últimos 3 partidos fueron con el mismo equipo? (traspasos: rol incierto)
    st["same_team_3"] = g["team_id"].apply(lambda s: s.tail(3).nunique() == 1 and len(s) >= 3)
    return st


def project_player(state, ctx_row, is_home: bool, p: PropParams) -> dict[str, float]:
    """Medias esperadas de un jugador para un partido que aún no se juega."""
    team_pace = ctx_row["home_pace"] if is_home else ctx_row["away_pace"]
    opp_dfn = ctx_row["away_dfn"] if is_home else ctx_row["home_dfn"]
    pace_f = ctx_row["poss"] / (ctx_row["league_pace"] + team_pace) if p.pace else 1.0
    def_f = (ctx_row["league_ortg"] + opp_dfn) / ctx_row["league_ortg"] if p.defense else 1.0
    mu = {}
    for stat in BASE_STATS:
        col = stat.lower()
        mu[stat] = state["m_proj"] * state[f"r_{col}"] * pace_f * (def_f if stat in ("PTS", "FG3M") else 1.0)
    for combo, parts in COMBOS.items():
        mu[combo] = sum(mu[x] for x in parts)
    return mu


def load_props_model() -> dict | None:
    try:
        return json.loads(PROPS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- Entrenamiento y validación --------------------------------------------------

def _poisson_deviance(y, mu) -> float:
    ok = np.isfinite(mu) & (mu > 0)
    y, mu = y[ok], mu[ok]
    with np.errstate(divide="ignore", invalid="ignore"):
        term = np.where(y > 0, y * np.log(y / mu), 0.0) - (y - mu)
    return float(2 * np.mean(term))


def tune(df: pd.DataFrame, ctx: pd.DataFrame) -> PropParams:
    train = df["season"].isin(TRAIN_SEASONS)
    best, best_loss = None, float("inf")
    for hm in (3.0, 5.0, 8.0):
        for hr in (8.0, 15.0, 30.0):
            for pace, defense in ((True, True), (False, False)):
                p = PropParams(hl_minutes=hm, hl_rate=hr, pace=pace, defense=defense)
                f = add_projections(df, ctx, p)[train]
                loss = np.mean([_poisson_deviance(f[s.lower()].to_numpy(float), f[f"mu_{s.lower()}"].to_numpy(float))
                                for s in BASE_STATS])
                print(f"   minutos {hm:>4} · por minuto {hr:>4} · ritmo/defensa {'sí' if pace else 'no'}: {loss:.4f}")
                if loss < best_loss:
                    best, best_loss = p, loss
    return best


def _paired_t(d: np.ndarray) -> float | None:
    if len(d) < 3 or d.std(ddof=1) == 0:
        return None
    return float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))


def outcomes_test(f: pd.DataFrame, alphas: dict) -> dict:
    """En temporadas no vistas: error de la proyección contra el promedio de
    los últimos 10 partidos, y calibración en la línea mediana."""
    t = f[f["season"].isin(TEST_SEASONS)]
    out = {}
    for stat in ALL_STATS:
        col = stat.lower()
        y = stat_series(t, stat).to_numpy()
        mu = t[f"mu_{col}"].to_numpy(float)
        naive = stat_series(t, stat).groupby(t["player_id"]).transform(lambda s: s.rolling(10, min_periods=5).mean().shift()).to_numpy(float)
        ok = np.isfinite(mu) & np.isfinite(naive) & (mu > 0)
        lines = np.floor(mu[ok]) + 0.5
        p_over = np.array([prob_over(m, l, alphas[stat]) for m, l in zip(mu[ok], lines)])
        hit = (y[ok] > lines).astype(float)
        fin = np.isfinite(p_over)
        p_over, hit = p_over[fin], hit[fin]
        bins = pd.cut(p_over, [0, 0.4, 0.45, 0.5, 0.55, 0.6, 1])
        calib = pd.DataFrame({"p": p_over, "y": hit}).groupby(bins, observed=True).agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size"))
        out[stat] = {
            "n": int(ok.sum()),
            "mae_model": round(float(np.mean(np.abs(y[ok] - mu[ok]))), 3),
            "mae_last10": round(float(np.mean(np.abs(y[ok] - naive[ok]))), 3),
            "brier": round(float(np.mean((p_over - hit) ** 2)), 4),
            "calibration": [{"pred": round(r.p, 3), "real": round(r.y, 3), "n": int(r.n)} for r in calib.itertuples()],
        }
    return out


def market_rows(db: Session, f: pd.DataFrame, alphas: dict) -> pd.DataFrame:
    """Pares de más/menos de DraftKings (cierre) con la proyección del modelo y
    el resultado. Si el jugador no jugó, la apuesta se anula: fuera."""
    from app.models import NbaPropOdds
    q = select(NbaPropOdds.game_id, NbaPropOdds.player_id, NbaPropOdds.stat, NbaPropOdds.line, NbaPropOdds.side,
               NbaPropOdds.odds_value, NbaPropOdds.snapshot).where(
        NbaPropOdds.source == "ESPN", NbaPropOdds.bookmaker == "DraftKings", NbaPropOdds.player_id.isnot(None))
    o = pd.DataFrame(db.execute(q).all(), columns=["game_id", "player_id", "stat", "line", "side", "odds", "snapshot"])
    if o.empty:
        return o
    o["line"] = o["line"].astype(float)
    o["odds"] = o["odds"].astype(float)
    o = o[(o["line"] % 1) == 0.5]
    wide = o.pivot_table(index=["game_id", "player_id", "stat", "line", "snapshot"], columns="side", values="odds",
                         aggfunc="max").reset_index()
    wide = wide.dropna(subset=[c for c in ("OVER", "UNDER") if c in wide])
    if "OVER" not in wide or "UNDER" not in wide:
        return pd.DataFrame()
    inv = 1 / wide["OVER"] + 1 / wide["UNDER"]
    wide = wide[(inv > 0.98) & (inv < 1.35)].copy()
    wide["p_mkt"] = (1 / wide["OVER"]) / (1 / wide["OVER"] + 1 / wide["UNDER"])
    fx = f.set_index(["game_id", "player_id"])
    rows = []
    for r in wide.itertuples():
        key = (r.game_id, r.player_id)
        if key not in fx.index:
            continue   # no jugó: anulada
        row = fx.loc[key]
        mu = float(row[f"mu_{r.stat.lower()}"])
        if not np.isfinite(mu):
            continue
        actual = float(sum(row[x.lower()] for x in COMBOS.get(r.stat, (r.stat,))))
        rows.append({"game_id": r.game_id, "player_id": r.player_id, "stat": r.stat, "line": r.line,
                     "snapshot": r.snapshot, "over": r.OVER, "under": r.UNDER, "p_mkt": r.p_mkt,
                     "p_model": prob_over(mu, r.line, alphas[r.stat]), "mu": mu, "won_over": float(actual > r.line)})
    return pd.DataFrame(rows)


def market_test(m: pd.DataFrame, snapshot: str = "CLOSE") -> dict:
    m = m[m["snapshot"] == snapshot].sort_values("game_id")
    if m.empty:
        return {"n": 0}
    res = {"n": int(len(m)), "games": int(m["game_id"].nunique()), "by_stat": {}}
    d_all = []
    for stat, g in [("TODAS", m)] + list(m.groupby("stat")):
        bm = (g["p_model"] - g["won_over"]) ** 2
        bk = (g["p_mkt"] - g["won_over"]) ** 2
        per_game = (bm - bk).groupby(g["game_id"]).mean().to_numpy()
        t = _paired_t(per_game)
        res["by_stat"][stat] = {"n": int(len(g)), "brier_model": round(float(bm.mean()), 4),
                                "brier_market": round(float(bk.mean()), 4), "t": round(t, 2) if t is not None else None}
        if stat == "TODAS":
            d_all = per_game
    # Mezcla modelo/casa: el peso se elige en la primera mitad y se prueba en la segunda.
    games = m["game_id"].drop_duplicates().tolist()
    first, second = set(games[: len(games) // 2]), set(games[len(games) // 2:])
    a, b = m[m["game_id"].isin(first)], m[m["game_id"].isin(second)]

    def brier(frame, w):
        p = frame["p_mkt"] + w * (frame["p_model"] - frame["p_mkt"])
        return float(((p - frame["won_over"]) ** 2).mean())

    grid = [0.0, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0]
    w_best = min(grid, key=lambda w: brier(a, w)) if len(a) else 0.0
    res["blend"] = {"w_first_half": w_best, "test_brier_blend": round(brier(b, w_best), 4) if len(b) else None,
                    "test_brier_market": round(brier(b, 0.0), 4) if len(b) else None}
    # Apostar donde el modelo ve ventaja, al precio de cierre (1 unidad).
    bets = []
    for r in m.itertuples():
        for side, odds, p in (("OVER", r.over, r.p_model), ("UNDER", r.under, 1 - r.p_model)):
            ev = p * odds - 1
            if ODDS_MIN <= odds <= ODDS_MAX and MIN_EV < ev <= MAX_EV:
                won = r.won_over == 1.0 if side == "OVER" else r.won_over == 0.0
                bets.append(odds - 1 if won else -1.0)
    res["betting_model"] = {"bets": len(bets), "roi": round(float(np.mean(bets)), 4) if bets else None,
                            "wins": int(sum(1 for x in bets if x > 0))}
    return res


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reusar-ajuste", action="store_true", help="Usa los parámetros ya ajustados y solo repite la validación")
    args = parser.parse_args()
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        print("NBA jugadores: cargando partidos y contexto de equipos...")
        df = load_player_frame(db)
        ctx = team_context(db)
        print(f"  {len(df)} filas jugador-partido, {df['player_id'].nunique()} jugadores")
        previous = load_props_model() if args.reusar_ajuste else None
        if previous:
            params = PropParams(**previous["params"])
            print("1) Parámetros ya ajustados con 2022-23 y 2023-24 (no se repite el ajuste).")
        else:
            print("1) Ajuste en 2022-23 y 2023-24 (desviación de Poisson, menos es mejor):")
            params = tune(df, ctx)
        f = add_projections(df, ctx, params)
        train = f[f["season"].isin(TRAIN_SEASONS)]
        alphas = {s: fit_alpha(stat_series(train, s).to_numpy(), train[f"mu_{s.lower()}"].to_numpy(float)) for s in ALL_STATS}
        print("   elegidos:", params.to_dict(), "| dispersión:", {k: round(v, 3) for k, v in alphas.items()})
        print("2) Prueba contra resultados en 2024-25 y 2025-26:")
        outcomes = outcomes_test(f, alphas)
        for s, r in outcomes.items():
            print(f"   {s:4s}: error medio {r['mae_model']} (últimos 10: {r['mae_last10']}), Brier {r['brier']}, n={r['n']}")
        print("3) Prueba contra las líneas reales de DraftKings (playoffs 2026):")
        m = market_rows(db, f, alphas)
        market = {snap: market_test(m, snap) for snap in ("CLOSE", "OPEN")} if len(m) else {}
        for snap, r in market.items():
            if not r.get("n"):
                print(f"   {snap}: sin datos")
                continue
            a = r["by_stat"]["TODAS"]
            print(f"   {snap}: {r['n']} líneas de {r['games']} partidos · Brier modelo {a['brier_model']} vs casa "
                  f"{a['brier_market']} (t={a['t']}) · mezcla {r['blend']} · apostar con el modelo {r['betting_model']}")
    finally:
        db.close()

    close = market.get("CLOSE", {})
    t_all = (close.get("by_stat", {}).get("TODAS") or {}).get("t")
    blend = close.get("blend") or {}
    # El modelo solo manda si le gana a la casa con claridad (t <= -2: su Brier
    # es menor) y la mezcla elegida en la primera mitad mejora la segunda.
    beats = (t_all is not None and t_all <= -2 and blend.get("w_first_half", 0) > 0
             and blend.get("test_brier_blend") is not None and blend["test_brier_blend"] < blend["test_brier_market"])
    w_live = float(blend["w_first_half"]) if beats else 0.0
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    PROPS_PATH.write_text(json.dumps({
        "params": params.to_dict(), "alphas": alphas, "w_live": w_live,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "validation": {"train_seasons": TRAIN_SEASONS, "test_seasons": TEST_SEASONS,
                       "outcomes": outcomes, "market": market},
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Peso del modelo en vivo: {w_live} ({'le gana a la casa' if beats else 'no le gana a la casa: picks solo por diferencias de precio'})")
    print(f"Guardado: {PROPS_PATH.name}")


if __name__ == "__main__":
    main()
