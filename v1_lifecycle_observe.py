"""V1 lifecycle observation capture.

Preserve original V1 computation and hourly predictions.
Record prospective intrahour observations independently.
No trading, no Telegram, no historical backfill.
"""

import json
import runpy
from pathlib import Path

OUTPUT = Path("v1_lifecycle_observations.jsonl")
FORMAL = Path("observer_early_predictions.jsonl")

SLOT_MS = 300000


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


def capture(scope):
    candidate = scope["record"]
    now = int(candidate["prediction_time"])
    slot = now // SLOT_MS

    observations = read_lines(OUTPUT)

    if any(
        int(row["slot"]) == slot
        for row in observations
    ):
        print("Lifecycle observation already recorded")
        return

    predictions = read_lines(FORMAL)

    eligible = [
        row for row in predictions
        if int(row["prediction_time"]) <= now
    ]

    anchor = max(
        eligible,
        key=lambda row: int(row["prediction_time"]),
        default=None
    )

    anchor_active = (
        anchor is not None
        and now < int(anchor["target_time"])
    )

    formal_written = (
        anchor is not None
        and int(anchor["prediction_time"]) == now
    )

    original = (
        anchor.get("signal")
        if anchor_active else None
    )

    current = candidate["signal"]

    directional = {
        "EARLY_UP",
        "EARLY_DOWN"
    }

    if formal_written:
        state = "FORMAL_PREDICTION"

    elif not anchor_active:
        state = "NO_ACTIVE_PREDICTION"

    elif original == current:
        state = "UNCHANGED"

    elif (
        original in directional
        and current == "UNCERTAIN"
    ):
        state = "WITHDRAW_CANDIDATE"

    elif (
        original == "UNCERTAIN"
        and current in directional
    ):
        state = "NEW_DIRECTION_CANDIDATE"

    elif (
        original in directional
        and current in directional
        and original != current
    ):
        state = "REVERSAL_CANDIDATE"

    else:
        state = "OTHER_CHANGE"

    m15_close = (
        int(scope["m15"][-1]["time"])
        + 900000
    )

    h1_close = (
        int(scope["h1"][-1]["time"])
        + 3600000
    )

    ticker_time = int(scope["price_ts"])

    current_oi = scope.get("current")
    previous_oi = scope.get("previous")

    timestamps_valid = (
        ticker_time <= now
        and m15_close <= now
        and h1_close <= now
    )

    row = {
        "experiment": "v1_lifecycle_time_constraint",
        "protocol_version": "1.0",
        "slot": slot,
        "observed_at_ms": now,
        "rule_version": candidate["rule_version"],
        "formal_prediction_written": formal_written,
        "anchor_prediction_time_ms": (
            anchor["prediction_time"]
            if anchor_active else None
        ),
        "anchor_target_time_ms": (
            anchor["target_time"]
            if anchor_active else None
        ),
        "anchor_signal": original,
        "anchor_score": (
            anchor.get("score")
            if anchor_active else None
        ),
        "remaining_seconds": (
            (int(anchor["target_time"]) - now) / 1000
            if anchor_active else None
        ),
        "candidate_signal": current,
        "candidate_score": candidate["score"],
        "observation_state": state,
        "ticker_time_ms": ticker_time,
        "last_15m_close_ms": m15_close,
        "last_1h_close_ms": h1_close,
        "oi_time_ms": (
            current_oi.get("oi_timestamp")
            if isinstance(current_oi, dict)
            else None
        ),
        "previous_oi_time_ms": (
            previous_oi.get("oi_timestamp")
            if isinstance(previous_oi, dict)
            else None
        ),
        "timestamps_valid": timestamps_valid,
        "candidate": candidate,
        "simulation_only": True,
        "telegram_enabled": False,
        "live_order_allowed": False
    }

    with OUTPUT.open(
        "a", encoding="utf-8"
    ) as file:
        file.write(
            json.dumps(
                row,
                ensure_ascii=False
            ) + "\n"
        )

    print("Lifecycle observation recorded")
    print("Observation state:", state)
    print("Observation timestamps valid:", timestamps_valid)


def main():
    # Execute the original V1 exactly once.
    scope = runpy.run_path(
        "observer_early.py",
        run_name="__main__"
    )

    # Observation errors must not block formal V1.
    try:
        capture(scope)
    except Exception as exc:
        print(
            "::warning::Lifecycle capture failed:",
            type(exc).__name__
        )


if __name__ == "__main__":
    main()
