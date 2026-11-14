from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Environment configuration. Rates here only seed the database on first
    run; after that they are edited through /api/settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    strava_client_id: str = ""
    strava_client_secret: str = ""
    # Public URL of this app, used to build the OAuth callback.
    base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./data/ebike.db"
    # Standard 5-field cron. Default: Mondays at 03:00. Empty disables it.
    sync_cron: str = "0 3 * * 1"

    default_mpg: float = 25.0
    default_gas_price: float = 3.50  # $/gallon
    default_wear_per_mile: float = 0.10  # $/mile: maintenance, tires, depreciation
    default_co2_kg_per_gallon: float = 8.887  # EPA figure for gasoline
    default_bike_cost: float = 3300.0
    default_title_match: str = "e-bike"


config = Config()
