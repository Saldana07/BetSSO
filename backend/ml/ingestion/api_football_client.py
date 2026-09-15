"""Cliente HTTP para API-Football (https://www.api-football.com/), acceso directo
(no vía RapidAPI) usando el header `x-apisports-key`.

El plan Free de API-Football es muy limitado (100 requests/día, 10/min), así
que este cliente:
  - Registra el consumo restante que la propia API devuelve en headers
    (`x-ratelimit-remaining`, `x-ratelimit-requests-remaining`) para poder
    frenar antes de agotar la cuota diaria.
  - Aplica backoff automático ante 429 (rate limit) respetando `Retry-After`.
  - Nunca reintenta si la cuota DIARIA está agotada (no tiene sentido esperar).
"""
import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

BASE_URL = "https://v3.football.api-sports.io"
# El límite por minuto del plan Free es 10 req/min; esperar algo más de un
# minuto garantiza que la ventana se haya reiniciado por completo.
RATE_LIMIT_COOLDOWN_SECONDS = 65


class APIFootballError(Exception):
    pass


class DailyQuotaExhaustedError(APIFootballError):
    pass


@dataclass
class RateLimitState:
    per_minute_remaining: int | None = None
    per_day_remaining: int | None = None
    per_day_limit: int | None = None


class APIFootballClient:
    def __init__(self, api_key: str | None = None, timeout: float = 15.0):
        settings = get_settings()
        key = api_key or settings.api_football_key
        if not key:
            raise APIFootballError("API_FOOTBALL_KEY no está configurada (revisa backend/.env)")

        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"x-apisports-key": key},
            timeout=timeout,
        )
        self.rate_limit = RateLimitState()

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _update_rate_limit(self, headers: httpx.Headers):
        if "x-ratelimit-remaining" in headers:
            self.rate_limit.per_minute_remaining = int(headers["x-ratelimit-remaining"])
        if "x-ratelimit-requests-remaining" in headers:
            self.rate_limit.per_day_remaining = int(headers["x-ratelimit-requests-remaining"])
        if "x-ratelimit-requests-limit" in headers:
            self.rate_limit.per_day_limit = int(headers["x-ratelimit-requests-limit"])

    def _get(self, endpoint: str, params: dict | None = None, max_retries: int = 3) -> list[dict]:
        if self.rate_limit.per_day_remaining == 0:
            raise DailyQuotaExhaustedError(
                f"Cuota diaria de API-Football agotada (limite={self.rate_limit.per_day_limit}). "
                "Reintenta mañana o sube de plan."
            )

        # Frena proactivamente ANTES de agotar la ventana del minuto: esperar
        # después de chocar cuesta un reintento perdido en cada llamada.
        if self.rate_limit.per_minute_remaining is not None and self.rate_limit.per_minute_remaining <= 1:
            time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)
            self.rate_limit.per_minute_remaining = None

        last_error: Exception | None = None
        for attempt in range(max_retries):
            response = self._client.get(endpoint, params=params or {})
            self._update_rate_limit(response.headers)

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "6"))
                time.sleep(retry_after)
                last_error = APIFootballError(f"429 rate limited en {endpoint}")
                continue

            response.raise_for_status()
            payload = response.json()
            errors = payload.get("errors")

            if errors:
                # API-Football NO usa 429 para el límite por minuto: devuelve
                # HTTP 200 con el aviso dentro de `errors`. Si esto se trata
                # como un error normal, el backoff nunca se activa y cualquier
                # sincronización larga revienta a mitad de camino.
                if isinstance(errors, dict) and "rateLimit" in errors:
                    time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)
                    last_error = APIFootballError(f"Límite por minuto alcanzado en {endpoint}")
                    continue
                raise APIFootballError(f"API-Football devolvió errores en {endpoint}: {errors}")

            return payload.get("response", [])

        raise last_error or APIFootballError(f"Fallo desconocido llamando a {endpoint}")

    def get_status(self) -> dict:
        results = self._get("/status")
        return results if isinstance(results, dict) else {}

    def get_fixtures_by_date(self, date: str, league: int | None = None, season: int | None = None) -> list[dict]:
        """date en formato 'YYYY-MM-DD'."""
        params = {"date": date}
        if league is not None:
            params["league"] = league
        if season is not None:
            params["season"] = season
        return self._get("/fixtures", params)

    def get_fixture_by_id(self, fixture_id: int) -> dict | None:
        results = self._get("/fixtures", {"id": fixture_id})
        return results[0] if results else None

    def get_odds(
        self,
        fixture_id: int | None = None,
        date: str | None = None,
        league: int | None = None,
        season: int | None = None,
        bookmaker: int | None = None,
        page: int = 1,
    ) -> list[dict]:
        params: dict = {"page": page}
        if fixture_id is not None:
            params["fixture"] = fixture_id
        if date is not None:
            params["date"] = date
        if league is not None:
            params["league"] = league
        if season is not None:
            params["season"] = season
        if bookmaker is not None:
            params["bookmaker"] = bookmaker
        return self._get("/odds", params)

    def get_injuries(
        self,
        fixture_id: int | None = None,
        team: int | None = None,
        league: int | None = None,
        season: int | None = None,
        date: str | None = None,
    ) -> list[dict]:
        params: dict = {}
        if fixture_id is not None:
            params["fixture"] = fixture_id
        if team is not None:
            params["team"] = team
        if league is not None:
            params["league"] = league
        if season is not None:
            params["season"] = season
        if date is not None:
            params["date"] = date
        return self._get("/injuries", params)

    def get_teams(self, league: int, season: int) -> list[dict]:
        return self._get("/teams", {"league": league, "season": season})

    def get_leagues(self, country: str | None = None, search: str | None = None) -> list[dict]:
        params = {}
        if country:
            params["country"] = country
        if search:
            params["search"] = search
        return self._get("/leagues", params)

    def get_team_statistics(self, team: int, league: int, season: int) -> dict | None:
        results = self._get("/teams/statistics", {"team": team, "league": league, "season": season})
        return results if isinstance(results, dict) and results else None
