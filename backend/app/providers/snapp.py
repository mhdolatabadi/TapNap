"""Snapp (app.snapp.taxi) ride price estimate + token refresh.

Reverse-engineered from the passenger-pwa web app bundle (v18.44.1):

- Price estimate: POST {VITE_API}/v3/price ("/api/v3/price" relative to
  app.snapp.taxi) with `Authorization: Bearer <accessToken>`.
- Token refresh: POST {VITE_API_OAUTH}/v2/auth
  ("/api/api-passenger-oauth/v2/auth") with a JSON body of
  {grant_type: "refresh_token", client_id, client_secret, device_id,
  refresh_token}. client_id/client_secret below are constants baked into
  the public web bundle (shipped to every browser), not secrets pulled
  from anywhere private -- this replicates exactly what the official web
  app does automatically for a logged-in user.

There's no public, unauthenticated price endpoint -- a real access_token
(obtained once via the user's own OTP login, see credentials.example.json)
is required, and this module keeps it alive by refreshing before/after
expiry using the paired refresh_token.

The exact price-response shape wasn't observed live (no test account was
available while writing this), so parsing tries a few plausible shapes
and always keeps the raw response for later re-parsing.
"""
import json
import uuid

import httpx

from .. import config
from .errors import ProviderAuthError, ProviderError

API_BASE = "https://app.snapp.taxi/api"
PRICE_URL = f"{API_BASE}/v3/price"
REFRESH_URL = f"{API_BASE}/api-passenger-oauth/v2/auth"

# Public client credentials baked into the passenger-pwa bundle (not secret).
REFRESH_CLIENT_ID = "ios_sadjfhasd9871231hfso234"
REFRESH_CLIENT_SECRET = "23497shjlf982734-=1031nln"

APP_HEADERS = {
    "App-Version": "pwa",
    "x-app-version": "v18.44.1",
    "x-app-name": "passenger-pwa",
    "Content-Type": "application/json",
    "Accept": "application/json",
    # Snapp's edge WAF hard-403s any request that doesn't look like a
    # browser -- a bare httpx client (default User-Agent, no Origin/Referer)
    # gets rejected before it ever reaches the app, independent of token
    # validity. These three make it through in testing against the user's
    # own account; the rest of a real browser's fingerprint (Sec-Fetch-*,
    # X-Raw-Fingerprint) wasn't needed.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:154.0) Gecko/20100101 Firefox/154.0",
    "Origin": "https://app.snapp.taxi",
    "Referer": "https://app.snapp.taxi/",
}


def _price_headers(access_token: str) -> dict:
    return {**APP_HEADERS, "Authorization": f"Bearer {access_token}"}


def _price_body(origin: dict, destination: dict) -> dict:
    return {
        "points": [
            {"lat": str(origin["lat"]), "lng": str(origin["lng"])},
            {"lat": str(destination["lat"]), "lng": str(destination["lng"])},
        ],
        "price_ride_recom": False,
        "options": {},
        "locale": "fa",
        "os": 6,
        "version": 0,
    }


def _extract_services(data: dict) -> list[dict]:
    """Extraction of [{service_name, price}, ...] from a POST /v3/price
    response. Confirmed live shape (2026-09-06):
    {"data": {"services": [{"info": {"name": ...}, "price": {"final": ...}, ...}, ...]}}
    -- falls back to a couple of plausible flat shapes in case the live
    format changes again, and always keeps the raw response regardless."""
    candidates = data.get("data", data)
    services = None
    if isinstance(candidates, dict):
        for key in ("services", "prices", "categories", "ride_options"):
            if isinstance(candidates.get(key), list):
                services = candidates[key]
                break
    elif isinstance(candidates, list):
        services = candidates

    if not services:
        return []

    results = []
    for item in services:
        if not isinstance(item, dict):
            continue
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        name = info.get("name") or item.get("name") or item.get("title") or item.get("service_type")
        price_field = item.get("price")
        if isinstance(price_field, dict):
            price = price_field.get("final")
        else:
            price = price_field
        price = price if price is not None else item.get("final") or item.get("final_price")
        if name is not None and price is not None:
            # Snapp's API reports Rial; Tapsi's reports Toman natively.
            # Normalize to Toman here so the two providers are comparable
            # on the same chart (1 Toman = 10 Rial, always an exact divide).
            results.append({"service_name": str(name), "price": price // 10})
    return results


def _refresh(creds: dict) -> str:
    """Exchange the stored refresh_token for a new access_token, persisting
    the rotated pair (Snapp rotates refresh_token on every use) back to
    credentials.json. Returns the new access_token."""
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        raise ProviderAuthError("no snapp refresh_token configured, need a fresh login")

    device_id = creds.get("device_id") or str(uuid.uuid4())
    body = {
        "grant_type": "refresh_token",
        "client_id": REFRESH_CLIENT_ID,
        "client_secret": REFRESH_CLIENT_SECRET,
        "device_id": device_id,
        "refresh_token": refresh_token,
    }
    try:
        resp = httpx.post(REFRESH_URL, json=body, headers=APP_HEADERS, timeout=15)
    except httpx.HTTPError as e:
        raise ProviderError(f"network error refreshing snapp token: {e}") from e

    if resp.status_code >= 400:
        raise ProviderAuthError(
            f"snapp refresh_token rejected (HTTP {resp.status_code}), need a fresh login: {resp.text[:300]}"
        )

    try:
        data = resp.json()
        new_access = data["access_token"]
        new_refresh = data["refresh_token"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ProviderError(f"unexpected snapp refresh response shape: {e}") from e

    config.save_provider_credentials(
        "snapp", {"access_token": new_access, "refresh_token": new_refresh, "device_id": device_id}
    )
    return new_access


def _call_price(origin: dict, destination: dict, access_token: str) -> httpx.Response:
    try:
        return httpx.post(
            PRICE_URL,
            json=_price_body(origin, destination),
            headers=_price_headers(access_token),
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise ProviderError(f"network error: {e}") from e


def fetch(origin: dict, destination: dict, creds: dict) -> tuple[list[dict], str]:
    access_token = creds.get("access_token")
    if not access_token:
        access_token = _refresh(creds)

    resp = _call_price(origin, destination, access_token)

    if resp.status_code in (401, 403):
        # Access token expired -- refresh once and retry.
        access_token = _refresh(creds)
        resp = _call_price(origin, destination, access_token)
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"snapp rejected refreshed token: HTTP {resp.status_code}")

    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise ProviderError(f"non-JSON response: {e}") from e

    return _extract_services(data), resp.text
