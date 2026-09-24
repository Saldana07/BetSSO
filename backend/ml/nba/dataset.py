"""Lectura de partidos y cuotas de la NBA desde la base de datos."""
from collections import defaultdict

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NbaGame, NbaOdds
from ml.value_betting.market import fair_probabilities

MODEL_SEASON_TYPES = ("REGULAR", "PLAYIN", "PLAYOFFS", "CUP")
GAME_COLUMNS = ["id", "season", "season_type", "game_date", "tipoff_utc", "home_team_id", "away_team_id", "status",
                "home_pts", "away_pts", "home_fga", "away_fga", "home_fta", "away_fta",
                "home_oreb", "away_oreb", "home_tov", "away_tov"]


def load_games(db: Session) -> pd.DataFrame:
    """Partidos que cuentan para el modelo (sin pretemporada): los jugados
    con marcador y los programados (estos solo reciben predicción)."""
    rows = db.execute(select(NbaGame).where(NbaGame.season_type.in_(MODEL_SEASON_TYPES),
                                            NbaGame.status.in_(("FINISHED", "SCHEDULED", "LIVE")))).scalars().all()
    df = pd.DataFrame([{c: getattr(g, c) for c in GAME_COLUMNS} for g in rows], columns=GAME_COLUMNS)
    if df.empty:
        return df
    # Un partido "terminado" sin marcador no sirve para actualizar.
    unfinished = df["status"] != "FINISHED"
    df.loc[unfinished, ["home_pts", "away_pts"]] = None
    return df.sort_values(["game_date", "id"]).reset_index(drop=True)


def odds_by_game(db: Session, game_ids: list[str], snapshot: str, source: str | None = None) -> dict:
    """{partido: {mercado: {casa: {selección: cuota}}}} con la foto más reciente
    de cada casa. Solo esa foto: si la casa movió la línea de -5.5 a -6.5, la
    -5.5 vieja ya no existe y no puede contar como precio vigente."""
    q = select(NbaOdds).where(NbaOdds.game_id.in_(game_ids), NbaOdds.snapshot == snapshot)
    if source:
        q = q.where(NbaOdds.source == source)
    rows = db.execute(q).scalars().all()
    last_at: dict = {}
    for o in rows:
        key = (o.game_id, o.source, o.bookmaker)
        if key not in last_at or last_at[key] < o.recorded_at:
            last_at[key] = o.recorded_at
    out: dict = defaultdict(lambda: defaultdict(dict))
    for o in rows:
        if o.recorded_at == last_at[(o.game_id, o.source, o.bookmaker)]:
            out[o.game_id][o.market].setdefault(o.bookmaker, {})[o.selection] = float(o.odds_value)
    return out


def closing_lines(db: Session, seasons: list[str]) -> pd.DataFrame:
    """Por partido: línea de cierre del hándicap del local y del total
    (mediana entre casas), y probabilidad sin margen del local en el ganador."""
    ids = [gid for (gid,) in db.execute(select(NbaGame.id).where(NbaGame.season.in_(seasons),
                                                                  NbaGame.espn_event_id.isnot(None)))]
    rows = []
    for chunk in range(0, len(ids), 2000):
        part = ids[chunk:chunk + 2000]
        for snapshot in ("CLOSE", "OPEN"):
            for gid, markets in odds_by_game(db, part, snapshot, "ESPN").items():
                spreads, totals, ml = [], [], []
                for market, books in markets.items():
                    if market.startswith("SPREAD_"):
                        spreads += [float(market[7:])] * len(books)
                    elif market.startswith("TOTAL_"):
                        totals += [float(market[6:])] * len(books)
                    elif market == "ML":
                        fair, n = fair_probabilities("ML", books)
                        if n:
                            ml.append(fair["HOME"])
                books_seen = {b for books in markets.values() for b in books}
                rows.append({"id": gid, "snapshot": snapshot, "n_books": len(books_seen),
                             "spread": pd.Series(spreads).median() if spreads else None,
                             "total": pd.Series(totals).median() if totals else None,
                             "ml_home": pd.Series(ml).median() if ml else None,
                             "markets": markets})
    return pd.DataFrame(rows)
