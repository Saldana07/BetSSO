"""Reconcilia identidades entre los equipos sincronizados desde API-Football
(partidos de hoy) y los equipos del histórico de FiveThirtyEight, para que un
partido de hoy herede el Elo y la forma acumulados de años anteriores.

El problema que resuelve: `sync_fixtures.py` crea filas nuevas en `teams`
para cada equipo de API-Football, sin relación con las filas del histórico
(fuentes distintas, sin IDs compartidos). Resultado: los partidos de hoy
arrancan con Elo 1500 y cero forma, y el modelo solo puede devolver la tasa
base de la población. Al repuntar los partidos de hoy hacia los equipos y
ligas canónicos (los del histórico), el modelo recupera años de señal real.

*** ADVERTENCIA SOBRE ANTIGÜEDAD ***
El histórico de 538 termina en junio de 2023. Heredar ese Elo en 2026
significa usar una estimación con ~3 años de desactualización (cambios de
plantilla, ascensos/descensos). Es mucho mejor que nada — la calidad de un
club es bastante persistente — pero NO equivale a un Elo al día. Los equipos
que no existían en primera división en 2023 (ej. ascendidos después)
simplemente no se reconcilian y siguen sin historial, que es lo correcto.

Uso:
    python -m ml.ingestion.reconcile_teams               # dry-run (no escribe nada)
    python -m ml.ingestion.reconcile_teams --apply       # aplica los cambios
"""
import argparse
import unicodedata

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import League, Match, Team
from ml.ingestion.import_fivethirtyeight_spi import SOURCE_TAG

# Ligas de API-Football (nombre, país) -> nombre de la liga equivalente en el
# histórico de 538. Solo se listan las que realmente se solapan; el resto de
# ligas de hoy no tiene histórico y debe quedarse sin reconciliar.
LEAGUE_MAP = {
    ("Liga Profesional Argentina", "Argentina"): "Argentina Primera Division",
    ("Serie A", "Brazil"): "Brasileiro Série A",
    ("Premier League", "England"): "Barclays Premier League",
    ("La Liga", "Spain"): "Spanish Primera Division",
    ("Serie A", "Italy"): "Italy Serie A",
    ("Bundesliga", "Germany"): "German Bundesliga",
    ("Ligue 1", "France"): "French Ligue 1",
    ("Primeira Liga", "Portugal"): "Portuguese Liga",
    ("Eredivisie", "Netherlands"): "Dutch Eredivisie",
    ("Major League Soccer", "USA"): "Major League Soccer",
    ("Liga MX", "Mexico"): "Mexican Primera Division Torneo Apertura",
}


# Alias para clubes que las dos fuentes escriben distinto (no basta con
# normalizar acentos). Clave: nombre normalizado en API-Football; valor:
# nombre normalizado en el histórico de 538.
TEAM_ALIASES = {
    "inter": "internazionale",
    "sc braga": "braga",
    "leeds": "leeds united",
    "guadalajara chivas": "guadalajara",
    "u.n.a.m. - pumas": "pumas unam",
    "estoril": "estoril praia",
}


def normalize(name: str) -> str:
    """Quita acentos, pasa a minúsculas y colapsa espacios, para que
    'Huracán' y 'Huracan' se reconozcan como el mismo club."""
    stripped = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def reconcile(apply_changes: bool = False) -> dict:
    db = SessionLocal()
    stats = {"leagues_matched": 0, "teams_matched": 0, "teams_unmatched": 0, "matches_repointed": 0, "teams_merged": 0}
    try:
        for (live_name, live_country), historical_name in LEAGUE_MAP.items():
            live_league = db.execute(
                select(League).where(League.name == live_name, League.country == live_country)
            ).scalar_one_or_none()
            historical_league = db.execute(
                select(League).where(League.name == historical_name, League.country == SOURCE_TAG)
            ).scalar_one_or_none()

            if live_league is None or historical_league is None:
                continue
            stats["leagues_matched"] += 1

            historical_teams = db.execute(
                select(Team).where(Team.league_id == historical_league.id)
            ).scalars().all()
            by_normalized = {normalize(t.name): t for t in historical_teams}

            live_teams = db.execute(select(Team).where(Team.league_id == live_league.id)).scalars().all()

            for live_team in live_teams:
                key = normalize(live_team.name)
                key = TEAM_ALIASES.get(key, key)
                canonical = by_normalized.get(key)
                if canonical is None:
                    stats["teams_unmatched"] += 1
                    print(f"  [sin match] {live_league.name}: '{live_team.name}' (sin histórico, se queda sin señal)")
                    continue

                stats["teams_matched"] += 1
                print(f"  [match] '{live_team.name}' -> histórico id={canonical.id}")

                if not apply_changes:
                    continue

                # Repunta los partidos del equipo "vivo" hacia el equipo canónico
                # y hacia la liga canónica, para que compute_pre_match_features
                # (que consulta por league_id) vea todo el histórico.
                for match in db.execute(
                    select(Match).where(Match.home_team_id == live_team.id)
                ).scalars().all():
                    match.home_team_id = canonical.id
                    match.league_id = historical_league.id
                    stats["matches_repointed"] += 1
                for match in db.execute(
                    select(Match).where(Match.away_team_id == live_team.id)
                ).scalars().all():
                    match.away_team_id = canonical.id
                    match.league_id = historical_league.id
                    stats["matches_repointed"] += 1

                # Hace la reconciliación PERSISTENTE: el equipo canónico hereda el
                # api_team_id, así el próximo sync_fixtures lo encuentra directo y
                # no vuelve a crear el duplicado (antes había que re-ejecutar esto
                # tras cada sync o los picks perdían el historial en silencio).
                if live_team.api_team_id is not None and canonical.api_team_id is None:
                    api_id = live_team.api_team_id
                    live_team.api_team_id = None
                    db.flush()  # libera la restricción única antes de reasignar
                    canonical.api_team_id = api_id
                    db.delete(live_team)
                    stats["teams_merged"] += 1

            # Lo mismo para la liga: con el api_league_id en la liga canónica, los
            # partidos nuevos nacen ya en el namespace del histórico.
            if apply_changes and live_league.api_league_id is not None and historical_league.api_league_id is None:
                api_id = live_league.api_league_id
                live_league.api_league_id = None
                db.flush()
                historical_league.api_league_id = api_id
                db.flush()
                still_referenced = db.execute(
                    select(func.count(Team.id)).where(Team.league_id == live_league.id)
                ).scalar_one() or db.execute(
                    select(func.count(Match.id)).where(Match.league_id == live_league.id)
                ).scalar_one()
                if not still_referenced:
                    db.delete(live_league)

        if apply_changes:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Aplica los cambios (por defecto es dry-run)")
    args = parser.parse_args()

    if not args.apply:
        print("DRY-RUN — no se escribe nada. Usa --apply para aplicar.\n")

    stats = reconcile(args.apply)
    print(f"\nLigas reconciliadas:   {stats['leagues_matched']}")
    print(f"Equipos reconciliados: {stats['teams_matched']}")
    print(f"Equipos sin histórico: {stats['teams_unmatched']}")
    print(f"Partidos repuntados:   {stats['matches_repointed']}")
    print(f"Equipos fusionados:    {stats['teams_merged']}  (heredan api_team_id: no se recrean en el próximo sync)")


if __name__ == "__main__":
    main()
