"""Observer Ladder V1 lifecycle: independent prospective study.

Original V1 predictions and results are never modified.
15-minute candles cannot verify an exact 5-minute outcome.
No real orders, fills, or net profit are claimed.
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

MODEL = "v1_lifecycle_shadow_v0"
PRED = Path("observer_early_predictions.jsonl")
ARCHIVE = Path("v3_archive_15m.jsonl")
ACTIVATION = Path("v1_lifecycle_activation.json")
RESULTS = Path("v1_lifecycle_results.jsonl")
SUMMARY = Path("v1_lifecycle_summary.json")

HORIZONS = (15, 30, 45, 60)
BAR_MS = 900_000
FLAT = 0.0005


def read_lines(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def save_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run():
    now = int(time.time() * 1000)

    if not ACTIVATION.exists():
        # Start at the following UTC hour. No backfill.
        first_hour = (now // 3_600_000 + 1) * 3_600_000
        save_json(ACTIVATION, {
            "model": MODEL,
            "activated_at_ms": now,
            "first_eligible_prediction_ms": first_hour,
            "version": "v0_frozen",
            "policy": "Forward-only, no historical backfill",
            "horizons_minutes": list(HORIZONS),
            "flat_threshold_fraction": FLAT,
            "verification": "first_confirmed_15m_close_at_or_after_target",
        })
        print("Lifecycle experiment activated")
        return

    activation = json.loads(ACTIVATION.read_text(encoding="utf-8"))
    start = int(activation["first_eligible_prediction_ms"])

    predictions = read_lines(PRED)
    bars = {
        int(b["time"]): b
        for b in read_lines(ARCHIVE)
        if b.get("confirmed") is True
    }
    existing = read_lines(RESULTS)

    done = {
        (int(r["prediction_time_ms"]), int(r["horizon_minutes"]))
        for r in existing
    }
    new = []

    for p in predictions:
        t0 = int(p["prediction_time"])
        signal = p.get("signal")

        if t0 < start:
            continue
        if signal not in ("EARLY_UP", "EARLY_DOWN"):
            continue

        reference = float(p["reference_price"])
        if reference <= 0:
            raise ValueError("Invalid prediction reference price")

        direction = 1 if signal == "EARLY_UP" else -1

        for minutes in HORIZONS:
            key = (t0, minutes)
            target = t0 + minutes * 60_000

            if key in done or now < target:
                continue

            # A confirmed 15m candle closes at start + 15m.
            # Select the first close at/after target, no more than 15m late.
            candidates = [
                (start_ms + BAR_MS, b)
                for start_ms, b in bars.items()
                if target <= start_ms + BAR_MS < target + BAR_MS
                and start_ms + BAR_MS <= now
            ]

            if not candidates:
                continue

            close_ms, candle = min(candidates, key=lambda x: x[0])
            actual = float(candle["close"])
            change = actual / reference - 1

            if abs(change) <= FLAT:
                result = "FLAT"
            elif direction * change > 0:
                result = "HIT"
            else:
                result = "MISS"

            new.append({
                "model": MODEL,
                "version": "v0_frozen",
                "prediction_time_ms": t0,
                "signal": signal,
                "score": p.get("score"),
                "reference_price": reference,
                "horizon_minutes": minutes,
                "target_time_ms": target,
                "verification_time_ms": close_ms,
                "verification_delay_seconds": (close_ms - target) / 1000,
                "verification_price": actual,
                "return_pct": round(change * 100, 6),
                "result": result,
                "method": "first_confirmed_15m_close_at_or_after_target",
                "simulation_only": True,
                "net_pnl": None,
            })
            done.add(key)

    if new:
        with RESULTS.open("a", encoding="utf-8") as f:
            for row in sorted(
                new,
                key=lambda x: (
                    x["prediction_time_ms"],
                    x["horizon_minutes"],
                ),
            ):
                f.write(json.dumps(row, sort_keys=True) + "\n")

    all_results = read_lines(RESULTS)

    by_horizon = {}
    for minutes in HORIZONS:
        subset = [
            x for x in all_results
            if x["horizon_minutes"] == minutes
        ]
        hits = sum(x["result"] == "HIT" for x in subset)
        misses = sum(x["result"] == "MISS" for x in subset)
        flats = sum(x["result"] == "FLAT" for x in subset)
        n = hits + misses

        by_horizon[str(minutes)] = {
            "hits": hits,
            "misses": misses,
            "flats": flats,
            "directional_samples": n,
            "hit_rate_pct": round(100 * hits / n, 2) if n else None,
        }

    save_json(SUMMARY, {
        "model": MODEL,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "first_eligible_prediction_ms": start,
        "new_results": len(new),
        "total_results": len(all_results),
        "by_horizon": by_horizon,
        "five_minute_status": "NOT_SUPPORTED_BY_15M_CANDLES",
        "warning": (
            "15m endpoint verification has a 0-15m delay. "
            "No exact intrabar path, fills, fees or net PnL."
        ),
    })

    print("Lifecycle evaluations added:", len(new))


if __name__ == "__main__":
    run()
