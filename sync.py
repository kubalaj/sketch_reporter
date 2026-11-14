#!/usr/bin/env python3
"""Pull e-bike rides from Strava and write data/rides.json.

    python3 sync.py --auth      one-time: connect your Strava account
    python3 sync.py             fetch new rides and update data/rides.json
    python3 sync.py --full      re-read your whole Strava history
    python3 sync.py --reprice   re-price every ride at the rates in config.json
                                and the gas price for the week of each ride

Uses only the Python standard library.
"""

import argparse
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
DATA_FILE = ROOT / "data" / "rides.json"
ENV_FILE = ROOT / ".env"
TOKEN_FILE = ROOT / ".strava_token.json"  # local only, never committed

AUTH_PORT = 8000
REDIRECT_URI = f"http://localhost:{AUTH_PORT}/callback"
METERS_PER_MILE = 1609.344
KG_PER_LB = 0.45359237
EBIKE_SPORT_TYPES = {"EBikeRide", "EMountainBikeRide"}
RATE_KEYS = ("mpg", "wear_per_mile", "co2_kg_per_gallon")
# Each sync re-reads this much history so renamed or edited rides are picked up.
OVERLAP = timedelta(days=14)


class ApiError(Exception):
    pass


# ---------- Strava ----------


def load_env():
    """Read KEY=VALUE lines from .env without overriding real environment variables."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


def credentials():
    try:
        return os.environ["STRAVA_CLIENT_ID"], os.environ["STRAVA_CLIENT_SECRET"]
    except KeyError as missing:
        sys.exit(f"{missing.args[0]} is not set. Put it in .env (see README).")


def request_json(url, form=None, token=None):
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request(url, data=data)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    # Shown in errors: the request without any API key in the query string.
    where = f"{'POST' if data else 'GET'} {re.sub(r'api_key=[^&]*', 'api_key=...', url)}"
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        raise ApiError(f"{where}\n  returned {err.code}: {err.read().decode()[:500]}")
    except (urllib.error.URLError, ValueError) as err:
        raise ApiError(f"{where}\n  failed: {err}")


def token_request(**grant):
    client_id, client_secret = credentials()
    return request_json(
        "https://www.strava.com/oauth/token",
        form={"client_id": client_id, "client_secret": client_secret, **grant},
    )


def authorize():
    """One-time browser login. Saves a refresh token to .strava_token.json."""
    client_id, _ = credentials()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state") == [state]:
                result.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and go back to the terminal.")

        def log_message(self, *args):
            pass

    url = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "read,activity:read_all",
            "state": state,
        }
    )
    server = HTTPServer(("localhost", AUTH_PORT), Handler)
    print(f"Opening Strava in your browser. If nothing opens, go to:\n\n{url}\n")
    webbrowser.open(url)
    while not result:
        server.handle_request()
    server.server_close()

    if "code" not in result or "activity:read" not in result.get("scope", ""):
        sys.exit("Strava did not grant activity access. Run --auth again and leave it checked.")
    token = token_request(grant_type="authorization_code", code=result["code"])
    TOKEN_FILE.write_text(json.dumps({"refresh_token": token["refresh_token"]}))
    print(f"Connected. Refresh token saved to {TOKEN_FILE.name}.")
    print("For GitHub Actions, add this as the STRAVA_REFRESH_TOKEN secret:\n")
    print(token["refresh_token"])


def get_access_token():
    in_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    if TOKEN_FILE.exists():
        refresh = json.loads(TOKEN_FILE.read_text())["refresh_token"]
    else:
        refresh = os.environ.get("STRAVA_REFRESH_TOKEN")
    if not refresh:
        sys.exit("Not connected to Strava yet. Run: python3 sync.py --auth")

    token = token_request(grant_type="refresh_token", refresh_token=refresh)
    if token["refresh_token"] != refresh:
        if in_ci:
            print(
                "::warning::Strava issued a new refresh token. Run `python3 sync.py --auth` "
                "locally and update the STRAVA_REFRESH_TOKEN secret, or the next sync will fail."
            )
        else:
            TOKEN_FILE.write_text(json.dumps({"refresh_token": token["refresh_token"]}))
    return token["access_token"]


def fetch_activities(access_token, after=None, per_page=200):
    page = 1
    while True:
        params = {"per_page": per_page, "page": page}
        if after is not None:
            params["after"] = after
        batch = request_json(
            "https://www.strava.com/api/v3/athlete/activities?" + urllib.parse.urlencode(params),
            token=access_token,
        )
        if not batch:
            return
        yield from batch
        page += 1


# ---------- gas prices ----------


def fetch_gas_prices(config):
    """Weekly pump prices from the U.S. Energy Information Administration,
    as a list of (week, dollars per gallon), oldest first.

    Returns an empty list when there is no API key or the lookup fails, in
    which case rides fall back to gas_price in config.json.
    """
    key = os.environ.get("EIA_API_KEY")
    if not key:
        print("No EIA_API_KEY set; using gas_price from config.json.")
        return []
    query = urllib.parse.urlencode(
        {
            "api_key": key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": config["gas_price_series"],
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 520,  # ten years of weeks
        }
    )
    try:
        rows = request_json(f"https://api.eia.gov/v2/petroleum/pri/gnd/data/?{query}")["response"]["data"]
        prices = sorted((row["period"], float(row["value"])) for row in rows if row.get("value"))
    except (ApiError, KeyError, TypeError, ValueError) as err:
        print(f"::warning::Gas price lookup failed, using gas_price from config.json. {err}")
        return []
    if not prices:
        print(f"::warning::No gas prices returned for series {config['gas_price_series']}.")
    return prices


def gas_price_on(gas_prices, day, fallback):
    """The weekly price in effect on a ride's date (YYYY-MM-DD...)."""
    if not gas_prices:
        return fallback
    earlier = [price for week, price in gas_prices if week <= day[:10]]
    return earlier[-1] if earlier else gas_prices[0][1]


