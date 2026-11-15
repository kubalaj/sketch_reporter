# Sketch Reporter

Tracks how much money and carbon my Santa Cruz Sketch e-bike has saved
compared with driving, and how close it is to paying for itself.

One script, `sync.py`, pulls e-bike rides from Strava and writes
[`data/rides.json`](data/rides.json). A GitHub Actions job runs it every
evening and commits the updated file. There is no server and no database, and
the script uses only the Python standard library.

## The dashboard

`index.html` is a static page that reads `data/rides.json` in the browser and
shows the totals, a savings-over-time chart and the ride list. It has no
build step or dependencies; the code is in `web/`. It is meant to be served
by GitHub Pages from the root of `main` (repo **Settings > Pages**), so each
nightly data commit updates the site.

To view it locally (opening the file directly won't work, since the page
loads the data over HTTP):

```bash
python3 -m http.server 8000
```

Then open <http://localhost:8000>.

## How savings are calculated

Each ride's distance stands in for the drive it replaced:

| Figure | Formula |
| --- | --- |
| Gas saved | `miles / mpg * gas_price` |
| Wear and tear saved | `miles * wear_per_mile` |
| CO2 saved | `miles / mpg * 8.887 kg` (EPA figure per gallon of gasoline) |

`mpg` and `wear_per_mile` live in [`config.json`](config.json) and are set
for a lifted 2021 Ford Ranger on 285/70R17 all-terrains.

**Gas price** is looked up per ride: each ride uses the average price of
regular gas for the week it happened, from the U.S. Energy Information
Administration's weekly San Francisco series (the closest one it publishes
to Oakland). Without an `EIA_API_KEY`, or if the lookup fails, rides use
`gas_price` from `config.json` instead.

A ride keeps the rates it was first synced with. Run
`python3 sync.py --reprice` to re-price every ride.

## Setup

Strava requires a paid subscription for API access.

1. Create an API application at <https://www.strava.com/settings/api> and set
   **Authorization Callback Domain** to `localhost`.
2. Copy `.env.example` to `.env` and fill in the client ID and secret.
   For weekly gas prices, also add a free API key from
   <https://www.eia.gov/opendata/register.php> as `EIA_API_KEY`.
3. Edit `config.json` to match your car.
4. Connect your Strava account (opens a browser; port 8000 must be free):

   ```bash
   python3 sync.py --auth
   ```

5. Pull your rides:

   ```bash
   python3 sync.py
   ```

## Weekly sync on GitHub

In the repo, go to **Settings > Secrets and variables > Actions** and add:

| Secret | Value |
| --- | --- |
| `STRAVA_CLIENT_ID` | from your Strava API application |
| `STRAVA_CLIENT_SECRET` | from your Strava API application |
| `STRAVA_REFRESH_TOKEN` | printed at the end of `python3 sync.py --auth` |
| `EIA_API_KEY` | your EIA API key (optional) |

The workflow in `.github/workflows/sync.yml` then runs every evening, and can
be run by hand from the **Actions** tab. If Strava ever replaces the refresh
token, the run prints a warning; repeat step 4 and update the secret.

## Which rides count

An activity counts when its title contains `title_match` (default `e-bike`,
case-insensitive), or when Strava classifies it as an e-bike ride and
`match_sport_type` is `true`. To leave a ride out of the totals, add its `id`
from `rides.json` to `exclude_ids` in `config.json`.

## The data file

```json
{
  "updated_at": "2026-11-16T09:00:12+00:00",
  "gas_price": {
    "source": "EIA weekly series EMM_EPMR_PTE_Y05SF_DPG",
    "week": "2026-11-09", "dollars_per_gallon": 4.87
  },
  "summary": {
    "rides": 42, "miles": 512.3,
    "gas_saved": 71.72, "wear_saved": 51.23, "total_saved": 122.95,
    "co2_saved_kg": 182.11, "co2_saved_lbs": 401.48,
    "bike_cost": 3300, "remaining": 3177.05, "paid_off_pct": 3.73,
    "projected_payoff_date": "2031-04-02"
  },
  "rides": [
    {
      "id": 123, "name": "Morning E-Bike Ride", "sport_type": "EBikeRide",
      "date": "2026-06-01T08:00:00", "miles": 10.0, "moving_time_s": 1800,
      "mpg": 16, "gas_price": 5.7, "gas_price_week": "2026-06-01", "wear_per_mile": 0.13, "co2_kg_per_gallon": 8.887,
      "gas_saved": 1.4, "wear_saved": 1.0, "total_saved": 2.4, "co2_saved_kg": 3.5548,
      "excluded": false
    }
  ]
}
```

`projected_payoff_date` is a straight-line projection from average savings
per day since the first ride.

## Tests

```bash
python3 -m unittest discover tests
```
