import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

DEFAULT_CLIENT_ID = "default"

SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    client_id TEXT PRIMARY KEY,
    origin_lat REAL NOT NULL,
    origin_lng REAL NOT NULL,
    origin_label TEXT,
    destination_lat REAL NOT NULL,
    destination_lng REAL NOT NULL,
    destination_label TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    provider TEXT NOT NULL,
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
    origin_lat REAL,
    origin_lng REAL,
    destination_lat REAL,
    destination_lng REAL,
    ok INTEGER NOT NULL,
    message TEXT
);

CREATE INDEX IF NOT EXISTS idx_prices_checked_at ON prices (checked_at);
CREATE INDEX IF NOT EXISTS idx_prices_route
    ON prices (origin_lat, origin_lng, destination_lat, destination_lng);
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
        for col in ("origin_lat", "origin_lng", "destination_lat", "destination_lng"):
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE fetch_log ADD COLUMN {col} REAL")

        # One-time migration off the old single-route-for-everyone table
        # (every visitor used to see whoever saved last): move its row,
        # if any, into the new per-client "default" bucket, then drop it.
        old = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='route'"
        ).fetchone()
        if old is not None:
            legacy = conn.execute("SELECT * FROM route WHERE id = 1").fetchone()
            if legacy is not None:
                conn.execute(
                    """INSERT OR IGNORE INTO routes
                       (client_id, origin_lat, origin_lng, origin_label,
                        destination_lat, destination_lng, destination_label, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        DEFAULT_CLIENT_ID,
                        legacy["origin_lat"], legacy["origin_lng"], legacy["origin_label"],
                        legacy["destination_lat"], legacy["destination_lng"], legacy["destination_label"],
                        legacy["updated_at"],
                    ),
                )
            conn.execute("DROP TABLE route")

        row = conn.execute("SELECT 1 FROM routes WHERE client_id = ?", (DEFAULT_CLIENT_ID,)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO routes
                   (client_id, origin_lat, origin_lng, origin_label,
                    destination_lat, destination_lng, destination_label, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    DEFAULT_CLIENT_ID,
                    config.DEFAULT_ORIGIN["lat"],
                    config.DEFAULT_ORIGIN["lng"],
                    config.DEFAULT_ORIGIN["label"],
                    config.DEFAULT_DESTINATION["lat"],
                    config.DEFAULT_DESTINATION["lng"],
                    config.DEFAULT_DESTINATION["label"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


def _row_to_route(row: sqlite3.Row) -> dict:
    return {
        "origin": {"lat": row["origin_lat"], "lng": row["origin_lng"], "label": row["origin_label"]},
        "destination": {
            "lat": row["destination_lat"],
            "lng": row["destination_lng"],
            "label": row["destination_label"],
        },
        "updated_at": row["updated_at"],
    }


def get_route(client_id: str) -> dict:
    """Each browser gets its own saved route, keyed by a client_id it
    generates itself (no accounts/login) -- falls back to the shared
    "default" bucket for a client_id that hasn't saved one yet."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM routes WHERE client_id = ?", (client_id,)).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM routes WHERE client_id = ?", (DEFAULT_CLIENT_ID,)).fetchone()
        return _row_to_route(row)


def set_route(client_id: str, origin: dict, destination: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO routes
                 (client_id, origin_lat, origin_lng, origin_label,
                  destination_lat, destination_lng, destination_label, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(client_id) DO UPDATE SET
                 origin_lat = excluded.origin_lat,
                 origin_lng = excluded.origin_lng,
                 origin_label = excluded.origin_label,
                 destination_lat = excluded.destination_lat,
                 destination_lng = excluded.destination_lng,
                 destination_label = excluded.destination_label,
                 updated_at = excluded.updated_at""",
            (
                client_id,
                origin["lat"],
                origin["lng"],
                origin.get("label"),
                destination["lat"],
                destination["lng"],
                destination.get("label"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_all_routes() -> list[dict]:
    """Every distinct saved route -- the scheduler polls prices for each
    one, since there's no single "current" route anymore."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM routes").fetchall()
        return [_row_to_route(r) for r in rows]


def insert_price(provider: str, origin: dict, destination: dict, service_name: str, price, raw_json: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO prices
               (checked_at, provider, origin_lat, origin_lng, destination_lat, destination_lng,
                service_name, price, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                provider,
                origin["lat"],
                origin["lng"],
                destination["lat"],
                destination["lng"],
                service_name,
                price,
                raw_json,
            ),
        )


def log_fetch(provider: str, origin: dict, destination: dict, ok: bool, message: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO fetch_log
               (checked_at, provider, origin_lat, origin_lng, destination_lat, destination_lng, ok, message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                provider,
                origin["lat"],
                origin["lng"],
                destination["lat"],
                destination["lng"],
                1 if ok else 0,
                message,
            ),
        )


def get_prices(since_iso: str, origin: dict, destination: dict):
    """Scoped to one exact origin/destination pair -- each saved route
    builds its own independent price history rather than all routes
    ever fetched being merged into one chart."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT checked_at, provider, service_name, price
               FROM prices
               WHERE checked_at >= ?
                 AND origin_lat = ? AND origin_lng = ?
                 AND destination_lat = ? AND destination_lng = ?
               ORDER BY checked_at ASC""",
            (since_iso, origin["lat"], origin["lng"], destination["lat"], destination["lng"]),
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_fetch_status(origin: dict, destination: dict):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT provider, ok, message, checked_at FROM fetch_log
               WHERE origin_lat = ? AND origin_lng = ?
                 AND destination_lat = ? AND destination_lng = ?
                 AND id IN (
                   SELECT MAX(id) FROM fetch_log
                   WHERE origin_lat = ? AND origin_lng = ?
                     AND destination_lat = ? AND destination_lng = ?
                   GROUP BY provider
                 )""",
            (
                origin["lat"], origin["lng"], destination["lat"], destination["lng"],
                origin["lat"], origin["lng"], destination["lat"], destination["lng"],
            ),
        ).fetchall()
        return [dict(r) for r in rows]
