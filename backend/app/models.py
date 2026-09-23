from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_league_id: Mapped[int | None] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(80))
    tier: Mapped[int] = mapped_column(SmallInteger, default=1)

    teams: Mapped[list["Team"]] = relationship(back_populates="league")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_team_id: Mapped[int | None] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(120))
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id"))
    country: Mapped[str | None] = mapped_column(String(80))
    elo_rating: Mapped[float] = mapped_column(Numeric(7, 2), default=1500.0)
    elo_updated_at: Mapped[datetime | None]

    league: Mapped["League"] = relationship(back_populates="teams")


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (CheckConstraint("home_team_id <> away_team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    api_match_id: Mapped[int | None] = mapped_column(unique=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"))
    season: Mapped[str] = mapped_column(String(9))
    round: Mapped[str | None] = mapped_column(String(50))
    kickoff_utc: Mapped[datetime]
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    home_goals: Mapped[int | None] = mapped_column(SmallInteger)
    away_goals: Mapped[int | None] = mapped_column(SmallInteger)
    home_xg: Mapped[float | None] = mapped_column(Numeric(4, 2))
    away_xg: Mapped[float | None] = mapped_column(Numeric(4, 2))
    # Estadísticas del partido para liquidar mercados de conteo (córners,
    # tiros, tiros a puerta, tarjetas, faltas). Las trae settle_predictions
    # de /fixtures/statistics solo para partidos con picks de esos mercados.
    home_corners: Mapped[int | None] = mapped_column(SmallInteger)
    away_corners: Mapped[int | None] = mapped_column(SmallInteger)
    home_shots: Mapped[int | None] = mapped_column(SmallInteger)
    away_shots: Mapped[int | None] = mapped_column(SmallInteger)
    home_shots_on_target: Mapped[int | None] = mapped_column(SmallInteger)
    away_shots_on_target: Mapped[int | None] = mapped_column(SmallInteger)
    home_yellow: Mapped[int | None] = mapped_column(SmallInteger)
    away_yellow: Mapped[int | None] = mapped_column(SmallInteger)
    home_red: Mapped[int | None] = mapped_column(SmallInteger)
    away_red: Mapped[int | None] = mapped_column(SmallInteger)
    home_fouls: Mapped[int | None] = mapped_column(SmallInteger)
    away_fouls: Mapped[int | None] = mapped_column(SmallInteger)
    # Última vez que se pidieron resultado + estadísticas por ID. Evita volver a
    # pagar la petición por partidos cuya API no publica estadísticas.
    stats_fetched_at: Mapped[datetime | None]
    referee: Mapped[str | None] = mapped_column(String(120))
    venue: Mapped[str | None] = mapped_column(String(120))
    ext_prob_home: Mapped[float | None] = mapped_column(Numeric(6, 4))
    ext_prob_draw: Mapped[float | None] = mapped_column(Numeric(6, 4))
    ext_prob_away: Mapped[float | None] = mapped_column(Numeric(6, 4))

    league: Mapped["League"] = relationship()
    home_team: Mapped["Team"] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped["Team"] = relationship(foreign_keys=[away_team_id])
    stats: Mapped[list["TeamMatchStats"]] = relationship(back_populates="match")
    odds: Mapped[list["Odds"]] = relationship(back_populates="match")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="match")

    def stat_totals(self) -> dict[str, int | None]:
        """Totales del partido por estadística, con None donde falten datos.
        Tarjetas = amarillas + 2×rojas (convención de las casas y del modelo)."""
        def total(h, a):
            return None if h is None or a is None else h + a
        cards = None
        if None not in (self.home_yellow, self.away_yellow, self.home_red, self.away_red):
            cards = self.home_yellow + self.away_yellow + 2 * (self.home_red + self.away_red)
        return {
            "corners": total(self.home_corners, self.away_corners),
            "shots": total(self.home_shots, self.away_shots),
            "shots_on_target": total(self.home_shots_on_target, self.away_shots_on_target),
            "cards": cards,
            "fouls": total(self.home_fouls, self.away_fouls),
        }


