"""Tapsi (api.tapsi.cab) ride price preview + cookie rotation.

Reverse-engineered from the app.tapsi.cab web app bundle: the ride preview
screen calls POST https://api.tapsi.cab/api/v3.1/ride/preview with
`credentials: "include"` -- auth here is entirely cookie/session based
(no JS-readable bearer token; the web client uses an oidc-client-js
UserManager whose refresh token is deliberately kept blank client-side).

There's no public, unauthenticated price endpoint -- a real session
cookie (obtained once via the user's own OTP login, see
credentials.example.json) is required. Since the actual session-renewal
endpoint isn't exposed to client JS, this module does the next best thing:
after every call it folds any `Set-Cookie` the server sent back into the
stored cookie string, so ordinary same-site cookie rotation is captured
automatically and a fresh full re-login is only needed once the session
truly expires server-side.

The exact response shape wasn't observed live (no test account session
was available while writing this), so parsing is best-effort and the raw
response is always kept for re-parsing later.
"""
import json

import httpx

from .. import config
from .errors import ProviderAuthError, ProviderError

BASE_URL = "https://api.tapsi.cab/api/v3.1/ride/preview"


def _headers(cookie: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # x-agent identifies the client to the backend; requests without it
        # get a generic 500 "UNKNOWN_ERROR" (gRPC code 13) rather than a
        # clean auth/validation error. Value copied from the passenger-webapp
        # bundle's own outgoing requests.
        "x-agent": "v2.2|passenger|WEBAPP|7.72.5||5.0",
        "Origin": "https://app.tapsi.cab",
        "Referer": "https://app.tapsi.cab/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:154.0) Gecko/20100101 Firefox/154.0",
        "Cookie": cookie,
    }


def _body(origin: dict, destination: dict) -> dict:
    return {
        # Keys must be "latitude"/"longitude", not "lat"/"lng" -- the API
        # silently accepts the wrong keys and returns
        # NOT_VALID_ORIGIN_OR_DESTINATION instead of a schema error.
        "origin": {"latitude": origin["lat"], "longitude": origin["lng"]},
        "destinations": [{"latitude": destination["lat"], "longitude": destination["lng"]}],
        "rider": None,
        "needTrunk": False,
        "silentMode": False,
        "waitingTime": 0,
        "gateway": "CAB",
        "initiatedVia": "WEB",
        "metadata": {"flowType": "ORIGIN_FIRST", "previewType": "ORIGIN_FIRST"},
    }


def _extract_services(data: dict) -> list[dict]:
    """Confirmed live shape (2026-09-06):
    {"data": {"tabs": [{"title": ..., "items": [{"service": {"key": ...,
    "isAvailable": ..., "prices": [{"numberOfPassengers": 1,
    "passengerShare": ...}, ...]}}, ...]}, ...]}}
    -- one price per (tab, service): the single-passenger fare."""
    root = data.get("data", data)
    tabs = root.get("tabs") if isinstance(root, dict) else None
    if not isinstance(tabs, list):
        return []

    results = []
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        tab_title = tab.get("title") or tab.get("key")
        for item in tab.get("items", []):
            service = item.get("service") if isinstance(item, dict) else None
            if not isinstance(service, dict) or not service.get("isAvailable"):
                continue
            prices = service.get("prices") or []
            single = next((p for p in prices if p.get("numberOfPassengers") == 1), None)
            price = (single or {}).get("passengerShare")
            key = service.get("key")
            if key is None or price is None:
                continue
            name = f"{tab_title}: {key}" if tab_title else str(key)
            results.append({"service_name": name, "price": price})
    return results


def _merge_cookies(existing: str, set_cookie_headers: list[str]) -> str:
    jar = {}
    for part in existing.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            jar[k] = v
    for raw in set_cookie_headers:
        attr = raw.split(";", 1)[0].strip()
        if "=" in attr:
            k, v = attr.split("=", 1)
            jar[k] = v
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def fetch(origin: dict, destination: dict, creds: dict) -> tuple[list[dict], str]:
    cookie = creds.get("cookie")
    if not cookie:
        raise ProviderAuthError("no tapsi cookie configured, need a fresh login")

    try:
        resp = httpx.post(
            BASE_URL,
            json=_body(origin, destination),
            headers=_headers(cookie),
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise ProviderError(f"network error: {e}") from e

    set_cookie_headers = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []
    if set_cookie_headers:
        new_cookie = _merge_cookies(cookie, set_cookie_headers)
        if new_cookie != cookie:
            config.save_provider_credentials("tapsi", {"cookie": new_cookie})

    if resp.status_code in (401, 403):
        raise ProviderAuthError(f"tapsi rejected session: HTTP {resp.status_code}, need a fresh login")
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise ProviderError(f"non-JSON response: {e}") from e

    return _extract_services(data), resp.text
