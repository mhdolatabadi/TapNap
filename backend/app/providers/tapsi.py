"""Tapsi (api.tapsi.cab) ride price preview + token refresh.

Reverse-engineered from the app.tapsi.cab web app bundle: the ride preview
screen calls POST https://api.tapsi.cab/api/v3.1/ride/preview with
`credentials: "include"` -- auth here is entirely cookie-based (the
webapp's oidc-client-js UserManager never exposes the tokens to JS
directly; both `accessToken` and `refreshToken` are HttpOnly cookies on
.tapsi.cab).

Session renewal (confirmed live via HAR capture, 2026-09-14):
POST https://arapi.tapsi.cab/api/v2.1/user/sso/token, form-urlencoded body
{grant_type: "refresh_token", refresh_token: "__EMPTY__", client_id:
"tapsi.cab.passenger", scope: "PASSENGER", device_info: {...}} -- the
`refresh_token` body field is a literal placeholder the webapp always
sends; auth is carried entirely by the `refreshToken` cookie. The 200
response body itself is just `{"result":"OK","data":{}}` -- the actual
new `accessToken`/`refreshToken` come back as Set-Cookie headers, not
JSON. Same session (`sid` in the JWT payload) is kept across refreshes,
just extended -- this isn't single-use refresh-token rotation the way
Snapp's is, but the new pair is persisted anyway since Max-Age changes.

There's no public, unauthenticated price endpoint -- a real session
cookie (obtained once via the user's own OTP login, see
credentials.example.json) is required. `fetch()` mirrors snapp.py's
refresh-on-401/403 pattern using the endpoint above; the passive
Set-Cookie-folding on every call stays too, since ordinary cookie
rotation can still show up outside of an explicit refresh.

The exact response shape wasn't observed live (no test account session
was available while writing this), so parsing is best-effort and the raw
response is always kept for re-parsing later.
"""
import json

import httpx

from .. import config
from .errors import ProviderAuthError, ProviderError

BASE_URL = "https://api.tapsi.cab/api/v3.1/ride/preview"
REFRESH_URL = "https://arapi.tapsi.cab/api/v2.1/user/sso/token"


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


def _cookie_jar(cookie: str) -> dict:
    jar = {}
    for part in cookie.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            jar[k] = v
    return jar


def _refresh(cookie: str) -> str:
    """Exchange the stored refreshToken cookie for a fresh accessToken
    (and extended refreshToken), persisting the new pair back to
    credentials.json. Returns the new cookie string."""
    refresh_token = _cookie_jar(cookie).get("refreshToken")
    if not refresh_token:
        raise ProviderAuthError("no tapsi refreshToken cookie configured, need a fresh login")

    body = {
        "grant_type": "refresh_token",
        "refresh_token": "__EMPTY__",
        "client_id": "tapsi.cab.passenger",
        "scope": "PASSENGER",
        "device_info": json.dumps({"platform": "WEBAPP", "product": "PASSENGER"}),
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://app.tapsi.cab",
        "Referer": "https://app.tapsi.cab/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:154.0) Gecko/20100101 Firefox/154.0",
        "Cookie": f"refreshToken={refresh_token}",
    }
    try:
        resp = httpx.post(REFRESH_URL, data=body, headers=headers, timeout=15)
    except httpx.HTTPError as e:
        raise ProviderError(f"network error refreshing tapsi token: {e}") from e

    if resp.status_code >= 400:
        raise ProviderAuthError(
            f"tapsi refreshToken rejected (HTTP {resp.status_code}), need a fresh login: {resp.text[:300]}"
        )

    set_cookie_headers = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []
    new_pair = {}
    for raw in set_cookie_headers:
        attr = raw.split(";", 1)[0].strip()
        if "=" in attr:
            k, v = attr.split("=", 1)
            if k in ("accessToken", "refreshToken"):
                new_pair[k] = v
    if "accessToken" not in new_pair or "refreshToken" not in new_pair:
        raise ProviderError("tapsi refresh response missing accessToken/refreshToken cookies")

    new_cookie = f"accessToken={new_pair['accessToken']}; refreshToken={new_pair['refreshToken']}"
    config.save_provider_credentials("tapsi", {"cookie": new_cookie})
    return new_cookie


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


def _call_preview(origin: dict, destination: dict, cookie: str) -> httpx.Response:
    try:
        return httpx.post(
            BASE_URL,
            json=_body(origin, destination),
            headers=_headers(cookie),
            timeout=15,
        )
    except httpx.HTTPError as e:
        raise ProviderError(f"network error: {e}") from e


def fetch(origin: dict, destination: dict, creds: dict) -> tuple[list[dict], str]:
    cookie = creds.get("cookie")
    if not cookie:
        raise ProviderAuthError("no tapsi cookie configured, need a fresh login")

    resp = _call_preview(origin, destination, cookie)

    set_cookie_headers = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []
    if set_cookie_headers:
        new_cookie = _merge_cookies(cookie, set_cookie_headers)
        if new_cookie != cookie:
            cookie = new_cookie
            config.save_provider_credentials("tapsi", {"cookie": new_cookie})

    if resp.status_code in (401, 403):
        # accessToken expired -- exchange refreshToken for a new one and retry.
        cookie = _refresh(cookie)
        resp = _call_preview(origin, destination, cookie)
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"tapsi rejected refreshed session: HTTP {resp.status_code}")

    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise ProviderError(f"non-JSON response: {e}") from e

    return _extract_services(data), resp.text
