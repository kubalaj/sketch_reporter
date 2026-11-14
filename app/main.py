import logging
import secrets
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Literal

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import strava
from .config import config
from .db import SessionLocal, get_db, init_db
from .models import METERS_PER_MILE, Ride, StravaToken, SyncLog, get_settings
from .savings import apply_rates
from .sync import NotConnected, run_sync, save_token, utcnow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

KG_PER_LB = 0.45359237


def http_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def scheduled_sync() -> None:
    try:
        with SessionLocal() as db, http_client() as client:
            entry = run_sync(db, client)
        log.info("Scheduled sync %s: %d added, %d updated", entry.status, entry.added, entry.updated)
    except NotConnected:
        log.warning("Scheduled sync skipped: Strava is not connected yet")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = None
    if config.sync_cron.strip():
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(scheduled_sync, CronTrigger.from_crontab(config.sync_cron, timezone="UTC"))
        scheduler.start()
        log.info("Strava sync scheduled: %s (UTC)", config.sync_cron)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="E-Bike Savings", lifespan=lifespan)


# ---------- schemas ----------


class RideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strava_id: int | None
    source: str
    name: str
    sport_type: str | None
    start_date: datetime
    start_date_local: datetime
    distance_miles: float
    moving_time_s: int | None
    elevation_gain_m: float | None
    excluded: bool
    mpg: float
    gas_price: float
    wear_per_mile: float
    gas_saved: float
    wear_saved: float
    total_saved: float
    co2_saved_kg: float


class RideCreate(BaseModel):
    name: str = "E-Bike Ride"
    start_date_local: datetime
    distance_miles: float = Field(gt=0)


class RidePatch(BaseModel):
    excluded: bool


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mpg: float
    gas_price: float
    wear_per_mile: float
    co2_kg_per_gallon: float
    bike_cost: float
    title_match: str
    match_sport_type: bool


class SettingsPatch(BaseModel):
    mpg: float | None = Field(default=None, gt=0)
    gas_price: float | None = Field(default=None, ge=0)
    wear_per_mile: float | None = Field(default=None, ge=0)
    co2_kg_per_gallon: float | None = Field(default=None, ge=0)
    bike_cost: float | None = Field(default=None, ge=0)
    title_match: str | None = None
    match_sport_type: bool | None = None


class SyncOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    fetched: int
    added: int
    updated: int
    error: str | None


# ---------- helpers ----------


def counted_rides(db: Session) -> list[Ride]:
    return list(db.scalars(select(Ride).where(Ride.excluded.is_(False)).order_by(Ride.start_date)))


def totals(rides: list[Ride]) -> dict:
    co2 = sum(r.co2_saved_kg for r in rides)
    gas = sum(r.gas_saved for r in rides)
    wear = sum(r.wear_saved for r in rides)
    return {
        "rides": len(rides),
        "miles": round(sum(r.distance_m for r in rides) / METERS_PER_MILE, 2),
        "gas_saved": round(gas, 2),
        "wear_saved": round(wear, 2),
        "total_saved": round(gas + wear, 2),
        "co2_saved_kg": round(co2, 2),
        "co2_saved_lbs": round(co2 / KG_PER_LB, 2),
    }


# ---------- Strava auth ----------

_oauth_states: set[str] = set()


@app.get("/auth/strava/login", include_in_schema=False)
def strava_login():
    if not (config.strava_client_id and config.strava_client_secret):
        raise HTTPException(500, "STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET are not set")
    state = secrets.token_urlsafe(16)
    _oauth_states.add(state)
    return RedirectResponse(strava.authorize_url(state))


