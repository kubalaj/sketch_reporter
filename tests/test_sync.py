import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sync  # noqa: E402

CONFIG = {
    "mpg": 25,
    "gas_price": 3.50,
    "wear_per_mile": 0.10,
    "co2_kg_per_gallon": 8.887,
    "gas_price_series": "TEST",
    "bike_cost": 3300,
    "title_match": "e-bike",
    "match_sport_type": True,
    "exclude_ids": [],
}


def activity(id, name, miles, sport="Ride", date="2026-06-01T15:00:00Z"):
    return {
        "id": id,
        "name": name,
        "sport_type": sport,
        "start_date": date,
        "start_date_local": date,
        "distance": miles * sync.METERS_PER_MILE,
        "moving_time": 1800,
    }


class SyncTests(unittest.TestCase):
    def test_filters_and_prices_rides(self):
        rides, added = sync.merge(
            [],
            [
                activity(1, "Morning E-Bike Ride", 10, sport="EBikeRide"),
                activity(2, "Morning Run", 3, sport="Run"),
                activity(3, "e-bike ride to work", 8),  # matched by title
                activity(4, "Commute", 4, sport="EBikeRide"),  # matched by sport type
                activity(5, "Road ride", 30),
            ],
            CONFIG,
        )
        self.assertEqual(added, 3)
        self.assertEqual([r["id"] for r in rides], [1, 3, 4])
        ten_miles = rides[0]
        self.assertAlmostEqual(ten_miles["gas_saved"], 1.40)
        self.assertAlmostEqual(ten_miles["wear_saved"], 1.00)
        self.assertAlmostEqual(ten_miles["total_saved"], 2.40)
        self.assertAlmostEqual(ten_miles["co2_saved_kg"], 3.5548)

    def test_title_only_matching(self):
        config = {**CONFIG, "match_sport_type": False}
        rides, _ = sync.merge([], [activity(4, "Commute", 4, sport="EBikeRide")], config)
        self.assertEqual(rides, [])

    def test_resync_is_idempotent_and_keeps_original_rates(self):
        rides, _ = sync.merge([], [activity(1, "E-Bike Ride", 10)], CONFIG)
        pricier = {**CONFIG, "gas_price": 5.00}
        rides, added = sync.merge(
            rides,
            [
                activity(1, "E-Bike Ride", 20),  # distance edited on Strava
                activity(2, "E-Bike Ride", 10, date="2026-06-02T15:00:00Z"),
            ],
            pricier,
        )
        self.assertEqual(added, 1)
        self.assertAlmostEqual(rides[0]["gas_saved"], 2.80)  # 20 mi at the original $3.50
        self.assertAlmostEqual(rides[1]["gas_saved"], 2.00)  # 10 mi at $5.00

        rides, _ = sync.merge(rides, [], pricier, reprice=True)
        self.assertAlmostEqual(rides[0]["gas_saved"], 4.00)

    def test_rides_use_the_gas_price_for_their_week(self):
        weekly = [("2026-05-25", 5.00), ("2026-06-01", 5.25), ("2026-06-08", 5.50)]
        rides, _ = sync.merge(
            [],
            [
                activity(1, "E-Bike Ride", 25, date="2026-01-10T08:00:00Z"),  # before the series
                activity(2, "E-Bike Ride", 25, date="2026-06-01T08:00:00Z"),  # on a publish day
                activity(3, "E-Bike Ride", 25, date="2026-06-07T08:00:00Z"),  # later that week
                activity(4, "E-Bike Ride", 25, date="2026-07-04T08:00:00Z"),  # after the series
            ],
            CONFIG,
            weekly,
        )
        self.assertEqual([r["gas_price"] for r in rides], [5.00, 5.25, 5.25, 5.50])
        self.assertAlmostEqual(rides[1]["gas_saved"], 5.25)  # 25 mi at 25 mpg is one gallon
        self.assertEqual(sync.gas_price_on([], "2026-06-01", 3.50), 3.50)

    def test_gas_lookup_parses_eia_response_and_survives_failure(self):
        import os
        from unittest import mock

        payload = {"response": {"data": [
            {"period": "2026-06-08", "value": "5.5"},
            {"period": "2026-06-01", "value": 5.25},
            {"period": "2026-05-25", "value": None},
        ]}}
        with mock.patch.dict(os.environ, {"EIA_API_KEY": "k"}):
            with mock.patch.object(sync, "request_json", return_value=payload) as req:
                self.assertEqual(
                    sync.fetch_gas_prices(CONFIG), [("2026-06-01", 5.25), ("2026-06-08", 5.5)]
                )
                self.assertIn("facets%5Bseries%5D%5B%5D=TEST", req.call_args[0][0])
            with mock.patch.object(sync, "request_json", side_effect=sync.ApiError("boom")):
                self.assertEqual(sync.fetch_gas_prices(CONFIG), [])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sync.fetch_gas_prices(CONFIG), [])

    def test_summary_and_exclusions(self):
        activities = [
            activity(1, "E-Bike Ride", 10, date="2026-05-01T08:00:00Z"),
            activity(2, "E-Bike Ride", 20, date="2026-06-01T08:00:00Z"),
        ]
        rides, _ = sync.merge([], activities, CONFIG)
        summary = sync.summarize(rides, CONFIG, today=date(2026, 7, 1))
        self.assertEqual(summary["rides"], 2)
        self.assertEqual(summary["miles"], 30)
        self.assertEqual(summary["total_saved"], 7.20)
        self.assertEqual(summary["remaining"], 3292.80)
        self.assertEqual(summary["paid_off_pct"], 0.22)
        # $7.20 over 61 days leaves $3292.80, which takes 27,897 more days
        self.assertEqual(summary["projected_payoff_date"], "2102-11-17")

        rides, _ = sync.merge(rides, [], {**CONFIG, "exclude_ids": [2]})
        self.assertEqual(sync.summarize(rides, CONFIG)["total_saved"], 2.40)

    def test_broken_activity_is_skipped_not_fatal(self):
        from unittest import mock
        from urllib.parse import parse_qs, urlparse

        everything = list(range(1, 1301))  # activity ids, in listing order
        broken = {1037, 1205}
        calls = []

        def fake_strava(url, form=None, token=None):
            calls.append(url)
            query = parse_qs(urlparse(url).query)
            size, page = int(query["per_page"][0]), int(query["page"][0])
            items = everything[(page - 1) * size : page * size]
            if broken & set(items):
                raise sync.ApiError("Record Not Found", 404)
            return [{"id": i} for i in items]

        with mock.patch.object(sync, "request_json", side_effect=fake_strava):
            activities, skipped = sync.fetch_activities("token")
        self.assertEqual([a["id"] for a in activities], [i for i in everything if i not in broken])
        self.assertEqual(skipped, [1037, 1205])
        self.assertLess(len(calls), 50)

        def rate_limited(url, form=None, token=None):
            raise sync.ApiError("Rate Limit Exceeded", 429)

        with mock.patch.object(sync, "request_json", side_effect=rate_limited):
            with self.assertRaises(sync.ApiError):
                sync.fetch_activities("token")

    def test_empty_summary(self):
        summary = sync.summarize([], CONFIG)
        self.assertEqual(summary["total_saved"], 0)
        self.assertIsNone(summary["projected_payoff_date"])


if __name__ == "__main__":
    unittest.main()
