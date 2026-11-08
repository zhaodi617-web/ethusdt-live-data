import json
import time
from pathlib import Path

PREDICTIONS = Path("observer_early_predictions.jsonl")
CANDLES = Path("okx_candles.json")
RESULTS = Path("observer_verifications.jsonl")
ACCURACY = Path("observer_accuracy.json")

HOUR = 3600000
M15 = 900000

# Predeclared flat threshold: 0.05%
FLAT_THRESHOLD = 0.0005

def read_lines(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

predictions = read_lines(PREDICTIONS)
verified = read_lines(RESULTS)

completed = {
    int(r["prediction_time"])
    for r in verified
}

data = json.loads(
    CANDLES.read_text(encoding="utf-8")
)

now = int(time.time() * 1000)

candles = sorted(
    [
        c for c in data["candles_15m"]
        if c["confirmed"] is True
    ],
    key=lambda c: int(c["time"])
)

new_results = []

for p in predictions:
    prediction_time = int(p["prediction_time"])
    target = int(p["target_time"])

    if prediction_time in completed:
        continue

    if now < target:
        continue

    signal = p["signal"]

    if signal == "UNCERTAIN":
        new_results.append({
            "model": p["model"],
            "prediction_time": prediction_time,
            "target_time": target,
            "signal": signal,
            "result": "ABSTAIN",
            "verified_at": now
        })
        continue

    if signal not in ("EARLY_UP", "EARLY_DOWN"):
        continue

    # First confirmed 15m candle closing
    # at or shortly after target time.
    candidates = [
        c for c in candles
        if target <= int(c["time"]) + M15
        < target + M15
        and int(c["time"]) + M15 <= now
    ]

    if not candidates:
        continue

    candle = min(
        candidates,
        key=lambda c: int(c["time"]) + M15
    )

    entry = float(p["reference_price"])
    close = float(candle["close"])

    if entry <= 0:
        raise RuntimeError("Invalid reference price")

    change = close / entry - 1

    if abs(change) <= FLAT_THRESHOLD:
        result = "FLAT"
    elif (
        (signal == "EARLY_UP" and change > 0)
        or
        (signal == "EARLY_DOWN" and change < 0)
    ):
        result = "HIT"
    else:
        result = "MISS"

    new_results.append({
        "model": p["model"],
        "prediction_time": prediction_time,
        "target_time": target,
        "signal": signal,
        "reference_price": entry,
        "verification_price": close,
        "verification_time": int(
            candle["time"]
        ) + M15,
        "verification_method":
            "first_confirmed_15m_close_after_target",
        "return_pct": round(change * 100, 5),
        "result": result,
        "verified_at": now
    })

if new_results:
    with RESULTS.open(
        "a", encoding="utf-8"
    ) as f:
        for result in new_results:
            f.write(json.dumps(result) + "\n")

all_results = read_lines(RESULTS)

def count(name):
    return sum(
        r["result"] == name for r in all_results
    )

hits = count("HIT")
misses = count("MISS")
flats = count("FLAT")
abstains = count("ABSTAIN")

directional = hits + misses

accuracy = {
    "model": "observer_early_v1",
    "verified_directional_samples": directional,
    "hits": hits,
    "misses": misses,
    "flats": flats,
    "abstains": abstains,
    "pending_or_unverified":
        len(predictions) - len(all_results),
    "hit_rate_pct": (
        round(hits / directional * 100, 2)
        if directional else None
    ),
    "updated_at_ms": now
}

ACCURACY.write_text(
    json.dumps(accuracy, indent=2),
    encoding="utf-8"
)

print("New verifications:", len(new_results))
print("Observer accuracy:", accuracy)
