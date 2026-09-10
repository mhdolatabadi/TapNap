import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "tapnap.db"
CREDENTIALS_PATH = DATA_DIR / "credentials.json"

FETCH_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "10"))

# Neshan Direction API key (https://platform.neshan.org) -- a static app
# key, not a rotating user token like Snapp/Tapsi's, so it lives in an env
# var rather than credentials.json. Travel-time polling is simply skipped
# (each mode logs a ProviderAuthError to fetch_log) if this is unset.
NESHAN_API_KEY = os.environ.get("NESHAN_API_KEY", "")
NESHAN_TRAVEL_MODES = [
    m.strip() for m in os.environ.get("NESHAN_TRAVEL_MODES", "car,motorcycle").split(",") if m.strip()
]

# Default route: Azadi Square -> Milad Tower, Tehran
DEFAULT_ORIGIN = {"lat": 35.6997, "lng": 51.3380, "label": "میدان آزادی"}
DEFAULT_DESTINATION = {"lat": 35.7448, "lng": 51.3752, "label": "برج میلاد"}


def load_credentials() -> dict:
    """Re-read on every call so rotating a token on disk takes effect
    without restarting the container."""
    if not CREDENTIALS_PATH.exists():
        return {"snapp": {}, "tapsi": {}}
    try:
        return json.loads(CREDENTIALS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"snapp": {}, "tapsi": {}}


def save_provider_credentials(provider: str, data: dict):
    """Merge-update one provider's block, e.g. after a token refresh
    rotates access_token/refresh_token, or a response rotates a cookie."""
    creds = load_credentials()
    creds.setdefault(provider, {}).update(data)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(creds, ensure_ascii=False, indent=2))
