"""Independent CG-Direction V1 verification.
Research only. Never edits predictions or V1/V1-CG.
"""

import json
import time
from pathlib import Path

PRED = Path("cg_direction_v1_predictions.jsonl")
OUT = Path("cg_direction_v1_verifications.jsonl")
SUMMARY = Path("cg_direction_v1_summary.json")
CANDLES = Path("okx_candles.json")

M15 = 15 * 60 * 1000
FLAT_THRESHOLD = 0.0005


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


def main():
    now = int(time.time() * 1000)
    predictions = read_jsonl(PRED)
    verified = read_jsonl(OUT)

    data = json.loads(
        CANDLES.read_text(encoding="utf-8")
    )

    if (
        data["exchange"] != "OKX"
        or data["symbol"] != "ETH-USDT-SWAP"
    ):
        raise ValueError("Market mismatch")

    candles = sorted(
        [
            c for c in data["candles_15m"]
            if c.get("confirmed") is True
        ],
        key=lambda c: int(c["time"])
    )

    completed = {
        int(v["prediction_time_ms"])
        for v in verified
    }

    new = []

    for p in predictions:
        ts = int(p["prediction_time_ms"])
        target = int(p["target_time_ms"])

        if ts in completed or target > now:
            continue

        if (
            p["model"] != "cg_direction_v1"
            or target - ts != 3600000
        ):
            raise ValueError("Invalid prediction")

        eligible = [
            c for c in candles
            if target <= int(c["time"]) + M15
            < target + M15
            and int(c["time"]) + M15 <= now
        ]

        if not eligible:
            continue

        bar = min(
            eligible,
            key=lambda c: int(c["time"]) + M15
        )

        endpoint = int(bar["time"]) + M15
        reference = p.get("reference_price")
        signal = p["signal"]

        if reference is None:
            result = "UNVERIFIABLE"
            change_pct = None
            direction = "UNKNOWN"
        else:
            reference = float(reference)
            if reference <= 0:
                raise ValueError("Invalid price")

            close = float(bar["close"])
            change = close / reference - 1
            change_pct = round(100 * change, 5)

            if abs(change) <= FLAT_THRESHOLD:
                direction = "FLAT"
            elif change > 0:
                direction = "UP"
            else:
                direction = "DOWN"

            if signal == "UNCERTAIN":
                result = "ABSTAIN"
            elif direction == "FLAT":
                result = "FLAT"
            elif (
                (signal == "CG_UP" and direction == "UP")
                or
                (signal == "CG_DOWN" and direction == "DOWN")
            ):
                result = "HIT"
            else:
                result = "MISS"

        record = {
            "model": "cg_direction_v1",
            "prediction_time_ms": ts,
            "target_time_ms": target,
            "verification_time_ms": endpoint,
            "verified_at_ms": now,
            "verification_lag_minutes": round(
                (endpoint - target) / 60000, 4
            ),
            "signal": signal,
            "reference_price": reference,
            "verification_price": float(bar["close"]),
            "return_pct": change_pct,
            "market_direction": direction,
            "result": result,
            "method": "confirmed_15m_close_after_target",
            "simulation_only": True
        }

        new.append(record)
        completed.add(ts)

    if new:
        with OUT.open("a", encoding="utf-8") as f:
            for record in new:
                f.write(
                    json.dumps(record, sort_keys=True)
                    + "\n"
                )

    all_results = verified + new
    hits = sum(
        v["result"] == "HIT"
        for v in all_results
    )
    misses = sum(
        v["result"] == "MISS"
        for v in all_results
    )
    directional = hits + misses

    summary = {
        "model": "cg_direction_v1",
        "predictions_total": len(predictions),
        "verified_total": len(all_results),
        "pending": len(predictions) - len(all_results),
        "hits": hits,
        "misses": misses,
        "flats": sum(
            v["result"] == "FLAT"
            for v in all_results
        ),
        "abstains": sum(
            v["result"] == "ABSTAIN"
            for v in all_results
        ),
        "unverifiable": sum(
            v["result"] == "UNVERIFIABLE"
            for v in all_results
        ),
        "direction_hit_rate_pct": (
            round(100 * hits / directional, 2)
            if directional else None
        ),
        "warning": (
            "Research-only directional verification. "
            "No fills or net PnL."
        )
    }

    SUMMARY.write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8"
    )

    print("New verifications:", len(new))
    print("Directional samples:", directional)
    print("Existing models untouched")


if __name__ == "__main__":
    main()
