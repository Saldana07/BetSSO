"""Análisis retrospectivo: ¿qué habría dicho el sistema ANTES de un partido ya
jugado, y qué pasó?

Para cada partido terminado de una fecha con historial suficiente:
  1. calcula las probabilidades del modelo con el estado PRE-partido (los
     features solo usan partidos anteriores al inicio: sin fuga),
  2. trae las cuotas pre-partido (API-Football las conserva tras el final) y
     las guarda como snapshot CLOSING,
  3. ordena los mercados igual que la vista "Todos los mercados" (validados
     primero, luego EV) y toma "lo más favorable" creíble,
  4. lo liquida contra el resultado real.

*** ES UN ENSAYO, NO RENDIMIENTO REAL *** Estos picks no se emitieron antes
del partido, así que no cuentan para el % de acierto ni el P&L (esos solo
miden picks reales de generate_daily_pick). Sirve para ver cómo se habría
comportado el sistema en partidos que no alcanzó a analizar a tiempo.

Uso:
    python -m jobs.retro_analysis --date 2026-09-17
"""
import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import BacktestResult, Match, Odds
from ml.ingestion.api_football_client import APIFootballClient, APIFootballError
from ml.ingestion.mappers import upsert_odds_for_match
from ml.serving.predict import get_models, predict_match_probabilities
from ml.value_betting.ev_kelly import expected_value
from ml.value_betting.settlement import MissingStatsError, UnknownMarketError, bet_won

settings = get_settings()


def _skill_by_market(db) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for r in db.execute(
        select(BacktestResult).where(BacktestResult.model_version == settings.model_version)
        .order_by(BacktestResult.created_at.desc())
    ).scalars().all():
        if r.market not in out:
            out[r.market] = (float(r.accuracy) - float(r.base_accuracy)) if r.accuracy is not None and r.base_accuracy is not None else None
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    start = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    db = SessionLocal()
    models = get_models(model_version=settings.model_version)
    skill = _skill_by_market(db)
    tier = {"VALIDADO": 0, "SIN_VALIDAR": 1, "SIN_VENTAJA": 2}

    finished = db.execute(
        select(Match).where(
            Match.kickoff_utc >= start, Match.kickoff_utc < end, Match.status == "FINISHED",
            Match.home_goals.isnot(None), Match.api_match_id.isnot(None),
        ).order_by(Match.kickoff_utc)
    ).scalars().all()

    print(f"*** ENSAYO RETROSPECTIVO {args.date} — no cuenta como rendimiento real ***\n")
    results = []
    with APIFootballClient() as client:
        for match in finished:
            probs = predict_match_probabilities(db, match, models)
            d = probs["_diagnostics"]
            if min(d["home_history"], d["away_history"]) < settings.min_team_history_matches:
                continue

            # Cuotas pre-partido (cierre) si aún no las tenemos.
            has_closing = db.execute(
                select(func.count(Odds.id)).where(Odds.match_id == match.id, Odds.snapshot_type == "CLOSING")
            ).scalar_one()
            if not has_closing:
                try:
                    for payload in client.get_odds(fixture_id=match.api_match_id):
                        upsert_odds_for_match(db, match.id, payload, "CLOSING", datetime.now(timezone.utc))
                    db.flush()
                except APIFootballError as exc:
                    print(f"  [!] sin cuotas para {match.home_team.name} vs {match.away_team.name}: {exc}")
                    continue

            best: dict[tuple[str, str], Odds] = {}
            for o in db.execute(select(Odds).where(Odds.match_id == match.id, Odds.snapshot_type == "CLOSING")).scalars():
                k = (o.market, o.selection)
                if k not in best or o.odds_value > best[k].odds_value:
                    best[k] = o

            rows = []
            for (market, selection), o in best.items():
                p = probs.get(f"{market}_{selection}")
                if p is None:
                    continue
                odds = float(o.odds_value)
                if not (settings.odds_min <= odds <= settings.odds_max):
                    continue
                ev = expected_value(p, odds)
                s = skill.get(market)
                validation = "SIN_VALIDAR" if s is None else ("VALIDADO" if s >= settings.min_historical_skill else "SIN_VENTAJA")
                # Mismo orden que la API: EV real primero, luego credibilidad, luego EV.
                suspicious = ev > settings.max_plausible_ev
                rows.append((ev < settings.min_pick_ev or suspicious, tier[validation], suspicious, -ev, market, selection, o.bookmaker, odds, p, validation))
            if not rows:
                continue
            rows.sort()
            no_real_ev, _, suspicious, neg_ev, market, selection, bookmaker, odds, p, validation = rows[0]
            if no_real_ev:
                results.append((match, None, None, None, None, None, -neg_ev, validation, False, None,
                                *_fav_and_actual(probs, match)))
                continue

            if market.startswith("CORNERS_") and match.home_corners is None:
                corners = client.get_fixture_corners(match.api_match_id)
                if corners:
                    match.home_corners, match.away_corners = corners
            try:
                won = bet_won(market, selection, match.home_goals, match.away_goals, match.home_corners, match.away_corners)
            except (MissingStatsError, UnknownMarketError):
                won = None

            results.append((match, market, selection, bookmaker, odds, p, -neg_ev, validation, suspicious, won,
                            *_fav_and_actual(probs, match)))
    db.commit()

    for (m, market, sel, bm, odds, p, ev, val, susp, won, fav, pfav, actual) in results:
        res = f"{m.home_goals}-{m.away_goals}"
        print(f"{m.home_team.name} {res} {m.away_team.name}   (historial ok)")
        print(f"   1X2 del modelo: favorito {fav} {pfav:.0%}  ->  resultado real {actual}  {'✓' if fav == actual else '✗'}")
        if market is None:
            print(f"   Lo más favorable: NADA — ningún mercado en rango con EV >= {settings.min_pick_ev:.0%} (el mejor era {ev:+.1%}). No se apuesta.")
        else:
            outcome = "GANÓ" if won else ("PERDIÓ" if won is False else "sin liquidar")
            print(f"   Lo más favorable: {market} {sel} @ {odds:.2f} ({bm})  modelo {p:.0%} vs mercado {1/odds:.0%}  EV {ev:+.1%}  [{val}{' !sospechoso' if susp else ''}]  ->  {outcome}")
        print()

    bets = [r for r in results if r[1] is not None and r[9] is not None]
    w = sum(1 for r in bets if r[9])
    pnl = sum((r[4] - 1) if r[9] else -1 for r in bets)
    fav_ok = sum(1 for r in results if r[10] == r[12])
    print(f"Partidos analizables: {len(results)} | con algo favorable: {len(bets)} -> acertó {w}/{len(bets)} (P&L {pnl:+.2f} u a 1 u/apuesta) | favorito 1X2 del modelo acertó {fav_ok}/{len(results)}")
    print("Recordatorio: ensayo retrospectivo. Con esta muestra es anécdota, no evidencia.")
    db.close()


def _fav_and_actual(probs: dict, match: Match) -> tuple[str, float, str]:
    p1x2 = {k: probs[f"1X2_{k}"] for k in ("HOME", "DRAW", "AWAY")}
    fav = max(p1x2, key=p1x2.get)
    actual = "HOME" if match.home_goals > match.away_goals else ("AWAY" if match.away_goals > match.home_goals else "DRAW")
    return fav, p1x2[fav], actual


if __name__ == "__main__":
    main()
