"""CG2 time constraint experiment.

Preserve official hourly predictions.
Evaluate intrahour candidates independently.
Private research only. No historical backfill.
"""

import time
from pathlib import Path

import cg_direction_v2_runner as runner

core = runner.core

OUTPUT = Path(
    "cg_direction_v2_observations.jsonl"
)

SLOT_MS = 300000

DIRECTIONAL = {
    "CG_V2_UP",
    "CG_V2_DOWN"
}

VALID_REASONS = {
    "BUY_FLOW_CONFIRMATION",
    "SELL_FLOW_CONFIRMATION",
    "COUNTEREVIDENCE_GATE",
    "INSUFFICIENT_FLOW_CONFIRMATION"
}


def capture():
    now = int(time.time() * 1000)

    summary = core.json_file(
        core.SUMMARY.name
    )

    history = core.records(core.PRED)

    observations = core.records(OUTPUT)

    slot = now // SLOT_MS

    if any(
        int(row["slot"]) == slot
        for row in observations
    ):
        print("CG2 observation already recorded")
        return

    formal_written = (
        summary["new_predictions"] == 1
    )

    if formal_written:
        candidate = core.json_file(
            core.LATEST.name
        )
    else:
        candidate = runner.prediction(now)

    calculated_at = int(
        candidate["prediction_time_ms"]
    )

    observed_at = int(time.time() * 1000)

    assert calculated_at <= observed_at

    assert candidate["rule_version"] == (
        runner.ACTIVE_RULE
    )

    assert candidate["simulation_only"] is True
    assert candidate["live_order_allowed"] is False
    assert candidate["telegram_enabled"] is False

    slot = observed_at // SLOT_MS

    if any(
        int(row["slot"]) == slot
        for row in observations
    ):
        return

    eligible = [
        row for row in history
        if int(row["prediction_time_ms"])
        <= calculated_at
    ]

    anchor = max(
        eligible,
        key=lambda row: int(
            row["prediction_time_ms"]
        ),
        default=None
    )

    anchor_active = (
        anchor is not None
        and observed_at < int(
            anchor["target_time_ms"]
        )
    )

    original = (
        anchor.get("signal")
        if anchor_active else None
    )

    current = candidate["signal"]

    reason = candidate.get("reason")

    data_valid = (
        reason in VALID_REASONS
        and candidate.get(
            "reference_price"
        ) is not None
    )

    if not data_valid:
        state = "DATA_UNAVAILABLE"

    elif formal_written:
        state = "FORMAL_PREDICTION"

    elif not anchor_active:
        state = "NO_ACTIVE_PREDICTION"

    elif original == current:
        state = "UNCHANGED"

    elif (
        original in DIRECTIONAL
        and current == "UNCERTAIN"
    ):
        state = "WITHDRAW_CANDIDATE"

    elif (
        original == "UNCERTAIN"
        and current in DIRECTIONAL
    ):
        state = "NEW_DIRECTION_CANDIDATE"

    elif (
        original in DIRECTIONAL
        and current in DIRECTIONAL
        and original != current
    ):
        state = "REVERSAL_CANDIDATE"

    else:
        state = "OTHER_CHANGE"

    row = {
        "experiment":
            "cg2_time_constraint",
        "protocol_version": "1.0",
        "slot": slot,
        "observed_at_ms": observed_at,
        "observed_at_utc":
            core.utc(observed_at),
        "calculated_at_ms": calculated_at,
        "rule_version":
            candidate["rule_version"],
        "formal_prediction_written":
            formal_written,
        "anchor_prediction_time_ms": (
            anchor["prediction_time_ms"]
            if anchor_active else None
        ),
        "anchor_target_time_ms": (
            anchor["target_time_ms"]
            if anchor_active else None
        ),
        "anchor_rule_version": (
            anchor.get("rule_version")
            if anchor_active else None
        ),
        "anchor_signal": original,
        "anchor_score": (
            anchor.get("score")
            if anchor_active else None
        ),
        "anchor_reason": (
            anchor.get("reason")
            if anchor_active else None
        ),
        "remaining_seconds": (
            (
                int(anchor["target_time_ms"])
                - observed_at
            ) / 1000
            if anchor_active else None
        ),
        "candidate_signal": current,
        "candidate_score":
            candidate.get("score"),
        "candidate_reason": reason,
        "observation_state": state,
        "model_inputs_usable": data_valid,
        "source_exchange_timestamps_verified":
            False,
        "candidate": candidate,
        "simulation_only": True,
        "telegram_enabled": False,
        "live_order_allowed": False
    }

    core.append(OUTPUT, row)

    print("CG2 observation recorded")


def main():
    # Preserve the original CG2 runner.
    runner.run()

    # Observation failures must not stop
    # official research record persistence.
    try:
        capture()
    except Exception as exc:
        print(
            "CG2 observation failed:",
            type(exc).__name__
        )


if __name__ == "__main__":
    main()
