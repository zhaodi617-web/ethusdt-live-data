"""CoinGlass ETH 4h taker ratio collector.
Independent research only. Original V1 is untouched.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://open-api-v4.coinglass.com"
LATEST = Path("coinglass_taker_latest.json")
HISTORY = Path("coinglass_taker_history.jsonl")

key = os.environ.get(
    "COINGLASS_API_KEY", ""
).strip()

if not key:
    raise SystemExit("Missing CoinGlass API secret")

query = urllib.parse.urlencode({
    "symbol": "ETH",
    "range": "4h"
})

url = (
    BASE
    + "/api/futures/taker-buy-sell-volume/exchange-list?"
    + query
)

request = urllib.request.Request(
    url,
    headers={
        "CG-API-KEY": key,
        "Accept": "application/json"
    }
)

with urllib.request.urlopen(
    request, timeout=25
) as response:
    payload = json.load(response)

if str(payload.get("code")) != "0":
    raise RuntimeError(
        "CoinGlass API rejected 4h request. Code: "
        + str(payload.get("code"))
    )

data = payload.get("data")

if not isinstance(data, dict):
    raise RuntimeError("Unexpected API response")

def checked_ratio(row, name):
    buy = float(row["buy_ratio"])
    sell = float(row["sell_ratio"])

    if not (
        0 <= buy <= 100
        and 0 <= sell <= 100
        and abs(buy + sell - 100) < 1
    ):
        raise ValueError("Invalid ratio: " + name)

    return {
        "buy_pct": round(buy, 6),
        "sell_pct": round(sell, 6)
    }

selected = {
    "All": checked_ratio(data, "All")
}

exchange_list = data.get("exchange_list", [])

if not isinstance(exchange_list, list):
    raise RuntimeError("Invalid exchange list")

for exchange in ("Binance", "OKX"):
    row = next(
        (
            x for x in exchange_list
            if isinstance(x, dict)
            and x.get("exchange") == exchange
        ),
        None
    )

    if row is None:
        raise RuntimeError(
            "Missing exchange: " + exchange
        )

    selected[exchange] = checked_ratio(
        row, exchange
    )

now_ms = int(time.time() * 1000)
bucket = now_ms // 300000

record = {
    "source": "CoinGlass API V4",
    "asset": "ETH",
    "metric": "taker_buy_sell_ratio",
    "api_range": "4h",
    "window_type": "rolling_4h",
    "collected_at_ms": now_ms,
    "collected_at_utc":
        datetime.now(timezone.utc).isoformat(),
    "bucket_5m": bucket,
    "data": selected,
    "source_timestamp_verified": False,
    "research_only": True,
    "simulation_only": True
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

if any(
    x.get("bucket_5m") == bucket
    for x in existing
):
    print("Already collected this bucket")
else:
    with HISTORY.open(
        "a", encoding="utf-8"
    ) as file:
        file.write(
            json.dumps(record, sort_keys=True) + "\n"
        )

    LATEST.write_text(
        json.dumps(record, indent=2) + "\n",
        encoding="utf-8"
    )

    print("PASS: CoinGlass ETH taker saved")
    print("Snapshot time:", record["collected_at_utc"])
    print("All buy %:", selected["All"]["buy_pct"])
    print("Binance buy %:",
          selected["Binance"]["buy_pct"])
    print("OKX buy %:", selected["OKX"]["buy_pct"])
