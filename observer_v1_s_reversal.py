"""V1-S Reversal Shadow v0.
Forward-only research. Never modifies original V1-S.
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from observer_v1_s import decide, jsonl, append, load

HOUR = 3600000
M15 = 900000
NAME = "v1_s_reversal_shadow_v0"
P = Path("v1_s_reversal_predictions.jsonl")
V = Path("v1_s_reversal_verifications.jsonl")
A = Path("v1_s_reversal_activation.json")
S = Path("v1_s_reversal_summary.json")

def classify(source):
    f = source["features"]
    short = f["short_30m"]
    bg = f["nonoverlap_background_3h"]
    volume = f["volume_ratio"]

    conflict = (
        abs(short) >= 0.0015
        and abs(bg) >= 0.0015
        and short * bg < 0
    )

    qualified = (
        conflict
        and volume is not None
        and volume >= 1.2
    )

    return {
        "model": NAME,
        "version": "v0_frozen",
        "prediction_time_ms": source["prediction_time_ms"],
        "reference_price": source["reference_price"],
        "decision": "WATCH" if qualified else "ABSTAIN",
        "direction": (
            "UP" if short > 0 else "DOWN"
        ) if qualified else "UNCERTAIN",
        "reason": (
            "EARLY_REVERSAL_CANDIDATE"
            if qualified else "FILTERED"
        ),
        "short_30m": short,
        "independent_bg_3h": bg,
        "volume_ratio": volume,
        "simulation_only": True,
        "live_order_allowed": False
    }

def verify(predictions, previous, candles, now):
    done = {
        (x["prediction_time_ms"], x["horizon"])
        for x in previous
    }

    bars = sorted(
        [
            x for x in candles["candles_15m"]
            if x["confirmed"]
            and int(x["time"]) + M15 <= now
        ],
        key=lambda x: int(x["time"])
    )

    output = []

    for p in predictions:
        if p["decision"] != "WATCH":
            continue

        for horizon in (15, 60):
            key = (p["prediction_time_ms"], horizon)

            if key in done:
                continue

            target = p["prediction_time_ms"] + horizon * 60000

            if now < target:
                continue

            matches = [
                b for b in bars
                if target <= int(b["time"]) + M15 < target + M15
            ]

            if not matches:
                continue

            b = min(matches, key=lambda x: int(x["time"]))
            price = float(b["close"])
            change = price / p["reference_price"] - 1

            result = (
                "FLAT" if abs(change) <= 0.0005
                else "HIT"
                if (change > 0) == (p["direction"] == "UP")
                else "MISS"
            )

            output.append({
                "prediction_time_ms": p["prediction_time_ms"],
                "horizon": horizon,
                "direction": p["direction"],
                "reference_price": p["reference_price"],
                "verification_price": price,
                "verification_time_ms": int(b["time"]) + M15,
                "change_pct": round(change * 100, 5),
                "result": result,
                "simulation_only": True
            })

    return output

def run():
    now = int(time.time() * 1000)

    if not A.exists():
        A.write_text(json.dumps({
            "model": NAME,
            "activated_at_ms": now,
            "version": "v0_frozen",
            "policy": "No historical backfill"
        }, indent=2))
        print("Activated. First prediction next UTC hour.")
        return

    activation = load(A)
    predictions = jsonl(P)
    previous = jsonl(V)
    candles = load("okx_candles.json")

    new = []

    if (
        now // HOUR > activation["activated_at_ms"] // HOUR
        and not any(
            p["prediction_time_ms"] // HOUR == now // HOUR
            for p in predictions
        )
    ):
        original = decide(candles, load("okx_latest.json"), now)
        new = [classify(original)]
        append(P, new)

    all_predictions = predictions + new
    new_results = verify(
        all_predictions, previous, candles, now
    )

    append(V, new_results)
    results = previous + new_results

    stats = {}

    for h in (15, 60):
        subset = [x for x in results if x["horizon"] == h]
        hits = sum(x["result"] == "HIT" for x in subset)
        misses = sum(x["result"] == "MISS" for x in subset)

        stats[str(h)] = {
            "hits": hits,
            "misses": misses,
            "hit_rate_pct": round(
                hits * 100 / (hits + misses), 2
            ) if hits + misses else None
        }

    summary = {
        "model": NAME,
        "updated_at_utc": datetime.fromtimestamp(
            now / 1000, timezone.utc
        ).isoformat(),
        "observations": len(all_predictions),
        "reversal_candidates": sum(
            p["decision"] == "WATCH"
            for p in all_predictions
        ),
        "new_predictions": len(new),
        "new_verifications": len(new_results),
        "results": stats,
        "warning": "Research only. No verified fills or net PnL."
    }

    S.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))

if __name__ == "__main__":
    run()
