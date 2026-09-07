import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db
from .login_snapp import request_otp as snapp_request_otp
from .login_snapp import submit_otp as snapp_submit_otp
from .providers.errors import ProviderError
from .scheduler import fetch_and_store, start_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    start_scheduler()
    yield


app = FastAPI(title="tapnap", lifespan=lifespan)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class Point(BaseModel):
    lat: float
    lng: float
    label: str | None = None


class RouteIn(BaseModel):
    origin: Point
    destination: Point


class OtpRequestIn(BaseModel):
    cellphone: str


class OtpVerifyIn(BaseModel):
    cellphone: str
    otp: str


def _check_admin_password(x_admin_password: str | None):
    # No account system on this app at all -- this is the only thing
    # standing between a public visitor and triggering a real SMS OTP to
    # an arbitrary phone number (an open OTP-spam relay) or overwriting
    # the stored Snapp session. If no password is configured, admin
    # endpoints are simply unusable rather than falling open.
    expected = config.load_credentials().get("admin_password")
    if not expected or not x_admin_password or not hmac.compare_digest(x_admin_password, expected):
        raise HTTPException(401, "invalid admin password")


def _client_id(x_client_id: str | None) -> str:
    # No accounts/login -- each browser generates its own id (see app.js)
    # and sends it as this header. Anyone who doesn't (curl, old cache)
    # shares the fallback "default" bucket rather than erroring out.
    return x_client_id or db.DEFAULT_CLIENT_ID


@app.get("/api/route")
def api_get_route(x_client_id: str | None = Header(default=None)):
    return db.get_route(_client_id(x_client_id))


@app.put("/api/route")
def api_set_route(route: RouteIn, x_client_id: str | None = Header(default=None)):
    client_id = _client_id(x_client_id)
    db.set_route(client_id, route.origin.model_dump(), route.destination.model_dump())
    return db.get_route(client_id)


@app.get("/api/prices")
def api_get_prices(hours: int = 24, x_client_id: str | None = Header(default=None)):
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be between 1 and 720")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    route = db.get_route(_client_id(x_client_id))
    return db.get_prices(since, route["origin"], route["destination"])


@app.get("/api/status")
def api_status(x_client_id: str | None = Header(default=None)):
    route = db.get_route(_client_id(x_client_id))
    return {"route": route, "last_fetch": db.get_last_fetch_status(route["origin"], route["destination"])}


@app.post("/api/fetch-now")
def api_fetch_now():
    fetch_and_store()
    return {"ok": True}


@app.post("/api/admin/snapp/request-otp")
def api_snapp_request_otp(body: OtpRequestIn, x_admin_password: str | None = Header(default=None)):
    _check_admin_password(x_admin_password)
    try:
        snapp_request_otp(body.cellphone)
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.post("/api/admin/snapp/verify-otp")
def api_snapp_verify_otp(body: OtpVerifyIn, x_admin_password: str | None = Header(default=None)):
    _check_admin_password(x_admin_password)
    try:
        data = snapp_submit_otp(body.cellphone, body.otp, str(uuid.uuid4()))
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "fullname": data.get("fullname")}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
