import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "tapnap.db"
CREDENTIALS_PATH = DATA_DIR / "credentials.json"

FETCH_INTERVAL_MINUTES = int(os.environ.get("FETCH_INTERVAL_MINUTES", "10"))

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
