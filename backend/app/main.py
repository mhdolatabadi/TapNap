import hmac
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, config, db
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


class SignupIn(BaseModel):
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


SESSION_COOKIE = "session"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _current_user(session: str | None = Cookie(default=None)) -> dict:
    """Every /api/jobs* route requires a logged-in, approved account --
    this app moved from "no accounts, sits behind a private proxy" to
    "public signup, admin-approved" (see db.create_user/approve_user), so
    a job now always belongs to whoever created it."""
    user = db.get_session_user(session) if session else None
    if user is None:
        raise HTTPException(401, "login required")
    if not user["approved"]:
        raise HTTPException(403, "account pending admin approval")
    return user


def _check_admin_password(x_admin_password: str | None):
    # Separate from the account system entirely (approving a signup is an
    # operator action, not something one user-account can do to another) --
    # this is the only thing standing between a public visitor and
    # triggering a real SMS OTP to an arbitrary phone number (an open
    # OTP-spam relay), overwriting the stored Snapp session, or approving
    # their own account. If no password is configured, admin endpoints are
    # simply unusable rather than falling open.
    expected = config.load_credentials().get("admin_password")
    if not expected or not x_admin_password or not hmac.compare_digest(x_admin_password, expected):
        raise HTTPException(401, "invalid admin password")


def _get_job_or_404(job_id: int, user_id: int) -> dict:
    # 404, not 403, for a job that belongs to someone else -- doesn't
    # confirm to a logged-in user that a given job ID even exists.
    job = db.get_job(job_id, user_id)
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
def api_list_jobs(user: dict = Depends(_current_user)):
    jobs = db.get_jobs(user["id"])
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
def api_create_job(job: JobIn, user: dict = Depends(_current_user)):
    name = job.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    if db.count_jobs_for_user(user["id"]) >= db.MAX_JOBS_PER_USER:
        raise HTTPException(403, "هر حساب حداکثر ۳ مسیر می‌تونه داشته باشه")
    return db.create_job(user["id"], name, job.origin.model_dump(), job.destination.model_dump())


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: int, user: dict = Depends(_current_user)):
    job = _get_job_or_404(job_id, user["id"])
    last_fetch = db.get_last_fetch_status(job_id)
    return {
        **job,
        "status": _job_status(job, last_fetch),
        "last_fetch": last_fetch,
        "latest_prices": db.get_latest_prices(job_id),
    }


@app.patch("/api/jobs/{job_id}")
def api_set_job_active(job_id: int, body: JobActiveIn, user: dict = Depends(_current_user)):
    _get_job_or_404(job_id, user["id"])
    job = db.set_job_active(job_id, user["id"], body.active)
    return {**job, "status": _job_status(job, db.get_last_fetch_status(job_id))}


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: int, user: dict = Depends(_current_user)):
    if not db.delete_job(job_id, user["id"]):
        raise HTTPException(404, "job not found")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/prices")
def api_get_job_prices(job_id: int, hours: int = 24, user: dict = Depends(_current_user)):
    _get_job_or_404(job_id, user["id"])
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be between 1 and 720")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return db.get_prices(job_id, since)


@app.get("/api/jobs/{job_id}/travel-times")
def api_get_job_travel_times(job_id: int, hours: int = 24, user: dict = Depends(_current_user)):
    _get_job_or_404(job_id, user["id"])
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be between 1 and 720")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return db.get_travel_times(job_id, since)


@app.post("/api/auth/signup")
def api_signup(body: SignupIn, response: Response):
    email = _normalize_email(body.email)
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "ایمیل معتبر نیست")
    if len(body.password) < 8:
        raise HTTPException(400, "رمز باید حداقل ۸ کاراکتر باشه")
    if db.get_user_by_email(email) is not None:
        raise HTTPException(409, "این ایمیل قبلاً ثبت شده")

    user = db.create_user(email, auth.hash_password(body.password))
    if not user["approved"]:
        # Pending accounts don't get a session -- nothing to log into yet.
        return {"approved": False}

    token = db.create_session(user["id"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365, path="/")
    return {"approved": True, "email": user["email"]}


@app.post("/api/auth/login")
def api_login(body: LoginIn, response: Response):
    email = _normalize_email(body.email)
    user = db.get_user_by_email(email)
    if user is None or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "ایمیل یا رمز اشتباهه")
    if not user["approved"]:
        raise HTTPException(403, "حساب شما هنوز توسط مدیر تایید نشده")

    token = db.create_session(user["id"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365, path="/")
    return {"email": user["email"]}


@app.post("/api/auth/logout")
def api_logout(response: Response, session: str | None = Cookie(default=None)):
    if session:
        db.delete_session(session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(user: dict = Depends(_current_user)):
    return {"email": user["email"]}


@app.post("/api/fetch-now")
def api_fetch_now(user: dict = Depends(_current_user)):
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


@app.get("/api/admin/users/pending")
def api_list_pending_users(x_admin_password: str | None = Header(default=None)):
    _check_admin_password(x_admin_password)
    return db.get_pending_users()


@app.post("/api/admin/users/{user_id}/approve")
def api_approve_user(user_id: int, x_admin_password: str | None = Header(default=None)):
    _check_admin_password(x_admin_password)
    if not db.approve_user(user_id):
        raise HTTPException(404, "user not found")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/reject")
def api_reject_user(user_id: int, x_admin_password: str | None = Header(default=None)):
    _check_admin_password(x_admin_password)
    if not db.reject_user(user_id):
        raise HTTPException(404, "pending user not found")
    return {"ok": True}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
