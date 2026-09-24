from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://bet_user:bet_pass@localhost:5432/bet_db"
    api_football_key: str = ""
    odds_min: float = 1.50
    odds_max: float = 2.00
    kelly_fraction_cap: float = 0.25       # Kelly fraccional (1/4 Kelly) por gestión de riesgo
    # Habilidad mínima (accuracy - tasa base) demostrada en backtest para que
    # un mercado genere picks. 0.0 = al menos no peor que apostar a ciegas.
    # Antes era accuracy cruda >= 0.55, que no es comparable entre mercados y
    # rechazaba la línea de córners con MÁS habilidad.
    min_historical_skill: float = 0.0
    model_version: str = "v1"
    # Fútbol colombiano: modelo, picks, evaluaciones y contadores propios
    # (apartado aparte en la app; no suma a los contadores de Europa).
    colombia_model_version: str = "col-v1"
    colombia_top_picks_count: int = 3
    # NBA: tablas, modelo, picks y contadores propios (apartado aparte; no suma
    # a los de fútbol). Ver ml/nba/.
    nba_model_version: str = "nba-v1"
    nba_top_picks_count: int = 3
    top_picks_count: int = 5  # cuántos "picks del día" se muestran (top N por EV, máx. 1 por partido)

    # --- Guardas de suficiencia de datos (ver README: "apostar a ciegas") ---
    # Sin historial propio, el modelo devuelve la tasa base de la población
    # (ej. P(Over 1.5)=0.746, el promedio global) y cualquier mercado que
    # discrepe parece "+EV". Eso no es ventaja: es ignorancia disfrazada.
    min_team_history_matches: int = 10
    # Un EV real en un mercado eficiente vive entre +2% y +8%. Un EV enorme
    # casi siempre significa que el modelo está mal, no que el mercado lo está.
    max_plausible_ev: float = 0.20
    # Las cuotas son perecederas: calcular EV sobre un precio de hace horas
    # produce una ventaja que ya no se puede tomar.
    max_odds_age_hours: float = 6.0
    # EV mínimo para que algo cuente como pick. Un +0.7% no es una recomendación:
    # no cubre ni el movimiento normal de la línea. Coincide con el piso de "MEDIA".
    min_pick_ev: float = 0.02
    # Casas donde el usuario puede apostar, separadas por comas (ej. "Betano,Bet365").
    # Vacío = todas. El EV de un pick solo existe en la casa que da esa cuota:
    # si el pick sale de una casa donde no se puede apostar (Bet365 está
    # bloqueada en Colombia), el EV mostrado no es el del usuario. La
    # probabilidad "justa" del mercado se sigue calculando con TODAS las casas.
    allowed_bookmakers: str = ""

    @property
    def allowed_bookmaker_set(self) -> frozenset[str]:
        return frozenset(b.strip().lower() for b in self.allowed_bookmakers.split(",") if b.strip())

    def bookmaker_allowed(self, bookmaker: str | None) -> bool:
        allowed = self.allowed_bookmaker_set
        return not allowed or (bookmaker or "").lower() in allowed


@lru_cache
def get_settings() -> Settings:
    return Settings()
