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

    def test_empty_summary(self):
        summary = sync.summarize([], CONFIG)
        self.assertEqual(summary["total_saved"], 0)
        self.assertIsNone(summary["projected_payoff_date"])


if __name__ == "__main__":
    unittest.main()
