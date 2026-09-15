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


@lru_cache
def get_settings() -> Settings:
    return Settings()
