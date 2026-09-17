import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    origin_lat REAL NOT NULL,
    origin_lng REAL NOT NULL,
    origin_label TEXT,
    destination_lat REAL NOT NULL,
    destination_lng REAL NOT NULL,
    destination_label TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    -- Non-NULL only on an auto-generated return leg: the id of the job it
    -- mirrors (origin/destination swapped). A base job never sets this on
    -- itself -- "does job X have a return leg" is a reverse lookup
    -- (WHERE paired_job_id = X.id), so the pairing lives in exactly one
    -- place and can't desync between the two rows.
    paired_job_id INTEGER,
    -- Independent on/off switches for the two kinds of polling a job can
    -- do -- e.g. keep Snapp/Tapsi price checks on but turn Neshan travel
    -- time off for a job once its quota is exhausted, without losing price
    -- history or having to delete/recreate the job. Both default on so
    -- existing jobs keep behaving exactly as before this column existed.
    track_price INTEGER NOT NULL DEFAULT 1,
    track_travel_time INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    -- The first account ever created is auto-approved (there's no admin yet
    -- to approve them) and becomes the owner of every job that existed
    -- before accounts did -- see create_user(). Everyone after that starts
    -- unapproved until an admin approves them (see approve_user()).
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    job_id INTEGER,
    origin_lat REAL,
    origin_lng REAL,
    destination_lat REAL,
    destination_lng REAL,
    service_name TEXT,
    price INTEGER,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    job_id INTEGER,
    origin_lat REAL,
    origin_lng REAL,
    destination_lat REAL,
    destination_lng REAL,
    ok INTEGER NOT NULL,
    message TEXT
);

CREATE TABLE IF NOT EXISTS travel_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    job_id INTEGER,
    mode TEXT NOT NULL,
    origin_lat REAL,
    origin_lng REAL,
    destination_lat REAL,
    destination_lng REAL,
    duration_seconds INTEGER,
    distance_meters INTEGER,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_prices_checked_at ON prices (checked_at);
"""

# Indexes on job_id are created separately, after the ALTER TABLE column
# migration below runs -- job_id doesn't exist yet on a table that
# predates it, and CREATE INDEX would fail before that column is added.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_prices_job ON prices (job_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_fetch_log_job ON fetch_log (job_id);
CREATE INDEX IF NOT EXISTS idx_travel_times_job ON travel_times (job_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs (user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_paired ON jobs (paired_job_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
"""


