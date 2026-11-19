"""H1: forward-only 8h position audit of Early V1 DOWN signals.
Independent research. No original V1 changes or live orders.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

MODEL = "v1_position_h1_shadow_v0"
HOUR = 3_600_000
BAR = 900_000
A = Path("v1_position_h1_activation.json")
P = Path("v1_position_h1_predictions.jsonl")
V = Path("v1_position_h1_verifications.jsonl")
S = Path("v1_position_h1_summary.json")


def lines(path):
    if not path.exists():
        return []
    return [
        json.loads(z)
        for z in path.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


def append(path, entries):
    if entries:
        with path.open("a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, sort_keys=True) + "\n")


def save(path, entry):
    path.write_text(
        json.dumps(entry, indent=2) + "\n",
        encoding="utf-8"
    )


def position(p, bars):
    t = int(p["prediction_time"])
    last_end = (t // BAR) * BAR

    by_end = {
        int(b["time"]) + BAR: b
        for b in bars
        if b.get("confirmed") is True
    }

    past = [
        by_end.get(last_end - BAR * i)
        for i in range(32)
    ]

    if any(b is None for b in past):
        return None

    if any(int(b["time"]) + BAR > t for b in past):
        raise ValueError("Future candle detected")

    lo = min(float(b["low"]) for b in past)
    hi = max(float(b["high"]) for b in past)

    if hi <= lo:
        return None

    frac = (float(p["reference_price"]) - lo) / (hi - lo)

    if frac <= 0.20:
        zone = "BOTTOM_20"
    elif frac > 0.50:
        zone = "UPPER_50"
    else:
        zone = "MIDDLE_20_50"

    return {
        "position_fraction": round(frac, 8),
        "zone": zone,
        "lookback_low": lo,
        "lookback_high": hi,
        "last_known_candle_end_ms": last_end
    }


def stats(rows):
    h = sum(r["result"] == "HIT" for r in rows)
    m = sum(r["result"] == "MISS" for r in rows)

    return {
        "hits": h,
        "misses": m,
        "flats": sum(r["result"] == "FLAT" for r in rows),
        "n_directional": h + m,
        "hit_rate_pct": (
            round(100 * h / (h + m), 2)
            if h + m else None
        )
    }


def run():
    now = int(time.time() * 1000)

    if not A.exists():
        save(A, {
            "model": MODEL,
            "activated_at_ms": now,
            "first_eligible_prediction_ms":
                (now // HOUR + 1) * HOUR,
            "version": "v0_frozen",
            "lookback_bars": 32,
            "zones": [
                "BOTTOM_20 <=0.20",
                "MIDDLE_20_50",
                "UPPER_50 >0.50"
            ],
            "policy": (
                "No historical backfill. "
                "Freeze position before 60m outcome."
            )
        })
        print("H1 activated; no historical backfill")
        return

    activation = json.loads(
        A.read_text(encoding="utf-8")
    )
    start = int(
        activation["first_eligible_prediction_ms"]
    )

    raw = lines(
        Path("observer_early_predictions.jsonl")
    )
    archive = lines(
        Path("v3_archive_15m.jsonl")
    )

    prior = lines(P)
    known = {
        int(x["prediction_time_ms"])
        for x in prior
    }

    new = []

    for p in raw:
        t = int(p["prediction_time"])

        if t < start:
            continue

        if p.get("signal") != "EARLY_DOWN":
            continue

        if p.get("score") not in (-2, -3):
            continue

        # Classify prospectively, never after outcome.
        if t in known or not (
            0 <= now - t < 15 * 60_000
        ):
            continue

        x = position(p, archive)

        if x is None:
            continue

        entry = {
            "model": MODEL,
            "version": "v0_frozen",
            "prediction_time_ms": t,
            "target_time_ms": int(p["target_time"]),
            "signal": p["signal"],
            "score": p["score"],
            "reference_price": p["reference_price"],
            "decision": (
                "FILTER_HYPOTHESIS"
                if x["zone"] == "BOTTOM_20"
                else "KEEP_HYPOTHESIS"
            ),
            "classified_at_ms": now,
            "simulation_only": True,
            **x
        }

        new.append(entry)
        known.add(t)

    append(P, new)
    all_predictions = prior + new

    prior_verified = lines(V)
    done = {
        int(x["prediction_time_ms"])
        for x in prior_verified
    }

    original = {
        int(x["prediction_time"]): x
        for x in lines(
            Path("observer_verifications.jsonl")
        )
    }

    fresh = []

    for p in all_predictions:
        t = int(p["prediction_time_ms"])

        if t in done or now < p["target_time_ms"]:
            continue

        o = original.get(t)

        if not o or o.get("result") not in (
            "HIT", "MISS", "FLAT"
        ):
            continue

        fresh.append({
            "model": MODEL,
            "prediction_time_ms": t,
            "zone": p["zone"],
            "decision": p["decision"],
            "result": o["result"],
            "original_verification_time_ms":
                o.get("verification_time"),
            "original_return_pct":
                o.get("return_pct"),
            "simulation_only": True
        })

        done.add(t)

    append(V, fresh)
    verified = prior_verified + fresh

    by_zone = {
        z: stats([
            v for v in verified
            if v["zone"] == z
        ])
        for z in (
            "BOTTOM_20",
            "MIDDLE_20_50",
            "UPPER_50"
        )
    }

    removed = [
        v for v in verified
        if v["decision"] == "FILTER_HYPOTHESIS"
    ]
    kept = [
        v for v in verified
        if v["decision"] == "KEEP_HYPOTHESIS"
    ]

    save(S, {
        "model": MODEL,
        "generated_at_utc":
            datetime.now(timezone.utc).isoformat(),
        "prospective_down_signals":
            len(all_predictions),
        "new_signals": len(new),
        "verified_signals": len(verified),
        "new_verifications": len(fresh),
        "by_zone": by_zone,
        "would_filter": stats(removed),
        "would_keep": stats(kept),
        "errors_avoided_if_filtered": sum(
            x["result"] == "MISS"
            for x in removed
        ),
        "correct_signals_missed_if_filtered": sum(
            x["result"] == "HIT"
            for x in removed
        ),
        "warning": (
            "Fixed research hypothesis, not a trade "
            "signal. Original V1 60m verification "
            "is lagged. No fills or net PnL."
        )
    })

    print(
        "New classified:", len(new),
        "new verified:", len(fresh)
    )


if __name__ == "__main__":
    run()
