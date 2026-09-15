-- =========================================================
-- Esquema PostgreSQL — Motor de predicciones de fútbol
-- =========================================================

CREATE TABLE leagues (
    id              SERIAL PRIMARY KEY,
    api_league_id   INTEGER UNIQUE,               -- id en API-Football / football-data
    name            VARCHAR(120) NOT NULL,
    country         VARCHAR(80) NOT NULL,
    tier            SMALLINT DEFAULT 1,            -- 1 = primera división, etc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE teams (
    id              SERIAL PRIMARY KEY,
    api_team_id     INTEGER UNIQUE,
    name            VARCHAR(120) NOT NULL,
    league_id       INTEGER REFERENCES leagues(id),
    country         VARCHAR(80),
    -- Elo se versiona en elo_history; aquí solo el valor "vivo" para lecturas rápidas
    elo_rating      NUMERIC(7,2) NOT NULL DEFAULT 1500.00,
    elo_updated_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE matches (
    id              SERIAL PRIMARY KEY,
    api_match_id    INTEGER UNIQUE,
    league_id       INTEGER NOT NULL REFERENCES leagues(id),
    season          VARCHAR(9) NOT NULL,           -- '2025-2026'
    round           VARCHAR(50),
    kickoff_utc     TIMESTAMPTZ NOT NULL,
    home_team_id    INTEGER NOT NULL REFERENCES teams(id),
    away_team_id    INTEGER NOT NULL REFERENCES teams(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED', -- SCHEDULED|LIVE|FINISHED|POSTPONED
    home_goals      SMALLINT,
    away_goals      SMALLINT,
    home_goals_ht   SMALLINT,
    away_goals_ht   SMALLINT,
    home_xg         NUMERIC(4,2),                  -- xG post-partido (proveedor)
    away_xg         NUMERIC(4,2),
    referee         VARCHAR(120),
    venue           VARCHAR(120),
    weather_temp_c  NUMERIC(4,1),
    weather_desc    VARCHAR(80),
    -- Probabilidad de un modelo EXTERNO de referencia (ej. FiveThirtyEight SPI)
    -- para el mismo partido, cuando la fuente de datos la trae. Sirve como
    -- "opinión de mercado" sustituta para simular backtests de EV cuando no
    -- hay cuotas reales de casas de apuestas para ese partido histórico.
    ext_prob_home   NUMERIC(6,4),
    ext_prob_draw   NUMERIC(6,4),
    ext_prob_away   NUMERIC(6,4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_different_teams CHECK (home_team_id <> away_team_id)
);
CREATE INDEX idx_matches_kickoff ON matches (kickoff_utc);
CREATE INDEX idx_matches_status ON matches (status);
CREATE INDEX idx_matches_teams ON matches (home_team_id, away_team_id);

-- Estadísticas por equipo y por partido (una fila por equipo/partido)
CREATE TABLE team_match_stats (
    id                  BIGSERIAL PRIMARY KEY,
    match_id            INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    team_id             INTEGER NOT NULL REFERENCES teams(id),
    is_home             BOOLEAN NOT NULL,
    shots               SMALLINT,
    shots_on_target     SMALLINT,
    possession_pct      NUMERIC(4,1),
    corners             SMALLINT,
    fouls               SMALLINT,
    yellow_cards        SMALLINT,
    red_cards           SMALLINT,
    xg                  NUMERIC(4,2),
    xga                 NUMERIC(4,2),               -- xG concedido
    injuries_count      SMALLINT DEFAULT 0,
    key_players_out     SMALLINT DEFAULT 0,          -- lesionados/sancionados con impacto alto
    UNIQUE (match_id, team_id)
);

-- Cuotas de casas de apuestas (apertura y evolución hasta cierre)
CREATE TABLE odds (
    id              BIGSERIAL PRIMARY KEY,
    match_id        INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker       VARCHAR(60) NOT NULL,
    market          VARCHAR(30) NOT NULL,          -- '1X2','DOUBLE_CHANCE','OU_1.5','OU_2.5','BTTS'
    selection       VARCHAR(20) NOT NULL,          -- 'HOME','DRAW','AWAY','OVER','UNDER','YES','NO','1X','X2','12'
    odds_value      NUMERIC(6,3) NOT NULL CHECK (odds_value > 1.0),
    snapshot_type   VARCHAR(10) NOT NULL,          -- 'OPENING' | 'LIVE' | 'CLOSING'
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_odds_match_market ON odds (match_id, market, snapshot_type);

-- Histórico de Elo (una fila por equipo tras cada partido)
CREATE TABLE elo_history (
    id              BIGSERIAL PRIMARY KEY,
    team_id         INTEGER NOT NULL REFERENCES teams(id),
    match_id        INTEGER NOT NULL REFERENCES matches(id),
    elo_before      NUMERIC(7,2) NOT NULL,
    elo_after       NUMERIC(7,2) NOT NULL,
    opponent_elo    NUMERIC(7,2) NOT NULL,
    is_home         BOOLEAN NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, match_id)
);

-- Predicciones generadas por el modelo para cada mercado evaluado
CREATE TABLE predictions (
    id                  BIGSERIAL PRIMARY KEY,
    match_id            INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    model_version        VARCHAR(40) NOT NULL,
    market              VARCHAR(30) NOT NULL,
    selection           VARCHAR(20) NOT NULL,
    model_probability   NUMERIC(6,4) NOT NULL CHECK (model_probability BETWEEN 0 AND 1),
    bookmaker_odds      NUMERIC(6,3) NOT NULL,
    implied_probability NUMERIC(6,4) NOT NULL,
    expected_value      NUMERIC(6,4) NOT NULL,      -- (model_prob * odds) - 1
    kelly_fraction      NUMERIC(6,4),                -- Kelly fraccional sugerido (ej. 0.25x)
    historical_accuracy NUMERIC(5,4),                -- accuracy del backtest para este mercado/modelo
    is_pick_of_day      BOOLEAN NOT NULL DEFAULT false,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (match_id, model_version, market, selection)
);
CREATE INDEX idx_predictions_pick_of_day ON predictions (is_pick_of_day, generated_at);

-- Resultados de backtesting por versión de modelo/mercado/temporada
CREATE TABLE backtest_results (
    id              BIGSERIAL PRIMARY KEY,
    model_version   VARCHAR(40) NOT NULL,
    market          VARCHAR(30) NOT NULL,
    season_start    VARCHAR(9) NOT NULL,
    season_end      VARCHAR(9) NOT NULL,
    n_bets          INTEGER NOT NULL,
    accuracy        NUMERIC(5,4),
    log_loss        NUMERIC(8,5),
    brier_score     NUMERIC(8,5),
    yield_pct       NUMERIC(6,3),                   -- (profit / total_staked) * 100
    roi_pct         NUMERIC(6,3),
    max_drawdown_pct NUMERIC(6,3),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lesiones/sanciones a nivel jugador (alimenta injuries_count / key_players_out)
CREATE TABLE injuries (
    id              BIGSERIAL PRIMARY KEY,
    team_id         INTEGER NOT NULL REFERENCES teams(id),
    player_name     VARCHAR(120) NOT NULL,
    status          VARCHAR(20) NOT NULL,           -- 'INJURED','SUSPENDED','DOUBTFUL'
    impact_score    SMALLINT DEFAULT 1,             -- 1=bajo .. 5=titular clave
    reported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expected_return DATE
);
CREATE INDEX idx_injuries_team ON injuries (team_id, reported_at);
