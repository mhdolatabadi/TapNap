import logging
import re
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from . import bale, config, db
from .providers import neshan, snapp, tapsi
from .providers.errors import ProviderAuthError, ProviderError, ProviderRateLimited

log = logging.getLogger("tapnap.scheduler")

PROVIDERS = {"snapp": snapp.fetch, "tapsi": tapsi.fetch}

# Set once fetch_travel_times() hits Neshan's quota, cleared once the
# cooldown passes -- module-level and in-memory is fine here: it's a
# single-process scheduler, and losing it on restart just means one extra
# doomed attempt rather than any real inconsistency.
_neshan_backoff_until: datetime | None = None

# Highest Bale update_id processed so far + 1 -- same in-memory,
# single-process reasoning as _neshan_backoff_until above; losing this on
# restart just means Bale redelivers a few already-handled updates, and
# claim_bale_link_code() is a no-op for a code that's already been consumed.
_bale_update_offset: int | None = None

_BALE_START_RE = re.compile(r"^/start\s+(\S+)")


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
                _dispatch_alerts(job, name, r["service_name"], r["price"])

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


def _dispatch_alerts(job: dict, provider: str, service_name: str, price):
    """Checks every active price_alert on this job against the price just
    stored for (provider, service_name) and pings the owner's linked Bale
    chat when the threshold is crossed. Silently a no-op if BALE_BOT_TOKEN
    isn't configured, the alert's owner lost premium, or they never linked
    a chat -- same "just skip it" posture as an unconfigured Snapp/Tapsi
    token, rather than raising and interrupting the price-fetch loop."""
    if not config.BALE_BOT_TOKEN or price is None:
        return

    now = datetime.now(timezone.utc)
    for alert in db.get_active_alerts_for_job(job["id"]):
        if not alert["is_premium"] or not alert["bale_chat_id"]:
            continue
        if alert["provider"] is not None and alert["provider"] != provider:
            continue

        crossed = (
            price <= alert["threshold_toman"] if alert["direction"] == "below" else price >= alert["threshold_toman"]
        )
        if not crossed:
            continue

        if alert["last_triggered_at"]:
            last = datetime.fromisoformat(alert["last_triggered_at"])
            if now - last < timedelta(minutes=config.ALERT_COOLDOWN_MINUTES):
                continue

        direction_fa = "کمتر یا مساوی" if alert["direction"] == "below" else "بیشتر یا مساوی"
        text = (
            f"⏰ هشدار قیمت «{job['name']}»\n"
            f"{provider} / {service_name}: {price:,} تومان\n"
            f"({direction_fa} آستانه {alert['threshold_toman']:,} تومان)"
        )
        try:
            bale.send_message(config.BALE_BOT_TOKEN, alert["bale_chat_id"], text)
            db.mark_alert_triggered(alert["id"])
        except Exception:
            log.exception("bale: failed to send alert %s", alert["id"])


def poll_bale_updates():
    """Runs on its own short interval (see start_scheduler) to pick up
    /start <link_code> messages sent to the bot and complete account
    linking -- see db.set_bale_link_code/claim_bale_link_code and
    POST /api/bale/link-code. A no-op entirely if BALE_BOT_TOKEN isn't
    configured, matching how travel-time polling skips itself when
    NESHAN_API_KEY is unset."""
    global _bale_update_offset
    if not config.BALE_BOT_TOKEN:
        return

    try:
        updates = bale.get_updates(config.BALE_BOT_TOKEN, _bale_update_offset)
    except Exception:
        log.exception("bale: failed to poll updates")
        return

    for update in updates:
        _bale_update_offset = update["update_id"] + 1
        text = (update.get("message") or {}).get("text") or ""
        chat_id = (update.get("message") or {}).get("chat", {}).get("id")
        match = _BALE_START_RE.match(text)
        if not match or chat_id is None:
            continue

        user = db.claim_bale_link_code(match.group(1), str(chat_id))
        if user is not None:
            bale.send_message(config.BALE_BOT_TOKEN, str(chat_id), "✅ حساب Tapnap شما به این ربات وصل شد.")


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
    scheduler.add_job(
        poll_bale_updates,
        "interval",
        seconds=20,
        id="poll_bale_updates",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
