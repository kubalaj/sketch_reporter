import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import METERS_PER_MILE, Ride, StravaToken, get_settings
from app.savings import apply_rates
from app.sync import NotConnected, run_sync


def activity(id, name, miles, sport="Ride", date="2026-06-01T15:00:00Z"):
    return {
        "id": id,
        "name": name,
        "sport_type": sport,
        "start_date": date,
        "start_date_local": date,
        "distance": miles * METERS_PER_MILE,
        "moving_time": 1800,
        "total_elevation_gain": 50.0,
    }


class FakeStrava:
    """Stands in for Strava's HTTP API."""

    def __init__(self, activities):
        self.activities = activities
        self.refreshes = 0
        self.activity_calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            self.refreshes += 1
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh",
                    "refresh_token": "r2",
                    "expires_at": int(time.time()) + 21600,
                },
            )
        assert request.headers["Authorization"].startswith("Bearer ")
        params = request.url.params
        self.activity_calls.append(dict(params))
        page, per_page = int(params["page"]), 2  # tiny pages to exercise paging
        after = int(params.get("after", 0))
        matching = [a for a in self.activities if _epoch(a["start_date"]) > after]
        return httpx.Response(200, json=matching[(page - 1) * per_page : page * per_page])

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _epoch(ts):
    from datetime import datetime

    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def connect(db, expires_in=3600):
    db.add(
        StravaToken(
            id=1, access_token="a", refresh_token="r", expires_at=int(time.time()) + expires_in
        )
    )
    db.commit()


def test_savings_math(db):
    settings = get_settings(db)  # 25 mpg, $3.50/gal, $0.10/mi, 8.887 kg/gal
    ride = Ride(distance_m=10 * METERS_PER_MILE)
    apply_rates(ride, settings)
    assert ride.gas_saved == pytest.approx(1.40)
    assert ride.wear_saved == pytest.approx(1.00)
    assert ride.total_saved == pytest.approx(2.40)
    assert ride.co2_saved_kg == pytest.approx(3.5548)


def test_sync_requires_connection(db):
    with pytest.raises(NotConnected):
        run_sync(db, FakeStrava([]).client())


def test_sync_filters_pages_and_is_idempotent(db):
    connect(db)
    fake = FakeStrava(
        [
            activity(1, "Morning E-Bike Ride", 5, sport="EBikeRide"),
            activity(2, "Morning Run", 3, sport="Run"),
            activity(3, "e-bike ride to work", 8, sport="Ride"),  # matched by title
            activity(4, "Commute", 4, sport="EBikeRide"),  # matched by sport type
            activity(5, "Road ride", 30, sport="Ride"),
        ]
    )
    entry = run_sync(db, fake.client())
    assert (entry.status, entry.fetched, entry.added) == ("ok", 5, 3)
    assert {r.strava_id for r in db.query(Ride)} == {1, 3, 4}

    again = run_sync(db, fake.client())
    assert (again.added, again.updated) == (0, 0)
    assert db.query(Ride).count() == 3
    assert "after" in fake.activity_calls[-1]  # incremental the second time


def test_sync_refreshes_expired_token(db):
    connect(db, expires_in=-10)
    fake = FakeStrava([activity(1, "E-Bike Ride", 5)])
    assert run_sync(db, fake.client()).added == 1
    assert fake.refreshes == 1
    assert db.get(StravaToken, 1).access_token == "fresh"


def test_edited_ride_keeps_its_original_rates(db):
    connect(db)
    fake = FakeStrava([activity(1, "E-Bike Ride", 10)])
    run_sync(db, fake.client())

    get_settings(db).gas_price = 5.00
    db.commit()
    fake.activities[0]["distance"] = 20 * METERS_PER_MILE
    fake.activities.append(activity(2, "E-Bike Ride", 10, date="2026-06-02T15:00:00Z"))
    entry = run_sync(db, fake.client())
    assert (entry.added, entry.updated) == (1, 1)

    old, new = db.query(Ride).order_by(Ride.strava_id).all()
    assert old.gas_saved == pytest.approx(2.80)  # 20 mi at the original $3.50
    assert new.gas_saved == pytest.approx(2.00)  # 10 mi at $5.00


def test_sync_failure_is_logged(db):
    connect(db)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    entry = run_sync(db, client)
    assert entry.status == "error" and "429" in entry.error


def test_api_flow():
    with TestClient(app) as api:
        assert api.post("/api/sync").status_code == 409
        assert api.get("/api/summary").json()["rides"] == 0

        for day, miles in (("2026-05-01", 10), ("2026-06-01", 20)):
            resp = api.post(
                "/api/rides",
                json={"start_date_local": f"{day}T08:00:00", "distance_miles": miles},
            )
            assert resp.status_code == 201
        ride_id = resp.json()["id"]

        summary = api.get("/api/summary").json()
        assert summary["rides"] == 2
        assert summary["miles"] == 30
        assert summary["total_saved"] == pytest.approx(7.20)
        assert summary["remaining"] == pytest.approx(3292.80)
        assert summary["paid_off_pct"] == pytest.approx(0.22)
        assert summary["projected_payoff_date"] is not None

        months = api.get("/api/summary/timeseries").json()
        assert [m["period"] for m in months] == ["2026-05", "2026-06"]
        assert months[1]["cumulative_saved"] == pytest.approx(7.20)

        api.patch(f"/api/rides/{ride_id}", json={"excluded": True})
        assert api.get("/api/summary").json()["total_saved"] == pytest.approx(2.40)
        api.patch(f"/api/rides/{ride_id}", json={"excluded": False})

        assert api.patch("/api/settings", json={"gas_price": 7.0}).json()["gas_price"] == 7.0
        assert api.get("/api/summary").json()["total_saved"] == pytest.approx(7.20)
        assert api.post("/api/rides/recalculate").json() == {"recalculated": 2}
        assert api.get("/api/summary").json()["total_saved"] == pytest.approx(11.40)

        assert api.patch("/api/settings", json={"mpg": 0}).status_code == 422
        assert api.delete(f"/api/rides/{ride_id}").status_code == 204


def test_oauth_login_and_bad_state():
    with TestClient(app) as api:
        resp = api.get("/auth/strava/login", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers["location"]
        assert "client_id=123" in location and "activity%3Aread_all" in location
        assert api.get("/auth/strava/callback?state=nope&code=x").status_code == 400
