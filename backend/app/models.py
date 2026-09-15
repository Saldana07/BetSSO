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
    home_corners: Mapped[int | None] = mapped_column(SmallInteger)
    away_corners: Mapped[int | None] = mapped_column(SmallInteger)
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
    is_pick_of_day: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime]

    # --- Liquidación (la llena jobs/settle_predictions.py cuando el partido termina) ---
    # Sin esto es imposible responder "¿este sistema realmente gana dinero?":
    # el backtest mide el pasado simulado, esto mide el desempeño real.
    settled_at: Mapped[datetime | None]
    is_win: Mapped[bool | None]
    profit_loss: Mapped[float | None] = mapped_column(Numeric(8, 3))  # a stake plano de 1 unidad

    match: Mapped["Match"] = relationship(back_populates="predictions")


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_version: Mapped[str] = mapped_column(String(40))
    market: Mapped[str] = mapped_column(String(30))
    season_start: Mapped[str] = mapped_column(String(9))
    season_end: Mapped[str] = mapped_column(String(9))
    n_bets: Mapped[int]
    accuracy: Mapped[float | None] = mapped_column(Numeric(5, 4))
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
