#!/usr/bin/env python3
import csv
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests
from dotenv import load_dotenv

load_dotenv()

ORIGIN_LAT = float(os.environ["ORIGIN_LAT"])
ORIGIN_LONG = float(os.environ["ORIGIN_LONG"])
DEST_LAT = float(os.environ["DEST_LAT"])
DEST_LONG = float(os.environ["DEST_LONG"])
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", 600))

TAPSI_ACCESS_TOKEN = os.environ["TAPSI_ACCESS_TOKEN"]
TAPSI_REFRESH_TOKEN = os.environ["TAPSI_REFRESH_TOKEN"]
SNAPP_ACCESS_TOKEN = os.environ["SNAPP_ACCESS_TOKEN"]

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "prices.csv"
CHART_PATH = DATA_DIR / "prices.png"


def fetch_tapsi_price():
    resp = requests.post(
        "https://api.tapsi.cab/api/v3/ride/preview",
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0",
            "Accept": "*/*",
            "Origin": "https://app.tapsi.cab",
            "Cookie": f"accessToken={TAPSI_ACCESS_TOKEN}; refreshToken={TAPSI_REFRESH_TOKEN}",
        },
        json={
            "origin": {"latitude": ORIGIN_LAT, "longitude": ORIGIN_LONG},
            "destinations": [{"latitude": DEST_LAT, "longitude": DEST_LONG}],
            "hasReturn": False,
            "waitingTime": 0,
            "gateway": "CAB",
            "initiatedVia": "WEB",
            "metadata": {"flowType": "DESTINATION_FIRST", "previewType": "ORIGIN_FIRST"},
        },
        timeout=15,
    )
    resp.raise_for_status()
    price = resp.json()["data"]["categories"][0]["items"][0]["service"]["prices"][0]
    return price["passengerShare"]


def fetch_snapp_price():
    resp = requests.post(
        "https://app.snapp.taxi/api/api-base/v2/passenger/newprice/s/6/0",
        headers={
            "Content-Type": "application/json",
            "Origin": "https://app.snapp.taxi",
            "Authorization": f"Bearer {SNAPP_ACCESS_TOKEN}",
        },
        json={
            "points": [
                {"lat": ORIGIN_LAT, "lng": ORIGIN_LONG},
                {"lat": DEST_LAT, "lng": DEST_LONG},
                None,
            ],
            "voucher_code": None,
            "service_types": [1, 2],
            "priceriderecom": False,
            "tag": "0",
            "serviceType": 1,
            "hurryRaised": 0,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["data"]["prices"][0]["final"]


def append_row(timestamp, tapsi_price, snapp_price):
    DATA_DIR.mkdir(exist_ok=True)
    is_new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "tapsi_price", "snapp_price"])
        writer.writerow([timestamp.isoformat(), tapsi_price, snapp_price])


def render_chart():
    if not CSV_PATH.exists():
        return

    timestamps, tapsi_prices, snapp_prices = [], [], []
    with CSV_PATH.open() as f:
        for row in csv.DictReader(f):
            timestamps.append(datetime.fromisoformat(row["timestamp"]))
            tapsi_prices.append(float(row["tapsi_price"]) if row["tapsi_price"] else math.nan)
            snapp_prices.append(float(row["snapp_price"]) if row["snapp_price"] else math.nan)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(timestamps, tapsi_prices, marker="o", label="Tapsi")
    ax.plot(timestamps, snapp_prices, marker="o", label="Snapp")
    ax.set_xlabel("Time")
    ax.set_ylabel("Price (Toman)")
    ax.set_title("Tapsi vs Snapp price")
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHART_PATH)
    plt.close(fig)


def run_once():
    timestamp = datetime.now(timezone.utc)
    tapsi_price = snapp_price = None

    try:
        tapsi_price = fetch_tapsi_price()
    except Exception:
        print("Tapsi fetch failed:", file=sys.stderr)
        traceback.print_exc()

    try:
        snapp_price = fetch_snapp_price()
    except Exception:
        print("Snapp fetch failed:", file=sys.stderr)
        traceback.print_exc()

    append_row(timestamp, tapsi_price, snapp_price)
    render_chart()
    print(f"{timestamp.isoformat()} tapsi={tapsi_price} snapp={snapp_price}")


def main():
    if "--once" in sys.argv:
        run_once()
        return

    while True:
        run_once()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