@app.get("/auth/strava/callback", include_in_schema=False)
def strava_callback(
    state: str = "",
    code: str | None = None,
    scope: str = "",
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if state not in _oauth_states:
        raise HTTPException(400, "Unknown OAuth state. Start again at /auth/strava/login")
    _oauth_states.discard(state)
    if error or not code:
        raise HTTPException(400, f"Strava authorization failed: {error or 'no code returned'}")
    if "activity:read" not in scope:
        raise HTTPException(400, "Activity access was not granted. Start again and leave it checked.")
    with http_client() as client:
        save_token(db, strava.exchange_code(client, code))
    return {"connected": True, "next": "POST /api/sync to import your rides"}


@app.get("/api/status")
def status(db: Session = Depends(get_db)):
    token = db.get(StravaToken, 1)
    last = db.scalar(select(SyncLog).order_by(SyncLog.id.desc()).limit(1))
    return {
        "strava_connected": token is not None,
        "athlete_id": token.athlete_id if token else None,
        "sync_cron": config.sync_cron or None,
        "last_sync": SyncOut.model_validate(last) if last else None,
    }


# ---------- sync ----------


@app.post("/api/sync", response_model=SyncOut)
def sync_now(full: bool = False, db: Session = Depends(get_db)):
    """Pull rides from Strava now. `full=true` re-reads your entire history."""
    try:
        with http_client() as client:
            entry = run_sync(db, client, full=full)
    except NotConnected as exc:
        raise HTTPException(409, str(exc))
    if entry.status == "error":
        raise HTTPException(502, entry.error)
    return entry


@app.get("/api/syncs", response_model=list[SyncOut])
def sync_history(limit: int = Query(20, le=200), db: Session = Depends(get_db)):
    return db.scalars(select(SyncLog).order_by(SyncLog.id.desc()).limit(limit)).all()


# ---------- rides ----------


@app.get("/api/rides", response_model=list[RideOut])
def list_rides(
    limit: int = Query(100, le=1000),
    offset: int = 0,
    include_excluded: bool = True,
    db: Session = Depends(get_db),
):
    query = select(Ride).order_by(Ride.start_date.desc()).limit(limit).offset(offset)
    if not include_excluded:
        query = query.where(Ride.excluded.is_(False))
    return db.scalars(query).all()


@app.post("/api/rides", response_model=RideOut, status_code=201)
def add_manual_ride(body: RideCreate, db: Session = Depends(get_db)):
    """Log a ride that isn't on Strava."""
    when = body.start_date_local.replace(tzinfo=None)
    ride = Ride(
        source="manual",
        name=body.name,
        start_date=when,
        start_date_local=when,
        distance_m=body.distance_miles * METERS_PER_MILE,
    )
    apply_rates(ride, get_settings(db))
    db.add(ride)
    db.commit()
    return ride


@app.post("/api/rides/recalculate")
def recalculate(db: Session = Depends(get_db)):
    """Re-price every ride at the current settings, replacing the rates each
    ride was originally recorded with."""
    settings = get_settings(db)
    rides = db.scalars(select(Ride)).all()
    for ride in rides:
        apply_rates(ride, settings)
    db.commit()
    return {"recalculated": len(rides)}


@app.patch("/api/rides/{ride_id}", response_model=RideOut)
def patch_ride(ride_id: int, body: RidePatch, db: Session = Depends(get_db)):
    ride = db.get(Ride, ride_id)
    if ride is None:
        raise HTTPException(404, "Ride not found")
    ride.excluded = body.excluded
    db.commit()
    return ride


@app.delete("/api/rides/{ride_id}", status_code=204)
def delete_ride(ride_id: int, db: Session = Depends(get_db)):
    ride = db.get(Ride, ride_id)
    if ride is None:
        raise HTTPException(404, "Ride not found")
    if ride.source != "manual":
        raise HTTPException(400, "Strava rides come back on the next sync; exclude it instead")
    db.delete(ride)
    db.commit()


# ---------- settings ----------


@app.get("/api/settings", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db)):
    return get_settings(db)


@app.patch("/api/settings", response_model=SettingsOut)
def update_settings(body: SettingsPatch, db: Session = Depends(get_db)):
    """New values apply to rides recorded from now on. Use
    POST /api/rides/recalculate to apply them to past rides as well."""
    settings = get_settings(db)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(settings, key, value)
    db.commit()
    return settings


# ---------- summary ----------


@app.get("/api/summary")
def summary(db: Session = Depends(get_db)):
    rides = counted_rides(db)
    settings = get_settings(db)
    result = totals(rides)
    saved = result["total_saved"]
    remaining = max(settings.bike_cost - saved, 0)

    projected = None
    if rides and saved > 0 and remaining > 0:
        days = max((utcnow() - rides[0].start_date).days, 1)
        projected = (utcnow() + timedelta(days=remaining / (saved / days))).date()

    result.update(
        bike_cost=settings.bike_cost,
        remaining=round(remaining, 2),
        paid_off_pct=round(100 * saved / settings.bike_cost, 2) if settings.bike_cost else None,
        avg_saved_per_ride=round(saved / len(rides), 2) if rides else 0,
        first_ride=rides[0].start_date_local if rides else None,
        latest_ride=rides[-1].start_date_local if rides else None,
        # Straight-line projection from your average savings per day so far.
        projected_payoff_date=projected,
    )
    return result


@app.get("/api/summary/timeseries")
def timeseries(
    bucket: Literal["day", "week", "month", "year"] = "month", db: Session = Depends(get_db)
):
    """Per-period totals plus a running total, oldest first."""
    formats = {"day": "%Y-%m-%d", "week": "%G-W%V", "month": "%Y-%m", "year": "%Y"}
    groups: dict[str, list[Ride]] = defaultdict(list)
    for ride in counted_rides(db):
        groups[ride.start_date_local.strftime(formats[bucket])].append(ride)

    out, running_saved, running_co2 = [], 0.0, 0.0
    for period in sorted(groups):
        row = totals(groups[period])
        running_saved += row["total_saved"]
        running_co2 += row["co2_saved_kg"]
        out.append(
            {
                "period": period,
                **row,
                "cumulative_saved": round(running_saved, 2),
                "cumulative_co2_kg": round(running_co2, 2),
            }
        )
    return out