class TeamMatchStats(Base):
    __tablename__ = "team_match_stats"
    __table_args__ = (UniqueConstraint("match_id", "team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    is_home: Mapped[bool]
    shots: Mapped[int | None] = mapped_column(SmallInteger)
    shots_on_target: Mapped[int | None] = mapped_column(SmallInteger)
    possession_pct: Mapped[float | None] = mapped_column(Numeric(4, 1))
    corners: Mapped[int | None] = mapped_column(SmallInteger)
    xg: Mapped[float | None] = mapped_column(Numeric(4, 2))
    xga: Mapped[float | None] = mapped_column(Numeric(4, 2))
    injuries_count: Mapped[int] = mapped_column(SmallInteger, default=0)
    key_players_out: Mapped[int] = mapped_column(SmallInteger, default=0)

    match: Mapped["Match"] = relationship(back_populates="stats")


class Odds(Base):
    __tablename__ = "odds"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    bookmaker: Mapped[str] = mapped_column(String(60))
    market: Mapped[str] = mapped_column(String(30))
    selection: Mapped[str] = mapped_column(String(20))
    odds_value: Mapped[float] = mapped_column(Numeric(6, 3))
    snapshot_type: Mapped[str] = mapped_column(String(10))  # OPENING | LIVE | CLOSING
    recorded_at: Mapped[datetime]

    match: Mapped["Match"] = relationship(back_populates="odds")


class EloHistory(Base):
    __tablename__ = "elo_history"
    __table_args__ = (UniqueConstraint("team_id", "match_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    elo_before: Mapped[float] = mapped_column(Numeric(7, 2))
    elo_after: Mapped[float] = mapped_column(Numeric(7, 2))
    opponent_elo: Mapped[float] = mapped_column(Numeric(7, 2))
    is_home: Mapped[bool]


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("match_id", "model_version", "market", "selection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    model_version: Mapped[str] = mapped_column(String(40))
    market: Mapped[str] = mapped_column(String(30))
    selection: Mapped[str] = mapped_column(String(20))
    model_probability: Mapped[float] = mapped_column(Numeric(6, 4))
    # Casa de apuestas que ofrecía esta cuota. Sin esto el pick es inaccionable:
    # la cuota es la MEJOR entre varias casas, y el usuario no sabría dónde ir.
    bookmaker: Mapped[str | None] = mapped_column(String(60))
    bookmaker_odds: Mapped[float] = mapped_column(Numeric(6, 3))
    implied_probability: Mapped[float] = mapped_column(Numeric(6, 4))
    expected_value: Mapped[float] = mapped_column(Numeric(6, 4))
    kelly_fraction: Mapped[float | None] = mapped_column(Numeric(6, 4))
    historical_accuracy: Mapped[float | None] = mapped_column(Numeric(5, 4))
    # Foto de la evidencia de habilidad al emitir el pick. La etiqueta de
    # confianza usa historical_skill (= accuracy - base), no accuracy cruda.
    historical_skill: Mapped[float | None] = mapped_column(Numeric(6, 4))
    backtest_n_bets: Mapped[int | None]
    is_pick_of_day: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime]

    # --- Liquidación (la llena jobs/settle_predictions.py cuando el partido termina) ---
    # Sin esto es imposible responder "¿este sistema realmente gana dinero?":
    # el backtest mide el pasado simulado, esto mide el desempeño real.
    settled_at: Mapped[datetime | None]
    is_win: Mapped[bool | None]
    profit_loss: Mapped[float | None] = mapped_column(Numeric(8, 3))  # a stake plano de 1 unidad
    # Mejor cuota de cierre para la misma selección. CLV = bookmaker_odds / closing_odds - 1.
    closing_odds: Mapped[float | None] = mapped_column(Numeric(6, 3))
    closing_bookmaker: Mapped[str | None] = mapped_column(String(60))

    match: Mapped["Match"] = relationship(back_populates="predictions")


class MarketEvaluation(Base):
    """TODOS los mercados evaluados de cada partido, no solo los picks.

    Existe para responder la pregunta que el backtest contra la tasa base no
    puede: ¿el modelo le gana a la CASA? Con 3-5 picks al día harían falta
    meses; guardando cada mercado con cuota (y liquidándolo) hay cientos de
    comparaciones por semana. `market_fair_probability` es la probabilidad de
    la casa sin su margen (mediana entre casas con ambos lados publicados):
    si el Brier del modelo no baja del Brier de esa columna, el modelo no
    sabe nada que la casa no sepa, y el EV que muestra es ilusión.
    """

    __tablename__ = "market_evaluations"
    __table_args__ = (
        UniqueConstraint("match_id", "model_version", "market", "selection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(40))
    market: Mapped[str] = mapped_column(String(30))
    selection: Mapped[str] = mapped_column(String(20))
    model_probability: Mapped[float] = mapped_column(Numeric(6, 4))
    market_fair_probability: Mapped[float | None] = mapped_column(Numeric(6, 4))
    n_bookmakers: Mapped[int] = mapped_column(SmallInteger, default=0)
    best_odds: Mapped[float] = mapped_column(Numeric(6, 3))
    best_bookmaker: Mapped[str | None] = mapped_column(String(60))
    expected_value: Mapped[float] = mapped_column(Numeric(8, 4))
    validation: Mapped[str] = mapped_column(String(12))   # VALIDADO | SIN_VALIDAR | SIN_VENTAJA
    evaluated_at: Mapped[datetime]
    settled_at: Mapped[datetime | None]
    is_win: Mapped[bool | None]

    match: Mapped["Match"] = relationship()


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_version: Mapped[str] = mapped_column(String(40))
    market: Mapped[str] = mapped_column(String(30))
    season_start: Mapped[str] = mapped_column(String(9))
    season_end: Mapped[str] = mapped_column(String(9))
    n_bets: Mapped[int]
    accuracy: Mapped[float | None] = mapped_column(Numeric(5, 4))
    base_accuracy: Mapped[float | None] = mapped_column(Numeric(5, 4))
    log_loss: Mapped[float | None] = mapped_column(Numeric(8, 5))
    brier_score: Mapped[float | None] = mapped_column(Numeric(8, 5))
    yield_pct: Mapped[float | None] = mapped_column(Numeric(6, 3))
    roi_pct: Mapped[float | None] = mapped_column(Numeric(6, 3))
    max_drawdown_pct: Mapped[float | None] = mapped_column(Numeric(6, 3))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Injury(Base):
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    player_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20))
    impact_score: Mapped[int] = mapped_column(SmallInteger, default=1)
    expected_return: Mapped[Date | None] = mapped_column(Date)
