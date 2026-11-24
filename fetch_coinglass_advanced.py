import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

BASE = "https://open-api-v4.coinglass.com"
KEY = os.environ.get("COINGLASS_API_KEY", "").strip()

if not KEY:
    raise SystemExit("Missing COINGLASS_API_KEY")

LATEST = Path("coinglass_advanced_latest.json")
HISTORY = Path("coinglass_advanced_history.jsonl")

def now_ms():
    return int(time.time() * 1000)

def fetch(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "CG-API-KEY": KEY,
            "Accept": "application/json"
        }
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                request, timeout=25
            ) as response:
                payload = json.load(response)

            code = str(payload.get("code"))
            data = payload.get("data")

            if code == "0" and data:
                return {
                    "status": "OK",
                    "received_at_ms": now_ms(),
                    "data": data
                }

            return {
                "status": "EMPTY" if code == "0" else "REJECTED",
                "code": code,
                "received_at_ms": now_ms(),
                "data": None
            }

        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue

            return {
                "status": "HTTP_ERROR",
                "code": e.code,
                "data": None
            }

        except Exception as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue

            return {
                "status": "ERROR",
                "error_type": type(e).__name__,
                "data": None
            }

def collect():
    started = now_ms()

    result = {
        "schema_version": "1.0",
        "source": "CoinGlass API V4",
        "symbol": "ETH",
        "exchange_list": ["Binance", "OKX"],
        "started_at_ms": started,
        "simulation_only": True,
        "source_timestamp_verified": False,
        "data": {}
    }

    tasks = []

    for market in ("futures", "spot"):
        for interval in ("30m", "1h"):
            tasks.append((
                market + "_cvd_" + interval,
                "/api/" + market + "/aggregated-cvd/history",
                {
                    "exchange_list": "Binance,OKX",
                    "symbol": "ETH",
                    "interval": interval,
                    "limit": 48,
                    "unit": "usd"
                }
            ))

    for interval in ("30m", "1h"):
        tasks.append((
            "liquidation_" + interval,
            "/api/futures/liquidation/aggregated-history",
            {
                "exchange_list": "Binance,OKX",
                "symbol": "ETH",
                "interval": interval,
                "limit": 48
            }
        ))

    tasks.append((
        "funding_rate",
        "/api/futures/funding-rate/exchange-list",
        {"symbol": "ETH"}
    ))

    for name, path, params in tasks:
        item = fetch(path, params)

        if name == "funding_rate" and item["status"] == "OK":
            raw = item["data"]

            if isinstance(raw, list):
                filtered = [
                    row for row in raw
                    if isinstance(row, dict)
                    and str(row.get("symbol", "")).upper() == "ETH"
                ]
                item["data"] = filtered

                if not filtered:
                    item["status"] = "ETH_NOT_FOUND"

            else:
                item["status"] = "SCHEMA_UNVERIFIED"

        result["data"][name] = item

        print(name, item["status"], flush=True)
        time.sleep(2)

    passed = sum(
        item["status"] == "OK"
        for item in result["data"].values()
    )

    result["finished_at_ms"] = now_ms()
    result["collected_at_utc"] = datetime.now(
        timezone.utc
    ).isoformat()

    result["successful_endpoints"] = passed
    result["total_endpoints"] = len(tasks)

    result["status"] = (
        "OK" if passed == len(tasks)
        else "PARTIAL" if passed > 0
        else "FAILED"
    )

    temp = LATEST.with_suffix(".tmp")

    temp.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temp.replace(LATEST)

    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(result, ensure_ascii=False) + "\n"
        )

    print("Successful:", passed, "/", len(tasks))
    print("Collection status:", result["status"])

    if passed == 0:
        raise SystemExit("All endpoints failed")

if __name__ == "__main__":
    collect()
