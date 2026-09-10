"""Neshan (api.neshan.org) Direction API -- travel-time/distance estimates
per travel mode (car, motorcycle, ...).

Unlike Snapp/Tapsi's reverse-engineered endpoints, this is Neshan's own
public, documented API (https://platform.neshan.org/api/direction/):
GET /v4/direction with header `Api-Key: <key>`, params `type` (car |
motorcycle), `origin`/`destination` as "lat,lng" strings. Response shape
mirrors Google's Directions API (routes[].legs[].duration/distance).

No API key was available while writing this, so the request was never
made against the live service -- response parsing is best-effort against
Neshan's published shape. Like the other providers, the raw response text
is always returned alongside the parsed result (even when parsing yields
None) and the caller persists it to travel_times.raw_json regardless, so
a shape mismatch can be diagnosed and re-parsed later.

Neshan doesn't document a "bicycle" profile as of this writing (only car
and motorcycle) -- NESHAN_TRAVEL_MODES (config.py) defaults to those two;
add "bicycle" there if/when Neshan ships it and it's confirmed to work.
"""
import json

import httpx

from .errors import ProviderAuthError, ProviderError

API_BASE = "https://api.neshan.org/v4/direction"


def _headers(api_key: str) -> dict:
    return {"Api-Key": api_key}


def _params(origin: dict, destination: dict, mode: str) -> dict:
    return {
        "type": mode,
        "origin": f"{origin['lat']},{origin['lng']}",
        "destination": f"{destination['lat']},{destination['lng']}",
    }


def _extract_leg(data: dict) -> dict | None:
    routes = data.get("routes")
    if not isinstance(routes, list) or not routes:
        return None
    first = routes[0]
    legs = first.get("legs") if isinstance(first, dict) else None
    if not isinstance(legs, list) or not legs:
        return None
    leg = legs[0]
    duration = (leg.get("duration") or {}).get("value")
    distance = (leg.get("distance") or {}).get("value")
    if duration is None:
        return None
    return {"duration_seconds": duration, "distance_meters": distance}


def fetch_mode(origin: dict, destination: dict, api_key: str, mode: str) -> tuple[dict | None, str]:
    """Returns (leg_or_None, raw_response_text) for a single travel mode.
    Raises ProviderAuthError if no key is configured or Neshan rejects it;
    ProviderError on any other failure (network, non-JSON, HTTP >= 400)."""
    if not api_key:
        raise ProviderAuthError("no neshan api key configured, set NESHAN_API_KEY")

    try:
        resp = httpx.get(
            API_BASE,
            params=_params(origin, destination, mode),
            headers=_headers(api_key),
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise ProviderError(f"network error: {e}") from e

    if resp.status_code in (401, 403):
        raise ProviderAuthError(f"neshan rejected api key: HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise ProviderError(f"non-JSON response: {e}") from e

    return _extract_leg(data), resp.text
