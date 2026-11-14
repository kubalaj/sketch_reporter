from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from .config import config
from .db import Base

METERS_PER_MILE = 1609.344


class Ride(Base):
    __tablename__ = "rides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strava_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    source: Mapped[str] = mapped_column(String(16), default="strava")  # strava | manual
    name: Mapped[str] = mapped_column(String(255))
    sport_type: Mapped[str | None] = mapped_column(String(64))
    start_date: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC
    start_date_local: Mapped[datetime] = mapped_column(DateTime)
    distance_m: Mapped[float] = mapped_column(Float)
    moving_time_s: Mapped[int | None] = mapped_column(Integer)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    # Excluded rides stay in the table but don't count toward totals
    # (e.g. a joyride that didn't replace a car trip).
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)

    # Rates locked in when the ride was first recorded, so changing the gas
    # price later doesn't rewrite history.
    mpg: Mapped[float] = mapped_column(Float)
    gas_price: Mapped[float] = mapped_column(Float)
    wear_per_mile: Mapped[float] = mapped_column(Float)
    co2_kg_per_gallon: Mapped[float] = mapped_column(Float)

    gas_saved: Mapped[float] = mapped_column(Float)
    wear_saved: Mapped[float] = mapped_column(Float)
    co2_saved_kg: Mapped[float] = mapped_column(Float)

    @property
    def distance_miles(self) -> float:
        return self.distance_m / METERS_PER_MILE

    @property
    def total_saved(self) -> float:
        return self.gas_saved + self.wear_saved


class AppSettings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mpg: Mapped[float] = mapped_column(Float)
    gas_price: Mapped[float] = mapped_column(Float)
    wear_per_mile: Mapped[float] = mapped_column(Float)
    co2_kg_per_gallon: Mapped[float] = mapped_column(Float)
    bike_cost: Mapped[float] = mapped_column(Float)
    # Case-insensitive substring an activity title must contain.
    title_match: Mapped[str] = mapped_column(String(255))
    # Also accept anything Strava itself classifies as an e-bike ride.
    match_sport_type: Mapped[bool] = mapped_column(Boolean, default=True)


class StravaToken(Base):
    __tablename__ = "strava_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    athlete_id: Mapped[int | None] = mapped_column(BigInteger)
    access_token: Mapped[str] = mapped_column(String(255))
    refresh_token: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[int] = mapped_column(Integer)  # epoch seconds


class SyncLog(Base):
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


def get_settings(db: Session) -> AppSettings:
    settings = db.get(AppSettings, 1)
    if settings is None:
        settings = AppSettings(
            id=1,
            mpg=config.default_mpg,
            gas_price=config.default_gas_price,
            wear_per_mile=config.default_wear_per_mile,
            co2_kg_per_gallon=config.default_co2_kg_per_gallon,
            bike_cost=config.default_bike_cost,
            title_match=config.default_title_match,
            match_sport_type=True,
        )
        db.add(settings)
        db.commit()
    return settings
