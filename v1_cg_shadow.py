"""Observer Ladder V1-CG shadow experiment v1.
Prospective, independent, research only.
Never modifies original V1 files.
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(".")
PROTOCOL = ROOT / "v1_cg_protocol.json"
OUTPUT = ROOT / "v1_cg_predictions.jsonl"

V1 = ROOT / "observer_early_predictions.jsonl"
OI = ROOT / "coinglass_oi_history.jsonl"
TAKER = ROOT / "coinglass_taker_history.jsonl"

MAX_AGE_MS = 20 * 60 * 1000
MAX_CAPTURE_DELAY_MS = 15 * 60 * 1000

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

def save_jsonl(path, rows):
    if not rows:
        return
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(
                json.dumps(row, sort_keys=True) + "\n"
            )

def asof(history, prediction_ms):
    valid = [
        x for x in history
        if (
            0 <= prediction_ms
            - int(x["collected_at_ms"])
            <= MAX_AGE_MS
        )
    ]
    if not valid:
        return None
    return max(
        valid,
        key=lambda x: int(x["collected_at_ms"])
    )

now = int(time.time() * 1000)

# The first real execution fixes the start date.
# Never backfill older V1 predictions.
if not PROTOCOL.exists():
    protocol = {
        "model": "observer_v1_cg_shadow_v1",
        "activated_at_ms": now,
        "activated_at_utc": datetime.fromtimestamp(
            now / 1000, timezone.utc
        ).isoformat(),
        "max_source_age_minutes": 20,
        "max_capture_delay_minutes": 15,
        "conflict_buy_below_pct_for_up": 49.0,
        "conflict_buy_above_pct_for_down": 51.0,
        "data_source": "CoinGlass API V4",
        "taker_window": "rolling_4h",
        "hypothesis": (
            "Strong opposite taker imbalance may "
            "identify lower-quality V1 signals"
        ),
        "mode": "prospective_shadow",
        "trade_execution": False
    }
    PROTOCOL.write_text(
        json.dumps(protocol, indent=2) + "\n",
        encoding="utf-8"
    )
    print("Protocol activated; older V1 excluded")
else:
    protocol = json.loads(
        PROTOCOL.read_text(encoding="utf-8")
    )

start = int(protocol["activated_at_ms"])

v1_predictions = read_jsonl(V1)
oi_history = read_jsonl(OI)
taker_history = read_jsonl(TAKER)

existing = read_jsonl(OUTPUT)
done = {
    int(x["prediction_time"])
    for x in existing
}

new_records = []

for p in sorted(
    v1_predictions,
    key=lambda x: int(x["prediction_time"])
):
    ts = int(p["prediction_time"])

    if ts < start or ts in done:
        continue

    # Do not produce a retrospective "prediction".
    if ts > now:
        continue

    if now - ts > MAX_CAPTURE_DELAY_MS:
        print("Skipped late V1 prediction:", ts)
        continue

    original = p["signal"]

    oi = asof(oi_history, ts)
    taker = asof(taker_history, ts)

    status = "READY"
    result = original
    reason = "NO_CONFLICT"

    if oi is None or taker is None:
        status = "MISSING_DATA"
        reason = "NO_FRESH_PRE_PREDICTION_SNAPSHOT"
        result = "NO_CG_DECISION"
    else:
        buy = float(
            taker["data"]["All"]["buy_pct"]
        )

        if original == "EARLY_UP" and buy < 49:
            result = "FILTER"
            reason = "TAKER_OPPOSES_UP"
        elif original == "EARLY_DOWN" and buy > 51:
            result = "FILTER"
            reason = "TAKER_OPPOSES_DOWN"
        elif original == "UNCERTAIN":
            result = "ABSTAIN"
            reason = "V1_UNCERTAIN"

    record = {
        "model": "observer_v1_cg_shadow_v1",
        "prediction_time": ts,
        "target_time": int(p["target_time"]),
        "reference_price": p["reference_price"],
        "original_v1_signal": original,
        "cg_decision": result,
        "decision_reason": reason,
        "status": status,
        "oi_snapshot_time_ms": (
            int(oi["collected_at_ms"])
            if oi else None
        ),
        "taker_snapshot_time_ms": (
            int(taker["collected_at_ms"])
            if taker else None
        ),
        "all_oi_1h_change_pct": (
            oi["data"]["All"].get(
                "open_interest_change_percent_1h"
            )
            if oi else None
        ),
        "all_taker_buy_pct": (
            taker["data"]["All"]["buy_pct"]
            if taker else None
        ),
        "captured_at_ms": now,
        "protocol_activated_at_ms": start,
        "outcome": "PENDING",
        "simulation_only": True
    }

    new_records.append(record)
    done.add(ts)

save_jsonl(OUTPUT, new_records)

print("V1-CG new records:", len(new_records))
print("V1-CG total:", len(existing) + len(new_records))
print("Original V1 files untouched")
