"""CoinGlass ETH 4h taker ratio collector.

Resilient network requests with bounded retries.
Independent research only.
Original V1 and other models are untouched.
Existing JSON schema and history are preserved.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://open-api-v4.coinglass.com"
LATEST = Path("coinglass_taker_latest.json")
HISTORY = Path("coinglass_taker_history.jsonl")

MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 25
RETRY_DELAYS = (3, 6)
RETRYABLE_HTTP = {429, 500, 502, 503, 504}

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


def fetch_with_retries():
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            url,
            headers={
                "CG-API-KEY": key,
                "Accept": "application/json"
            }
        )

        try:
            print(
                "CoinGlass request attempt:",
                attempt,
                "of",
                MAX_ATTEMPTS,
                flush=True
            )

            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT
            ) as response:
                payload = json.load(response)

            if not isinstance(payload, dict):
                raise ValueError(
                    "Unexpected CoinGlass response"
                )

            if str(payload.get("code")) != "0":
                raise RuntimeError(
                    "CoinGlass API rejected request. Code: "
                    + str(payload.get("code"))
                )

            print(
                "CoinGlass request successful",
                flush=True
            )
            return payload

        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP:
                raise RuntimeError(
                    "Non-retryable CoinGlass HTTP status: "
                    + str(exc.code)
                ) from None

            error_message = (
                "Temporary CoinGlass HTTP status: "
                + str(exc.code)
            )

        except (
            TimeoutError,
            ConnectionError,
            urllib.error.URLError
        ) as exc:
            error_message = (
                "Temporary CoinGlass connection failure: "
                + type(exc).__name__
            )

        print(error_message, flush=True)

        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(
                "CoinGlass collection failed after "
                + str(MAX_ATTEMPTS)
                + " attempts. "
                + error_message
            ) from None

        delay = RETRY_DELAYS[attempt - 1]
        print(
            "Retrying in",
            delay,
            "seconds",
            flush=True
        )
        time.sleep(delay)

    raise RuntimeError(
        "CoinGlass retry loop ended unexpectedly"
    )


def checked_ratio(row, name):
    if not isinstance(row, dict):
        raise ValueError("Invalid data row: " + name)

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


def main():
    payload = fetch_with_retries()

    data = payload.get("data")

    if not isinstance(data, dict):
        raise RuntimeError("Unexpected API response")

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
            datetime.fromtimestamp(
                now_ms / 1000,
                timezone.utc
            ).isoformat(),
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
        print(
            "Already collected this bucket",
            flush=True
        )
        return

    # Write only after a successful API response
    # and validation of all three market ratios.
    with HISTORY.open(
        "a",
        encoding="utf-8"
    ) as file:
        file.write(
            json.dumps(
                record,
                sort_keys=True
            ) + "\n"
        )

    LATEST.write_text(
        json.dumps(
            record,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    print(
        "PASS: CoinGlass ETH taker saved",
        flush=True
    )
    print(
        "Snapshot time:",
        record["collected_at_utc"]
    )
    print(
        "All buy %:",
        selected["All"]["buy_pct"]
    )
    print(
        "Binance buy %:",
        selected["Binance"]["buy_pct"]
    )
    print(
        "OKX buy %:",
        selected["OKX"]["buy_pct"]
    )


if __name__ == "__main__":
    main()
