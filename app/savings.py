"""The savings math. A ride's distance stands in for the drive it replaced."""

from .models import METERS_PER_MILE, AppSettings, Ride


def apply_rates(ride: Ride, settings: AppSettings) -> None:
    """Lock the current rates onto a ride and compute its savings."""
    ride.mpg = settings.mpg
    ride.gas_price = settings.gas_price
    ride.wear_per_mile = settings.wear_per_mile
    ride.co2_kg_per_gallon = settings.co2_kg_per_gallon
    recompute(ride)


def recompute(ride: Ride) -> None:
    """Recompute savings from the ride's distance and its own locked rates."""
    miles = ride.distance_m / METERS_PER_MILE
    gallons = miles / ride.mpg
    ride.gas_saved = gallons * ride.gas_price
    ride.wear_saved = miles * ride.wear_per_mile
    ride.co2_saved_kg = gallons * ride.co2_kg_per_gallon