@contextmanager
def get_conn():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)

        # fetch_log predates the origin/destination columns -- CREATE TABLE
        # IF NOT EXISTS is a no-op on an existing table, so an older DB
        # needs them added explicitly.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(fetch_log)")}
        for col in ("origin_lat", "origin_lng", "destination_lat", "destination_lng", "job_id"):
            if col not in existing_cols:
                col_type = "INTEGER" if col == "job_id" else "REAL"
                conn.execute(f"ALTER TABLE fetch_log ADD COLUMN {col} {col_type}")

        price_cols = {row["name"] for row in conn.execute("PRAGMA table_info(prices)")}
        if "job_id" not in price_cols:
            conn.execute("ALTER TABLE prices ADD COLUMN job_id INTEGER")

        job_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "user_id" not in job_cols:
            # NULL here means "predates accounts" -- create_user() assigns
            # every such job to the first account ever created.
            conn.execute("ALTER TABLE jobs ADD COLUMN user_id INTEGER")
        if "paired_job_id" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN paired_job_id INTEGER")
        if "track_price" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN track_price INTEGER NOT NULL DEFAULT 1")
        if "track_travel_time" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN track_travel_time INTEGER NOT NULL DEFAULT 1")

        conn.executescript(INDEXES)

        # One-time migration off the very old single-route-for-everyone
        # table (every visitor used to see whoever saved last): move its
        # row, if any, straight into a job, then drop it.
        old = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='route'"
        ).fetchone()
        if old is not None:
            legacy = conn.execute("SELECT * FROM route WHERE id = 1").fetchone()
            if legacy is not None:
                if legacy["origin_label"] and legacy["destination_label"]:
                    name = f"{legacy['origin_label']} → {legacy['destination_label']}"
                else:
                    name = legacy["origin_label"] or legacy["destination_label"] or "Job 1"
                conn.execute(
                    """INSERT INTO jobs
                       (name, origin_lat, origin_lng, origin_label,
                        destination_lat, destination_lng, destination_label,
                        active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        name,
                        legacy["origin_lat"], legacy["origin_lng"], legacy["origin_label"],
                        legacy["destination_lat"], legacy["destination_lng"], legacy["destination_label"],
                        legacy["updated_at"], legacy["updated_at"],
                    ),
                )
            conn.execute("DROP TABLE route")

        # One-time migration off the per-client "routes" table (one route
        # per browser, no name, no on/off state) to named, independently
        # schedulable jobs. Each existing route becomes one job; existing
        # prices/fetch_log rows are matched to their job by exact lat/lng
        # and tagged with job_id so per-job history keeps working.
        old_routes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='routes'"
        ).fetchone()
        if old_routes is not None:
            for i, r in enumerate(conn.execute("SELECT * FROM routes").fetchall(), start=1):
                if r["origin_label"] and r["destination_label"]:
                    name = f"{r['origin_label']} → {r['destination_label']}"
                else:
                    name = r["origin_label"] or r["destination_label"] or f"Job {i}"
                cur = conn.execute(
                    """INSERT INTO jobs
                       (name, origin_lat, origin_lng, origin_label,
                        destination_lat, destination_lng, destination_label,
                        active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        name,
                        r["origin_lat"], r["origin_lng"], r["origin_label"],
                        r["destination_lat"], r["destination_lng"], r["destination_label"],
                        r["updated_at"], r["updated_at"],
                    ),
                )
                job_id = cur.lastrowid
                for table in ("prices", "fetch_log"):
                    conn.execute(
                        f"""UPDATE {table} SET job_id = ?
                            WHERE job_id IS NULL AND origin_lat = ? AND origin_lng = ?
                              AND destination_lat = ? AND destination_lng = ?""",
                        (job_id, r["origin_lat"], r["origin_lng"], r["destination_lat"], r["destination_lng"]),
                    )
            conn.execute("DROP TABLE routes")

        # Fresh install with no migrated data at all -- seed one default
        # job so the app isn't empty out of the box.
        row = conn.execute("SELECT 1 FROM jobs LIMIT 1").fetchone()
        if row is None:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO jobs
                   (name, origin_lat, origin_lng, origin_label,
                    destination_lat, destination_lng, destination_label,
                    active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    f"{config.DEFAULT_ORIGIN['label']} → {config.DEFAULT_DESTINATION['label']}",
                    config.DEFAULT_ORIGIN["lat"],
                    config.DEFAULT_ORIGIN["lng"],
                    config.DEFAULT_ORIGIN["label"],
                    config.DEFAULT_DESTINATION["lat"],
                    config.DEFAULT_DESTINATION["lng"],
                    config.DEFAULT_DESTINATION["label"],
                    now,
                    now,
                ),
            )


