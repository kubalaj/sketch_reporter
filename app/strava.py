"""Thin wrapper over the parts of the Strava API we use."""

from collections.abc import Iterator
from urllib.parse import urlencode

import httpx

from .config import config

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"
PAGE_SIZE = 200  # Strava's maximum


def redirect_uri() -> str:
    return config.base_url.rstrip("/") + "/auth/strava/callback"


def authorize_url(state: str) -> str:
    params = {
        "client_id": config.strava_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "approval_prompt": "auto",
        # read_all so rides with restricted visibility are included too
        "scope": "read,activity:read_all",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token_request(client: httpx.Client, **grant) -> dict:
    resp = client.post(
        TOKEN_URL,
        data={
            "client_id": config.strava_client_id,
            "client_secret": config.strava_client_secret,
            **grant,
        },
    )
    resp.raise_for_status()
    return resp.json()


def exchange_code(client: httpx.Client, code: str) -> dict:
    return _token_request(client, grant_type="authorization_code", code=code)


def refresh_token(client: httpx.Client, refresh: str) -> dict:
    return _token_request(client, grant_type="refresh_token", refresh_token=refresh)


def list_activities(
    client: httpx.Client, access_token: str, after: int | None = None
) -> Iterator[dict]:
    """Yield every activity, optionally only those starting after an epoch time."""
    page = 1
    while True:
        params: dict = {"per_page": PAGE_SIZE, "page": page}
        if after is not None:
            params["after"] = after
        resp = client.get(
            f"{API_URL}/athlete/activities",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            return
        yield from batch
        page += 1
