"""Bale (ir.bale.ai) Bot API client -- used for price-alert delivery.

Bale's Bot API is a same-shaped clone of Telegram's (same endpoint
layout, same getUpdates/sendMessage semantics), just served from
tapi.bale.ai instead of api.telegram.org. There's no webhook endpoint in
this app, so linking and delivery both go through polling
(getUpdates with an offset) rather than a push callback -- simpler to run
inside the existing APScheduler loop (see scheduler.poll_bale_updates)
without needing a public HTTPS callback URL.
"""
import httpx

API_BASE = "https://tapi.bale.ai/bot{token}"


def get_updates(token: str, offset: int | None) -> list[dict]:
    """Long-poll-free short poll (timeout=0) -- called on our own interval
    from the scheduler rather than blocking a worker on Bale's long-poll,
    so a slow/unreachable Bale doesn't stall the price-fetch loop next to
    it. offset is the highest update_id seen so far + 1, so already-seen
    updates aren't redelivered."""
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    resp = httpx.get(f"{API_BASE.format(token=token)}/getUpdates", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", []) if data.get("ok") else []


def send_message(token: str, chat_id: str, text: str) -> bool:
    resp = httpx.post(
        f"{API_BASE.format(token=token)}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    return resp.status_code == 200 and resp.json().get("ok", False)
