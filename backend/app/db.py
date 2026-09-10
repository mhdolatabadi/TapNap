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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_job(name: str, origin: dict, destination: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO jobs
               (name, origin_lat, origin_lng, origin_label,
                destination_lat, destination_lng, destination_label,
                active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                name,
                origin["lat"], origin["lng"], origin.get("label"),
                destination["lat"], destination["lng"], destination.get("label"),
                now, now,
            ),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_job(row)


def get_jobs() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [_row_to_job(r) for r in rows]


def get_job(job_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row is not None else None


def get_active_jobs() -> list[dict]:
    """Only jobs the user has switched on -- the scheduler polls these,
    an inactive job keeps its history but stops accumulating new rows."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE active = 1 ORDER BY id").fetchall()
        return [_row_to_job(r) for r in rows]


def set_job_active(job_id: int, active: bool) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, datetime.now(timezone.utc).isoformat(), job_id),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row is not None else None


def delete_job(job_id: int) -> bool:
    """Drops the job itself; its price/fetch_log history is left in place
    (job_id just points at nothing) rather than cascading the delete, so
    old charts remain reconstructable from raw_json if ever needed."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
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
