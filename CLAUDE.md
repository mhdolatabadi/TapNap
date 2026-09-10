# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TapNap tracks and compares ride-hailing prices (Snapp vs Tapsi, Tehran) across multiple named, independently on/off-switchable polling jobs, each with its own origin/destination and its own price history/chart. FastAPI backend + static JS/HTML frontend, no build step, no accounts.

`tracker.py` (root) and `main.sh`/`.env.example`/root `requirements.txt` are an older standalone single-route CLI prototype (writes `data/prices.csv` + a matplotlib PNG), superseded by `backend/app`. Not part of the deployed app — don't extend it; treat the FastAPI app under `backend/app` as the real codebase.

## Running locally

```
pip install -r backend/requirements.txt
cd backend && DATA_DIR=../data uvicorn app.main:app --reload
```

Serves the API and the static frontend (mounted at `/`) from one process on port 8000. `DATA_DIR` (default `/app/data`) holds `tapnap.db` (sqlite) and `credentials.json` — create `data/credentials.json` from `data/credentials.example.json` with real Snapp/Tapsi tokens before prices will actually fetch. Travel-time polling (Neshan) needs `NESHAN_API_KEY` set as an env var (not in `credentials.json` — see Architecture below); unset, it's skipped with an auth-error row in `fetch_log` per mode, same as an unconfigured Snapp/Tapsi.

No test suite, linter, or frontend build step exists in this repo.

### Docker

```
docker compose up -d --build
```

`docker-compose.yml` builds from the root `Dockerfile`, bind-mounts `./data` to `/app/data`, reads `NESHAN_API_KEY`/`NESHAN_TRAVEL_MODES` from a `.env` file next to it on the deploy host (not committed — docker compose auto-loads it), and expects an external `proxynet` network (a reverse proxy is assumed to live outside this repo). Deploys automatically on push to `master` via `.github/workflows/deploy.yml`, which SSHes into the host and does `git reset --hard origin/master && docker compose up -d --build` — force-syncs the deploy host to whatever's on `master`, so don't push half-finished work there.

## Architecture

**Backend** (`backend/app/`):
- `main.py` — FastAPI app + all routes. Mounts `frontend/` as static files at `/`, lifespan hook runs `db.init_db()` and starts the APScheduler background job.
- `db.py` — raw sqlite3 (no ORM). Four tables: `jobs` (id, name, origin/destination, `active` on/off flag), `prices` (one row per provider/service per fetch, tagged with `job_id`), `fetch_log` (per-provider success/failure per poll, tagged with `job_id`), `travel_times` (one row per travel mode per fetch — `mode`, `duration_seconds`, `distance_meters`, `raw_json` — tagged with `job_id`). `init_db()` also runs one-time migrations (adding columns, and converting the old single-route-per-client `routes` table — and, older still, the single-route-for-everyone `route` table — into one job per saved route) — new schema changes should follow that pattern rather than assuming a fresh DB.
- `scheduler.py` — APScheduler background job (`FETCH_INTERVAL_MINUTES`, default 10) that calls `fetch_and_store()`: iterates every *active* job (`db.get_active_jobs()`) × every price provider, then once more per job for every mode in `NESHAN_TRAVEL_MODES` (`_fetch_travel_times`), and logs failures without killing the loop. A deactivated job keeps its history but stops accumulating new rows.
- `providers/snapp.py`, `providers/tapsi.py` — one `fetch(origin, destination, creds) -> (results, raw_json)` per provider, reverse-engineered from each service's own web app (see extensive comments in both files for endpoint shapes, header requirements, and auth quirks). Both normalize to Toman and both keep the raw response even when parsing yields nothing, so responses can be re-parsed later if the live shape drifts. `providers/errors.py` defines `ProviderError` (generic) vs `ProviderAuthError` (token/cookie needs a fresh login) — the scheduler and admin endpoints branch on this distinction.
- `providers/neshan.py` — travel-time/distance per mode via Neshan's *public, documented* Direction API (`GET /v4/direction`, `Api-Key` header) — unlike Snapp/Tapsi this isn't reverse-engineered, but its response shape also wasn't confirmed against a live key while writing it, so it follows the same defensive-parsing/keep-the-raw-response convention. One call per `(job, mode)` — `fetch_mode(origin, destination, api_key, mode) -> (leg_or_None, raw_json)`. `NESHAN_API_KEY` (env var, `config.py`) is a static app key, not a rotating user token, so — unlike Snapp/Tapsi — it doesn't live in `credentials.json`.
- `login_snapp.py` — Snapp's OTP login flow (`request_otp`/`submit_otp`), used both by the password-gated `/api/admin/snapp/*` endpoints and as a standalone CLI: `docker exec -it tapnap python -m app.login_snapp`. Tapsi has no equivalent login flow here — its cookie must be pasted into `credentials.json` manually from a real browser session.
- `config.py` — re-reads `credentials.json` from disk on every call (so rotating a token on disk takes effect without a restart) and merge-updates one provider's block at a time (important since token refresh happens concurrently with the scheduler).

**No accounts**: `/api/jobs` is fully open (anyone with the URL can create/toggle/delete jobs) — this app is assumed to sit behind a private reverse proxy, not exposed as a public multi-tenant service. Admin-only endpoints (`/api/admin/snapp/*`) are gated by an `X-Admin-Password` header checked via `hmac.compare_digest` against `admin_password` in `credentials.json` — if unset, those endpoints are simply unusable rather than open.

**Job status** shown in the UI/API is derived, not stored: `stopped` if `active` is false, `pending` if active but never fetched, `error` if the latest fetch_log entry for any provider failed, else `running` (see `main._job_status`).

**Frontend** (`frontend/`): plain JS/HTML/CSS, no framework or bundler, vendored Chart.js and Leaflet under `frontend/vendor/`. `app.js` renders the jobs list (status badges, activate/deactivate, delete-with-confirm), a map-based "new job" form, and the price/travel-time chart + status panels for whichever job is currently selected — both charts share the one `#range-select` control.

## Conventions worth knowing

- Provider responses are volatile (reverse-engineered, unconfirmed shapes) — both providers' `_extract_services` try a few plausible response shapes and silently return `[]` rather than raising when parsing fails; the raw JSON is always persisted to `prices`/kept in the fetch log regardless, specifically so a shape change can be diagnosed and re-parsed later.
- Snapp prices arrive in Rial and are divided by 10 to normalize to Toman (matching Tapsi's native unit) before storage — don't re-divide or double-convert if touching this code.
- Comments throughout justify *why* a header/field/quirk exists (e.g. WAF fingerprinting requirements, wrong-key silently-accepted API bugs) — read them before changing request shapes, since they encode findings from reverse-engineering the live APIs.
