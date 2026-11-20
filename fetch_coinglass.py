"""CoinGlass ETH OI collector.
Research only. No live orders or V1 modifications.
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://open-api-v4.coinglass.com"
LATEST = Path("coinglass_oi_latest.json")
HISTORY = Path("coinglass_oi_history.jsonl")

key = os.environ.get("COINGLASS_API_KEY", "").strip()

if not key:
    raise SystemExit("Missing COINGLASS_API_KEY secret")

url = BASE + "/api/futures/open-interest/exchange-list?symbol=ETH"

request = urllib.request.Request(
    url,
    headers={
        "CG-API-KEY": key,
        "Accept": "application/json"
    }
)

with urllib.request.urlopen(request, timeout=25) as response:
    payload = json.load(response)

if str(payload.get("code")) != "0":
    raise RuntimeError("CoinGlass API request failed")

rows = payload.get("data")

if not isinstance(rows, list):
    raise RuntimeError("Invalid CoinGlass response")

by_exchange = {
    row.get("exchange"): row
    for row in rows
    if isinstance(row, dict)
}

selected = {}

for exchange in ("All", "Binance", "OKX"):
    row = by_exchange.get(exchange)

    if not row:
        raise RuntimeError(
            "Missing required exchange: " + exchange
        )

    oi = float(row["open_interest_usd"])

    if oi <= 0:
        raise RuntimeError("Invalid open interest")

    selected[exchange] = {
        "open_interest_usd": oi,
        "open_interest_change_percent_1h":
            row.get("open_interest_change_percent_1h")
    }

now_ms = int(time.time() * 1000)
bucket = now_ms // 300000

record = {
    "source": "CoinGlass API V4",
    "asset": "ETH",
    "collected_at_utc":
        datetime.now(timezone.utc).isoformat(),
    "collected_at_ms": now_ms,
    "bucket_5m": bucket,
    "data": selected,
    "research_only": True,
    "exchange_data_timestamp_verified": False
}

existing = []

if HISTORY.exists():
    existing = [
        json.loads(line)
        for line in HISTORY.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

# Preserve only one observation per 5-minute bucket.
if not any(
    x.get("bucket_5m") == bucket
    for x in existing
):
    with HISTORY.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(record, sort_keys=True) + "\n"
        )

    LATEST.write_text(
        json.dumps(record, indent=2) + "\n",
        encoding="utf-8"
    )

    print("CoinGlass ETH OI snapshot collected")
else:
    print("This 5-minute bucket already exists")
