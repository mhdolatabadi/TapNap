"""Snapp OTP login: request_otp()/submit_otp() are used both by the
interactive CLI below and by the password-gated /api/admin/snapp/*
endpoints in main.py.

The `access_token`/`refresh_token` pair in credentials.json keeps itself
alive indefinitely via snapp.py's own refresh-on-expiry logic (see
scheduler.py) -- this only exists for the day that chain finally breaks
(refresh_token revoked, logged out elsewhere, etc.) and a fresh login is
needed. It runs the same SMS-OTP flow the passenger-pwa web app uses
(grant_type "sms_v2"), reusing this app's own client credentials and
browser-shaped headers (see snapp.py) so it isn't blocked by the same
edge WAF that blocks plain scripted requests.

Run the CLI from the host:
    docker exec -it tapnap python -m app.login_snapp
"""
import sys
import uuid

import httpx

from . import config
from .providers.errors import ProviderError
from .providers.snapp import API_BASE, APP_HEADERS, REFRESH_CLIENT_ID, REFRESH_CLIENT_SECRET

OTP_URL = f"{API_BASE}/api-passenger-oauth/v3/mutotp"
AUTH_URL = f"{API_BASE}/api-passenger-oauth/v3/mutotp/auth"


def request_otp(cellphone: str) -> None:
    resp = httpx.post(
        OTP_URL,
        json={"cellphone": cellphone, "attestation": {"method": "skip", "platform": "skip"}, "extra_methods": []},
        headers=APP_HEADERS,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise ProviderError(f"خطا در درخواست کد تایید: HTTP {resp.status_code}: {resp.text[:300]}")


def submit_otp(cellphone: str, otp: str, device_id: str) -> dict:
    """Returns the auth response dict and, on success, persists the new
    access/refresh token pair to credentials.json itself."""
    resp = httpx.post(
        AUTH_URL,
        json={
            "attestation": {"method": "skip", "platform": "skip"},
            "grant_type": "sms_v2",
            "client_id": REFRESH_CLIENT_ID,
            "client_secret": REFRESH_CLIENT_SECRET,
            "cellphone": cellphone,
            "token": otp,
            "referrer": "pwa",
            "device_id": device_id,
        },
        headers=APP_HEADERS,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise ProviderError(f"خطا در تایید کد: HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    try:
        access_token = data["access_token"]
        refresh_token = data["refresh_token"]
    except KeyError:
        raise ProviderError(f"پاسخ غیرمنتظره از سرور: {data}") from None

    config.save_provider_credentials(
        "snapp", {"access_token": access_token, "refresh_token": refresh_token, "device_id": device_id}
    )
    return data


def main():
    cellphone = input("شماره موبایل اسنپ (با کد کشور، مثل +989121234567): ").strip()
    device_id = str(uuid.uuid4())

    try:
        request_otp(cellphone)
        print("کد تایید پیامک شد.")
        otp = input("کد تایید دریافتی: ").strip()
        data = submit_otp(cellphone, otp, device_id)
    except ProviderError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"ورود موفق برای {data.get('fullname', cellphone)} -- توکن‌ها در credentials.json ذخیره شدند.")


if __name__ == "__main__":
    main()
