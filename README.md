# E-Bike Savings

Self-hosted tracker for how much money and carbon an e-bike saves compared
with driving. It pulls e-bike rides from Strava on a schedule and, for each
one, works out the gas, car wear and tear, and CO2 the same trip would have
cost by car, then keeps a running total against what the bike cost.

This is the backend: a FastAPI app with a SQLite database. The frontend comes next.

## How savings are calculated

Each ride's distance stands in for the drive it replaced:

| Figure | Formula |
| --- | --- |
| Gas saved | `miles / mpg * gas_price` |
| Wear and tear saved | `miles * wear_per_mile` |
| CO2 saved | `miles / mpg * 8.887 kg` (EPA figure per gallon of gasoline) |

The rates in effect when a ride is first recorded are stored on that ride, so
changing the gas price later only affects new rides. To re-price everything
at the current settings, call `POST /api/rides/recalculate`.

## Setup

1. Create a Strava API application at <https://www.strava.com/settings/api>.
   Set **Authorization Callback Domain** to the host you will reach this app
   on (`localhost` when running locally).
2. Copy `.env.example` to `.env` and fill in the client ID and secret. Set the
   default rates to match your car.
3. Start it:

   ```bash
   docker compose up -d --build
   ```

4. Open <http://localhost:8000/auth/strava/login> and approve access.
5. Import your ride history:

   ```bash
   curl -X POST http://localhost:8000/api/sync
   ```

After that it syncs on the `SYNC_CRON` schedule (weekly by default).
Interactive API docs are at <http://localhost:8000/docs>.

## Which rides count

A Strava activity is imported when its title contains the `title_match`
setting (default `e-bike`, case-insensitive), or when Strava classifies it as
an e-bike ride and `match_sport_type` is on. If a ride didn't replace a car
trip, exclude it from the totals with
`PATCH /api/rides/{id}` and `{"excluded": true}`.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/summary` | Totals, percent of the bike paid off, projected payoff date |
| `GET /api/summary/timeseries?bucket=day\|week\|month\|year` | Per-period and cumulative totals |
| `GET /api/rides` | Rides, newest first |
| `POST /api/rides` | Log a ride that isn't on Strava |
| `PATCH /api/rides/{id}` | Exclude or re-include a ride |
| `POST /api/rides/recalculate` | Re-price all rides at the current settings |
| `GET`, `PATCH /api/settings` | MPG, gas price, wear rate, bike cost, title filter |
| `POST /api/sync` | Sync now (`?full=true` re-reads all history) |
| `GET /api/status`, `GET /api/syncs` | Connection state and sync history |

## Development

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -e '.[dev]'
```

```bash
.venv/bin/pytest
```

```bash
.venv/bin/uvicorn app.main:app --reload
```

## Security

The API has no login of its own. Keep it on your home network or behind a
reverse proxy that handles authentication; don't expose port 8000 to the
internet directly.
