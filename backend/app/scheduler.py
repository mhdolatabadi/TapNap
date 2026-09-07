import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, db
from .providers import snapp, tapsi
from .providers.errors import ProviderAuthError, ProviderError

log = logging.getLogger("tapnap.scheduler")

PROVIDERS = {"snapp": snapp.fetch, "tapsi": tapsi.fetch}


def fetch_and_store():
    """Polls every job the user has switched on -- an inactive job is
    skipped entirely (no provider calls, no new fetch_log/prices rows)."""
    creds = config.load_credentials()

    for job in db.get_active_jobs():
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


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        fetch_and_store,
        "interval",
        minutes=config.FETCH_INTERVAL_MINUTES,
        id="fetch_prices",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
