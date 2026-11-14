import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import strava
from .models import AppSettings, Ride, StravaToken, SyncLog, get_settings
from .savings import apply_rates, recompute

log = logging.getLogger(__name__)

EBIKE_SPORT_TYPES = {"EBikeRide", "EMountainBikeRide"}
# Incremental syncs re-read this much history so renamed or edited
# activities get picked up.
OVERLAP = timedelta(days=14)


class NotConnected(Exception):
    """No Strava account has been authorized yet."""


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def save_token(db: Session, payload: dict) -> StravaToken:
    token = db.get(StravaToken, 1) or StravaToken(id=1)
    token.access_token = payload["access_token"]
    token.refresh_token = payload["refresh_token"]
    token.expires_at = payload["expires_at"]
    if payload.get("athlete"):
        token.athlete_id = payload["athlete"]["id"]
    db.add(token)
    db.commit()
    return token


def get_access_token(db: Session, client: httpx.Client) -> str:
    token = db.get(StravaToken, 1)
    if token is None:
        raise NotConnected("Strava is not connected. Visit /auth/strava/login first.")
    if token.expires_at - 60 < time.time():
        token = save_token(db, strava.refresh_token(client, token.refresh_token))
    return token.access_token


def is_ebike_ride(activity: dict, settings: AppSettings) -> bool:
    title_match = settings.title_match.strip().lower()
    if title_match and title_match in activity.get("name", "").lower():
        return True
    sport = activity.get("sport_type") or activity.get("type")
    return settings.match_sport_type and sport in EBIKE_SPORT_TYPES


def run_sync(db: Session, client: httpx.Client, full: bool = False) -> SyncLog:
    """Pull e-bike rides from Strava. Raises NotConnected if never authorized;
    any other failure is recorded on the returned SyncLog."""
    access_token = get_access_token(db, client)
    settings = get_settings(db)

    entry = SyncLog(started_at=utcnow())
    db.add(entry)
    db.commit()

    try:
        after = None
        if not full:
            latest = db.scalar(select(func.max(Ride.start_date)).where(Ride.source == "strava"))
            if latest is not None:
                after = int((latest - OVERLAP).replace(tzinfo=UTC).timestamp())

        for activity in strava.list_activities(client, access_token, after):
            entry.fetched += 1
            if not is_ebike_ride(activity, settings):
                continue
            ride = db.scalar(select(Ride).where(Ride.strava_id == activity["id"]))
            is_new = ride is None
            if is_new:
                ride = Ride(strava_id=activity["id"], source="strava")
                db.add(ride)
            before = (ride.name, ride.distance_m)
            ride.name = activity["name"]
            ride.sport_type = activity.get("sport_type") or activity.get("type")
            ride.start_date = parse_ts(activity["start_date"])
            ride.start_date_local = parse_ts(activity.get("start_date_local", activity["start_date"]))
            ride.distance_m = activity["distance"]
            ride.moving_time_s = activity.get("moving_time")
            ride.elevation_gain_m = activity.get("total_elevation_gain")
            if is_new:
                apply_rates(ride, settings)
                entry.added += 1
            elif before != (ride.name, ride.distance_m):
                recompute(ride)  # keep the rates it was first recorded with
                entry.updated += 1
        entry.status = "ok"
    except Exception as exc:
        db.rollback()
        log.exception("Strava sync failed")
        entry = db.merge(entry)
        entry.status = "error"
        entry.error = f"{type(exc).__name__}: {exc}"

    entry.finished_at = utcnow()
    db.commit()
    return entry
