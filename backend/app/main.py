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

# Docker's COPY layout puts app/ and frontend/ side by side under /app, so
# .parent.parent lands on "frontend" there -- but a local checkout has
# app/ nested one level deeper (backend/app/) with frontend/ at the repo
# root, so the documented "cd backend && uvicorn app.main:app" dev command
# needs one more .parent to find it. Probe both rather than hardcoding one.
_FRONTEND_DIR_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "frontend",  # Docker: /app/app -> /app/frontend
    Path(__file__).resolve().parent.parent.parent / "frontend",  # local dev: backend/app -> repo root/frontend
]
FRONTEND_DIR = next((p for p in _FRONTEND_DIR_CANDIDATES if p.is_dir()), _FRONTEND_DIR_CANDIDATES[0])


class Point(BaseModel):
    lat: float
    lng: float
    label: str | None = None


class JobIn(BaseModel):
    name: str
    origin: Point
    destination: Point


class JobActiveIn(BaseModel):
    active: bool


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


def _get_job_or_404(job_id: int) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


def _job_status(job: dict, last_fetch: list[dict]) -> str:
    """"stopped" if switched off, "pending" if switched on but never
    fetched yet, "error" if the latest attempt for any provider failed,
    else "running"."""
    if not job["active"]:
        return "stopped"
    if not last_fetch:
        return "pending"
    if any(not entry["ok"] for entry in last_fetch):
        return "error"
    return "running"


@app.get("/api/jobs")
def api_list_jobs():
    jobs = db.get_jobs()
    result = []
    for job in jobs:
        last_fetch = db.get_last_fetch_status(job["id"])
        result.append({
            **job,
            "status": _job_status(job, last_fetch),
            "latest_prices": db.get_latest_prices(job["id"]),
        })
    return result


@app.post("/api/jobs")
def api_create_job(job: JobIn):
    name = job.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    return db.create_job(name, job.origin.model_dump(), job.destination.model_dump())


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: int):
    job = _get_job_or_404(job_id)
    last_fetch = db.get_last_fetch_status(job_id)
    return {
        **job,
        "status": _job_status(job, last_fetch),
        "last_fetch": last_fetch,
        "latest_prices": db.get_latest_prices(job_id),
    }


@app.patch("/api/jobs/{job_id}")
def api_set_job_active(job_id: int, body: JobActiveIn):
    _get_job_or_404(job_id)
    job = db.set_job_active(job_id, body.active)
    return {**job, "status": _job_status(job, db.get_last_fetch_status(job_id))}


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: int):
    if not db.delete_job(job_id):
        raise HTTPException(404, "job not found")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/prices")
def api_get_job_prices(job_id: int, hours: int = 24):
    _get_job_or_404(job_id)
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be between 1 and 720")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return db.get_prices(job_id, since)


@app.get("/api/jobs/{job_id}/travel-times")
def api_get_job_travel_times(job_id: int, hours: int = 24):
    _get_job_or_404(job_id)
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be between 1 and 720")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return db.get_travel_times(job_id, since)


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