# ---------- rides and savings ----------


def is_ebike_ride(activity, config):
    title_match = config["title_match"].strip().lower()
    if title_match and title_match in activity.get("name", "").lower():
        return True
    sport = activity.get("sport_type") or activity.get("type")
    return bool(config["match_sport_type"]) and sport in EBIKE_SPORT_TYPES


def set_rates(ride, config, gas_prices):
    ride.update({key: config[key] for key in RATE_KEYS})
    ride["gas_price"] = gas_price_on(gas_prices, ride["date"], config["gas_price"])


def price(ride):
    """Work out savings from the ride's miles and the rates stored on it."""
    gallons = ride["miles"] / ride["mpg"]
    ride["gas_saved"] = round(gallons * ride["gas_price"], 4)
    ride["wear_saved"] = round(ride["miles"] * ride["wear_per_mile"], 4)
    ride["total_saved"] = round(ride["gas_saved"] + ride["wear_saved"], 4)
    ride["co2_saved_kg"] = round(gallons * ride["co2_kg_per_gallon"], 4)


def merge(rides, activities, config, gas_prices=(), reprice=False):
    """Fold Strava activities into the existing ride list.

    A new ride gets the gas price for the week it happened and the other
    rates from today's config, and keeps them from then on (unless reprice
    is set). Returns (rides sorted oldest first, number added).
    """
    by_id = {ride["id"]: ride for ride in rides}
    added = 0
    for activity in activities:
        if not is_ebike_ride(activity, config):
            continue
        ride = by_id.get(activity["id"])
        is_new = ride is None
        if is_new:
            ride = by_id[activity["id"]] = {"id": activity["id"]}
            added += 1
        ride["name"] = activity["name"]
        ride["sport_type"] = activity.get("sport_type") or activity.get("type")
        ride["date"] = activity.get("start_date_local", activity["start_date"]).rstrip("Z")
        ride["miles"] = round(activity["distance"] / METERS_PER_MILE, 3)
        ride["moving_time_s"] = activity.get("moving_time")
        if is_new:
            set_rates(ride, config, gas_prices)

    excluded = set(config.get("exclude_ids", []))
    for ride in by_id.values():
        if reprice:
            set_rates(ride, config, gas_prices)
        ride["excluded"] = ride["id"] in excluded
        price(ride)
    return sorted(by_id.values(), key=lambda r: r["date"]), added


