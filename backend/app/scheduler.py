import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, db
from .providers import neshan, snapp, tapsi
from .providers.errors import ProviderAuthError, ProviderError, ProviderRateLimited

log = logging.getLogger("tapnap.scheduler")

PROVIDERS = {"snapp": snapp.fetch, "tapsi": tapsi.fetch}

# Set once fetch_travel_times() hits Neshan's quota, cleared once the
# cooldown passes -- module-level and in-memory is fine here: it's a
# single-process scheduler, and losing it on restart just means one extra
# doomed attempt rather than any real inconsistency.
_neshan_backoff_until: datetime | None = None


def fetch_prices():
    """Polls every active job with price-tracking on -- runs on its own
    schedule (FETCH_INTERVAL_MINUTES) separate from travel times, see
    fetch_travel_times()."""
    creds = config.load_credentials()

    for job in db.get_active_jobs():
        if not job["track_price"]:
            continue
        job_id = job["id"]
        origin, destination = job["origin"], job["destination"]

        for name, fetch_fn in PROVIDERS.items():
            try:
                results, raw = fetch_fn(origin, destination, creds.get(name, {}))
            except ProviderAuthError as e:
                log.warning("%s: auth error: %s", name, e)
                db.log_fetch(name, job_id, origin, destination, ok=False, message=f"auth: {e}")
                continue
            except ProviderError as e:
                log.warning("%s: error: %s", name, e)
                db.log_fetch(name, job_id, origin, destination, ok=False, message=str(e))
                continue
            except Exception as e:  # keep the loop alive no matter what
                log.exception("%s: unexpected error", name)
                db.log_fetch(name, job_id, origin, destination, ok=False, message=f"unexpected: {e}")
                continue

            for r in results:
                db.insert_price(name, job_id, origin, destination, r["service_name"], r["price"], raw)

            if results:
                db.log_fetch(name, job_id, origin, destination, ok=True, message=f"{len(results)} service(s)")
            else:
                db.log_fetch(
                    name, job_id, origin, destination, ok=True,
                    message="0 services parsed from response (raw kept)",
                )


def fetch_travel_times():
    """Polls every active job with travel-time-tracking on -- runs on its
    own, slower schedule (NESHAN_FETCH_INTERVAL_MINUTES) since Neshan's
    quota is far tighter than Snapp/Tapsi's and traffic doesn't need
    price-cycle granularity anyway (see config.py). Backs off entirely for
    NESHAN_BACKOFF_MINUTES the moment the key's quota is hit -- one API key
    is shared by every job, so once it's exhausted there's no point
    burning more attempts on the rest of the list this cycle."""
    global _neshan_backoff_until
    now = datetime.now(timezone.utc)
    if _neshan_backoff_until is not None:
        if now < _neshan_backoff_until:
            return
        _neshan_backoff_until = None

    for job in db.get_active_jobs():
        if not job["track_travel_time"]:
            continue
        rate_limited = _fetch_travel_times(job["id"], job["origin"], job["destination"])
        if rate_limited:
            _neshan_backoff_until = now + timedelta(minutes=config.NESHAN_BACKOFF_MINUTES)
            log.warning("neshan: quota exceeded, backing off until %s", _neshan_backoff_until.isoformat())
            break


def fetch_and_store():
    """On-demand refresh (see POST /api/fetch-now) -- runs both price and
    travel-time fetches right now regardless of their separate scheduled
    cadences, still respecting per-job on/off flags and Neshan's backoff."""
    fetch_prices()
    fetch_travel_times()


def _fetch_travel_times(job_id: int, origin: dict, destination: dict) -> bool:
    """One Neshan Direction call per configured mode (config.NESHAN_TRAVEL_MODES),
    each logged to fetch_log under its own "neshan_<mode>" provider tag --
    same shape as the price providers above, just one HTTP call per mode
    instead of one call covering every service at once. Missing/rejected
    API key surfaces as an ordinary auth-error fetch_log row (visible in
    the job's status panel) rather than being silently skipped, matching
    how Snapp/Tapsi behave with no credentials configured.

    Returns True if Neshan's quota was hit (caller should stop trying
    further jobs this cycle and start the backoff), else False."""
    for mode in config.NESHAN_TRAVEL_MODES:
        provider_tag = f"neshan_{mode}"
        try:
            leg, raw = neshan.fetch_mode(origin, destination, config.NESHAN_API_KEY, mode)
        except ProviderRateLimited as e:
            log.warning("%s: rate limited: %s", provider_tag, e)
            db.log_fetch(provider_tag, job_id, origin, destination, ok=False, message=f"rate limited: {e}")
            return True
        except ProviderAuthError as e:
            log.warning("%s: auth error: %s", provider_tag, e)
            db.log_fetch(provider_tag, job_id, origin, destination, ok=False, message=f"auth: {e}")
            continue
        except ProviderError as e:
            log.warning("%s: error: %s", provider_tag, e)
            db.log_fetch(provider_tag, job_id, origin, destination, ok=False, message=str(e))
            continue
        except Exception as e:  # keep the loop alive no matter what
            log.exception("%s: unexpected error", provider_tag)
            db.log_fetch(provider_tag, job_id, origin, destination, ok=False, message=f"unexpected: {e}")
            continue

        if leg is not None:
            db.insert_travel_time(
                job_id, origin, destination, mode, leg["duration_seconds"], leg.get("distance_meters"), raw
            )
            db.log_fetch(provider_tag, job_id, origin, destination, ok=True, message=f"{leg['duration_seconds']}s")
        else:
            db.log_fetch(
                provider_tag, job_id, origin, destination, ok=True,
                message="no route parsed from response (raw kept)",
            )
    return False


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        fetch_prices,
        "interval",
        minutes=config.FETCH_INTERVAL_MINUTES,
        id="fetch_prices",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        fetch_travel_times,
        "interval",
        minutes=config.NESHAN_FETCH_INTERVAL_MINUTES,
        id="fetch_travel_times",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