def _row_to_job(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "origin": {"lat": row["origin_lat"], "lng": row["origin_lng"], "label": row["origin_label"]},
        "destination": {
            "lat": row["destination_lat"],
            "lng": row["destination_lng"],
            "label": row["destination_label"],
        },
        "active": bool(row["active"]),
        "paired_job_id": row["paired_job_id"],
        "track_price": bool(row["track_price"]),
        "track_travel_time": bool(row["track_travel_time"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


MAX_JOBS_PER_USER = 3


def create_job(
    user_id: int, name: str, origin: dict, destination: dict,
    track_price: bool = True, track_travel_time: bool = True,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO jobs
               (user_id, name, origin_lat, origin_lng, origin_label,
                destination_lat, destination_lng, destination_label,
                active, track_price, track_travel_time, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (
                user_id, name,
                origin["lat"], origin["lng"], origin.get("label"),
                destination["lat"], destination["lng"], destination.get("label"),
                1 if track_price else 0, 1 if track_travel_time else 0,
                now, now,
            ),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_job(row)


def count_jobs_for_user(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE user_id = ?", (user_id,)).fetchone()["n"]


def get_jobs(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        return [_row_to_job(r) for r in rows]


def get_job(job_id: int, user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()
        return _row_to_job(row) if row is not None else None


def get_active_jobs() -> list[dict]:
    """Every switched-on job across every account -- the scheduler polls
    these regardless of owner; an inactive job keeps its history but stops
    accumulating new rows."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE active = 1 ORDER BY id").fetchall()
        return [_row_to_job(r) for r in rows]


def set_job_active(job_id: int, user_id: int, active: bool) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET active = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (1 if active else 0, datetime.now(timezone.utc).isoformat(), job_id, user_id),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()
        return _row_to_job(row) if row is not None else None


def set_job_tracking(job_id: int, user_id: int, track_price: bool, track_travel_time: bool) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET track_price = ?, track_travel_time = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (1 if track_price else 0, 1 if track_travel_time else 0, datetime.now(timezone.utc).isoformat(),
             job_id, user_id),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()
        return _row_to_job(row) if row is not None else None


def delete_job(job_id: int, user_id: int) -> bool:
    """Drops the job itself; its price/fetch_log history is left in place
    (job_id just points at nothing) rather than cascading the delete, so
    old charts remain reconstructable from raw_json if ever needed. Its
    return leg (if any) IS cascaded, though -- an orphaned mirror job with
    no way to reach it from the UI would just poll forever unnoticed."""
    with get_conn() as conn:
        conn.execute("DELETE FROM jobs WHERE paired_job_id = ? AND user_id = ?", (job_id, user_id))
        cur = conn.execute("DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        return cur.rowcount > 0


def get_return_leg(base_job_id: int, user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE paired_job_id = ? AND user_id = ?", (base_job_id, user_id)
        ).fetchone()
        return _row_to_job(row) if row is not None else None


def create_return_leg(user_id: int, base_job: dict) -> dict:
    """Mirrors base_job's origin/destination and links back to it via
    paired_job_id -- from there on it's an ordinary job (schedulable,
    deletable, chartable on its own) that just happens to know what it's
    the return leg of."""
    now = datetime.now(timezone.utc).isoformat()
    name = f"{base_job['name']} (برگشت)"
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO jobs
               (user_id, name, origin_lat, origin_lng, origin_label,
                destination_lat, destination_lng, destination_label,
                active, paired_job_id, track_price, track_travel_time, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
            (
                user_id, name,
                base_job["destination"]["lat"], base_job["destination"]["lng"], base_job["destination"]["label"],
                base_job["origin"]["lat"], base_job["origin"]["lng"], base_job["origin"]["label"],
                base_job["id"],
                1 if base_job["track_price"] else 0, 1 if base_job["track_travel_time"] else 0,
                now, now,
            ),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_job(row)


def delete_return_leg(base_job_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE paired_job_id = ? AND user_id = ?", (base_job_id, user_id))
        return cur.rowcount > 0


def insert_price(provider: str, job_id: int, origin: dict, destination: dict, service_name: str, price, raw_json: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO prices
               (checked_at, provider, job_id, origin_lat, origin_lng, destination_lat, destination_lng,
                service_name, price, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                provider,
                job_id,
                origin["lat"],
                origin["lng"],
                destination["lat"],
                destination["lng"],
                service_name,
                price,
                raw_json,
            ),
        )


def log_fetch(provider: str, job_id: int, origin: dict, destination: dict, ok: bool, message: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO fetch_log
               (checked_at, provider, job_id, origin_lat, origin_lng, destination_lat, destination_lng, ok, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                provider,
                job_id,
                origin["lat"],
                origin["lng"],
                destination["lat"],
                destination["lng"],
                1 if ok else 0,
                message,
            ),
        )


def get_prices(job_id: int, since_iso: str):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT checked_at, provider, service_name, price
               FROM prices
               WHERE job_id = ? AND checked_at >= ?
               ORDER BY checked_at ASC""",
            (job_id, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]


def insert_travel_time(
    job_id: int, origin: dict, destination: dict, mode: str, duration_seconds: int, distance_meters, raw_json: str
):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO travel_times
               (checked_at, job_id, mode, origin_lat, origin_lng, destination_lat, destination_lng,
                duration_seconds, distance_meters, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                job_id,
                mode,
                origin["lat"],
                origin["lng"],
                destination["lat"],
                destination["lng"],
                duration_seconds,
                distance_meters,
                raw_json,
            ),
        )


def get_travel_times(job_id: int, since_iso: str):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT checked_at, mode, duration_seconds, distance_meters
               FROM travel_times
               WHERE job_id = ? AND checked_at >= ?
               ORDER BY checked_at ASC""",
            (job_id, since_iso),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_prices(job_id: int):
    """Latest price for each provider's "standard" service -- one row per
    provider, not per (provider, service_name) -- for the job card's price
    snapshot. "Standard" is whichever service_name that provider first ever
    reported for this job: providers list their ride categories in the same
    order every poll, so this stays stable, and it lines up with the same
    notion the chart already uses to pick its default-visible series (see
    lineDatasets() in app.js). Taking the cheapest across every service
    instead would let a pricier premium option -- or, before the Snapp
    zero-price-service fix, a momentarily unavailable one -- get shown on
    the job card as if it were the normal fare."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT p.provider, p.service_name, p.price, p.checked_at
               FROM prices p
               JOIN (
                   SELECT provider, service_name FROM prices
                   WHERE job_id = ? AND id IN (SELECT MIN(id) FROM prices WHERE job_id = ? GROUP BY provider)
               ) std ON std.provider = p.provider AND std.service_name = p.service_name
               WHERE p.job_id = ?
                 AND p.id IN (SELECT MAX(id) FROM prices WHERE job_id = ? GROUP BY provider, service_name)
               ORDER BY p.provider""",
            (job_id, job_id, job_id, job_id),
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_fetch_status(job_id: int):
    """Latest fetch_log row per provider for this job -- used both for the
    status panel and to decide whether the job is currently "running" or
    has an "error" (see main.job_status)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT provider, ok, message, checked_at FROM fetch_log
               WHERE job_id = ?
                 AND id IN (SELECT MAX(id) FROM fetch_log WHERE job_id = ? GROUP BY provider)""",
            (job_id, job_id),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- accounts ----


def _row_to_user(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "email": row["email"], "approved": bool(row["approved"]), "created_at": row["created_at"]}


def create_user(email: str, password_hash: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        is_first = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, approved, created_at) VALUES (?, ?, ?, ?)",
            (email, password_hash, 1 if is_first else 0, now),
        )
        user_id = cur.lastrowid
        if is_first:
            conn.execute("UPDATE jobs SET user_id = ? WHERE user_id IS NULL", (user_id,))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row)


def get_user_by_email(email: str) -> dict | None:
    """Includes password_hash (unlike _row_to_user's public shape) --
    for login's own verify_password() call, not for API responses."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row is not None else None


def get_pending_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, email, created_at FROM users WHERE approved = 0 ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def approve_user(user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE users SET approved = 1 WHERE id = ?", (user_id,))
        return cur.rowcount > 0


def reject_user(user_id: int) -> bool:
    """Only ever deletes a still-pending signup -- never an approved
    account, so this can't be used to silently deactivate someone."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ? AND approved = 0", (user_id,))
        return cur.rowcount > 0


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, now))
    return token


def get_session_user(token: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token = ?""",
            (token,),
        ).fetchone()
        return _row_to_user(row) if row is not None else None


def delete_session(token: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