def summarize(rides, config, today=None):
    today = today or datetime.now().date()
    counted = [r for r in rides if not r["excluded"]]
    saved = sum(r["total_saved"] for r in counted)
    co2 = sum(r["co2_saved_kg"] for r in counted)
    bike_cost = config["bike_cost"]
    remaining = max(bike_cost - saved, 0)

    # Straight-line projection from average savings per day since the first ride.
    payoff = None
    if counted and saved > 0 and remaining > 0:
        days = max((today - datetime.fromisoformat(counted[0]["date"]).date()).days, 1)
        payoff = (today + timedelta(days=remaining / (saved / days))).isoformat()

    return {
        "rides": len(counted),
        "miles": round(sum(r["miles"] for r in counted), 2),
        "gas_saved": round(sum(r["gas_saved"] for r in counted), 2),
        "wear_saved": round(sum(r["wear_saved"] for r in counted), 2),
        "total_saved": round(saved, 2),
        "co2_saved_kg": round(co2, 2),
        "co2_saved_lbs": round(co2 / KG_PER_LB, 2),
        "bike_cost": bike_cost,
        "remaining": round(remaining, 2),
        "paid_off_pct": round(100 * saved / bike_cost, 2) if bike_cost else None,
        "projected_payoff_date": payoff,
    }


# ---------- main ----------


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--auth", action="store_true", help="connect your Strava account")
    parser.add_argument("--full", action="store_true", help="re-read all Strava history")
    parser.add_argument("--reprice", action="store_true", help="re-price all rides at current rates")
    parser.add_argument("--per-page", type=int, default=200, help="activities per request (max 200)")
    args = parser.parse_args()

    load_env()
    if args.auth:
        return authorize()

    config = json.loads(CONFIG_FILE.read_text())
    rides = json.loads(DATA_FILE.read_text())["rides"] if DATA_FILE.exists() else []

    after = None
    if rides and not args.full:
        latest = datetime.fromisoformat(max(r["date"] for r in rides)).replace(tzinfo=timezone.utc)
        after = int((latest - OVERLAP).timestamp())

    access_token = get_access_token()
    try:
        activities = list(fetch_activities(access_token, after, args.per_page))
    except ApiError as err:
        # Tell a bad login apart from a problem with the activity list itself.
        try:
            athlete = request_json("https://www.strava.com/api/v3/athlete", token=access_token)
            note = f"The login itself works (athlete id {athlete.get('id')})."
        except ApiError as profile_err:
            note = f"Reading your profile failed too:\n{profile_err}"
        raise ApiError(f"{err}\n{note}")
    gas_prices = fetch_gas_prices(config)
    rides, added = merge(rides, activities, config, gas_prices, reprice=args.reprice)
    summary = summarize(rides, config)

    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "gas_price": {
                    "source": f"EIA weekly series {config['gas_price_series']}" if gas_prices else "config.json",
                    "week": gas_prices[-1][0] if gas_prices else None,
                    "dollars_per_gallon": gas_prices[-1][1] if gas_prices else config["gas_price"],
                },
                "summary": summary,
                "rides": rides,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Checked {len(activities)} activities, added {added} new e-bike rides.")
    if gas_prices:
        print(f"Gas: ${gas_prices[-1][1]:.2f}/gal for the week of {gas_prices[-1][0]}.")
    print(
        f"{summary['rides']} rides, {summary['miles']} miles, ${summary['total_saved']:.2f} saved "
        f"({summary['paid_off_pct']}% of the bike), {summary['co2_saved_kg']} kg CO2 avoided."
    )


if __name__ == "__main__":
    try:
        main()
    except ApiError as err:
        sys.exit(str(err))
