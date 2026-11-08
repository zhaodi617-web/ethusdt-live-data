import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

PREDICTIONS = Path("eth_predictions.jsonl")
RESULTS = Path("eth_verifications.jsonl")
SUMMARY = Path("eth_accuracy.json")
MINUTE = 60000

def read_jsonl(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

def get_target_price(target_ms):
    # Query historical one-minute candles
    params = urllib.parse.urlencode({
        "instId": "ETH-USDT-SWAP",
        "bar": "1m",
        "after": str(target_ms + 2 * MINUTE),
        "limit": "100"
    })
    url = (
        "https://www.okx.com/api/v5/market/"
        "history-candles?" + params
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        result = json.load(response)

    if result.get("code") != "0":
        raise RuntimeError("OKX history API error")

    expected = (target_ms // MINUTE) * MINUTE

    for row in result.get("data", []):
        if (
            int(row[0]) == expected
            and row[8] == "1"
        ):
            return float(row[4]), int(row[0])

    return None, None

predictions = read_jsonl(PREDICTIONS)
verified = read_jsonl(RESULTS)

done = {v["prediction_time"] for v in verified}
now_ms = int(time.time() * 1000)

for p in predictions:
    key = p["prediction_time"]

    if key in done:
        continue

    target = p["target_time"]

    # Wait until the target minute closes
    target_end = (
        (target // MINUTE) + 1
    ) * MINUTE

    if now_ms < target_end + 30000:
        continue

    direction = p["direction"]
    reference = float(p["reference_price"])

    if direction == "NO_SIGNAL":
        outcome = "ABSTAIN"
        actual_price = None
        candle_time = None
        change_pct = None
    else:
        try:
            actual_price, candle_time = (
                get_target_price(target)
            )
        except Exception as error:
            print("Verification retry:", error)
            continue

        if actual_price is None:
            print("Target candle unavailable:", key)
            continue

        change_pct = (
            actual_price / reference - 1
        ) * 100

        if actual_price == reference:
            outcome = "FLAT"
        elif (
            direction == "UP"
            and actual_price > reference
        ) or (
            direction == "DOWN"
            and actual_price < reference
        ):
            outcome = "HIT"
        else:
            outcome = "MISS"

    record = {
        "prediction_time": key,
        "target_time": target,
        "direction": direction,
        "reference_price": reference,
        "actual_price": actual_price,
        "target_candle_time": candle_time,
        "change_pct": (
            round(change_pct, 5)
            if change_pct is not None else None
        ),
        "outcome": outcome,
        "verified_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "method": "target_minute_confirmed_close_v1"
    }

    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    done.add(key)
    print("Verified:", record)

verified = read_jsonl(RESULTS)

hits = sum(v["outcome"] == "HIT" for v in verified)
misses = sum(v["outcome"] == "MISS" for v in verified)
flats = sum(v["outcome"] == "FLAT" for v in verified)
abstains = sum(
    v["outcome"] == "ABSTAIN" for v in verified
)
directional = hits + misses + flats

summary = {
    "model": "baseline_v1",
    "verified_directional_samples": directional,
    "hits": hits,
    "misses": misses,
    "flats": flats,
    "abstains": abstains,
    "pending_or_unverified": (
        len(predictions) - len(verified)
    ),
    "hit_rate_pct": (
        round(100 * hits / directional, 2)
        if directional else None
    ),
    "updated_at": datetime.now(
        timezone.utc
    ).isoformat()
}

SUMMARY.write_text(
    json.dumps(summary, indent=2),
    encoding="utf-8"
)

print("Verification summary:", summary)
