import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, db
from .providers import snapp, tapsi
from .providers.errors import ProviderAuthError, ProviderError

log = logging.getLogger("tapnap.scheduler")

PROVIDERS = {"snapp": snapp.fetch, "tapsi": tapsi.fetch}


def fetch_and_store():
    """Polls every distinct route someone has saved -- there's no single
    "current" route anymore, so each one gets its own provider calls and
    its own fetch_log/prices rows (scoped by exact origin/destination)."""
    creds = config.load_credentials()

    for route in db.get_all_routes():
        origin, destination = route["origin"], route["destination"]

        for name, fetch_fn in PROVIDERS.items():
            try:
                results, raw = fetch_fn(origin, destination, creds.get(name, {}))
            except ProviderAuthError as e:
                log.warning("%s: auth error: %s", name, e)
                db.log_fetch(name, origin, destination, ok=False, message=f"auth: {e}")
                continue
            except ProviderError as e:
                log.warning("%s: error: %s", name, e)
                db.log_fetch(name, origin, destination, ok=False, message=str(e))
                continue
            except Exception as e:  # keep the loop alive no matter what
                log.exception("%s: unexpected error", name)
                db.log_fetch(name, origin, destination, ok=False, message=f"unexpected: {e}")
                continue

            for r in results:
                db.insert_price(name, origin, destination, r["service_name"], r["price"], raw)

            if results:
                db.log_fetch(name, origin, destination, ok=True, message=f"{len(results)} service(s)")
            else:
                db.log_fetch(
                    name, origin, destination, ok=True, message="0 services parsed from response (raw kept)"
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
