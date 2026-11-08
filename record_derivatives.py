import json
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal

SOURCE = Path("okx_derivatives.json")
PRICE_FILE = Path("okx_latest.json")
HISTORY = Path("okx_derivatives_history.jsonl")

def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def milliseconds(value):
    return int(value)

def freshness_check(timestamp_ms, max_age_seconds=900):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    age = now - timestamp_ms

    if age < -60000 or age > max_age_seconds * 1000:
        raise RuntimeError("Market data is stale or invalid")

data = load_json(SOURCE)

if (
    data.get("exchange") != "OKX"
    or data.get("symbol") != "ETH-USDT-SWAP"
):
    raise RuntimeError("Unexpected exchange or symbol")

oi = data["open_interest"]
funding = data["funding"]

oi_ts = milliseconds(oi["timestamp"])
funding_ts = milliseconds(funding["timestamp"])

freshness_check(oi_ts)
freshness_check(funding_ts)

price = None
price_ts = None

if PRICE_FILE.exists():
    latest = load_json(PRICE_FILE)

    if (
        latest.get("exchange") == "OKX"
        and latest.get("symbol") == "ETH-USDT-SWAP"
    ):
        price_ts = milliseconds(latest["market_time"])
        freshness_check(price_ts)
        price = str(Decimal(latest["price"]))

record = {
    "exchange": "OKX",
    "symbol": "ETH-USDT-SWAP",
    "recorded_at": datetime.now(
        timezone.utc
    ).isoformat(),
    "oi_timestamp": oi_ts,
    "funding_timestamp": funding_ts,
    "open_interest_eth": oi["base_currency"],
    "open_interest_contracts": oi["contracts"],
    "open_interest_usd": oi["usd_value"],
    "funding_rate": funding["rate"],
    "funding_time": funding["funding_time"],
    "price": price,
    "price_timestamp": price_ts
}

existing = set()

if HISTORY.exists():
    with HISTORY.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                old = json.loads(line)
                existing.add(old["oi_timestamp"])

if oi_ts in existing:
    print("OI snapshot already recorded")
else:
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    print("New derivatives snapshot:", record)

print("Total historical snapshots:", len(existing) + (
    0 if oi_ts in existing else 1
))
