"""Independent V1 / CG2 time constraint verification.

Forward-only observations, immutable official records.
No Telegram, no trading, no model modifications.
"""

import json
import math
import sys
import time
from pathlib import Path
from collections import Counter
from statistics import median

M15 = 900000
HOUR = 3600000
FLAT = 0.0005

EVENTS = {
    "WITHDRAW_CANDIDATE",
    "REVERSAL_CANDIDATE",
    "NEW_DIRECTION_CANDIDATE"
}

DIRECTIONS = {
    "EARLY_UP": 1,
    "EARLY_DOWN": -1,
    "CG_V2_UP": 1,
    "CG_V2_DOWN": -1
}

CG_VALID = {
    "BUY_FLOW_CONFIRMATION",
    "SELL_FLOW_CONFIRMATION",
    "COUNTEREVIDENCE_GATE",
    "INSUFFICIENT_FLOW_CONFIRMATION"
}

CONFIG = {
    "v1": {
        "observations":
            "v1_lifecycle_observations.jsonl",
        "predictions":
            "observer_early_predictions.jsonl",
        "time_key": "prediction_time",
        "target_key": "target_time"
    },
    "cg2": {
        "observations":
            "cg_direction_v2_observations.jsonl",
        "predictions":
            "cg_direction_v2_predictions.jsonl",
        "time_key": "prediction_time_ms",
        "target_key": "target_time_ms"
    }
}


def read_lines(path):
    path = Path(path)

    if not path.exists():
        return []

    return [
        json.loads(line)
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def positive(value):
    try:
        n = float(value)
        return math.isfinite(n) and n > 0
    except (TypeError, ValueError, OverflowError):
        return False


def outcome(signal, reference, actual):
    if not positive(reference):
        return "UNVERIFIABLE"

    if not positive(actual):
        return "UNVERIFIABLE"

    if signal == "UNCERTAIN":
        return "ABSTAIN"

    if signal not in DIRECTIONS:
        return "UNVERIFIABLE"

    change = float(actual) / float(reference) - 1

    if abs(change) <= FLAT:
        return "FLAT"

    return (
        "HIT"
        if DIRECTIONS[signal] * change > 0
        else "MISS"
    )


def endpoint(bars, target, now):
    valid = [
        (int(b["time"]) + M15, float(b["close"]))
        for b in bars
        if b.get("confirmed") is True
        and target <= int(b["time"]) + M15
        < target + M15
        and int(b["time"]) + M15 <= now
        and positive(b.get("close"))
    ]

    return min(valid) if valid else None


def valid_observation(model, row, anchor, now):
    observed = int(row["observed_at_ms"])
    candidate = row["candidate"]
    config = CONFIG[model]

    calculated = int(
        candidate[config["time_key"]]
    )

    target = int(
        anchor[config["target_key"]]
    )

    if not (
        int(anchor[config["time_key"]])
        <= calculated <= observed < target
        and observed <= now
    ):
        return False

    if row.get("protocol_version") != "1.0":
        return False

    if row.get("formal_prediction_written"):
        return False

    if row.get("rule_version") != anchor.get(
        "rule_version"
    ):
        return False

    if candidate.get("rule_version") != row.get(
        "rule_version"
    ):
        return False

    if not positive(
        candidate.get("reference_price")
    ):
        return False

    if not positive(
        anchor.get("reference_price")
    ):
        return False

    if model == "v1":
        return row.get(
            "timestamps_valid"
        ) is True

    return (
        row.get("model_inputs_usable") is True
        and anchor.get("reason") in CG_VALID
        and candidate.get("reason") in CG_VALID
    )


def classify(state, original, changed):
    if state == "WITHDRAW_CANDIDATE":
        return {
            "MISS": "VALID_WITHDRAWAL",
            "HIT": "FALSE_WITHDRAWAL",
            "FLAT": "NEUTRAL_WITHDRAWAL"
        }.get(original, "UNVERIFIABLE")

    if state == "REVERSAL_CANDIDATE":
        return {
            "HIT": "VALID_REVERSAL",
            "MISS": "FALSE_REVERSAL",
            "FLAT": "NEUTRAL_REVERSAL"
        }.get(changed, "UNVERIFIABLE")

    return {
        "HIT": "VALID_NEW_DIRECTION",
        "MISS": "FALSE_NEW_DIRECTION",
        "FLAT": "NEUTRAL_NEW_DIRECTION"
    }.get(changed, "UNVERIFIABLE")


def run(model):
    config = CONFIG[model]
    now = int(time.time() * 1000)

    observations = read_lines(
        config["observations"]
    )

    predictions = read_lines(
        config["predictions"]
    )

    time_key = config["time_key"]
    target_key = config["target_key"]

    predictions = sorted(
        predictions,
        key=lambda p: int(p[time_key])
    )

    anchors = {
        int(p[time_key]): p
        for p in predictions
        if int(p[time_key]) <= now
    }

    assert len(anchors) == sum(
        int(p[time_key]) <= now
        for p in predictions
    )

    data = json.loads(
        Path("okx_candles.json").read_text(
            encoding="utf-8"
        )
    )

    assert data["exchange"] == "OKX"
    assert data["symbol"] == "ETH-USDT-SWAP"

    bars = data["candles_15m"]

    results_path = Path(
        f"{model}_time_constraint_results.jsonl"
    )

    summary_path = Path(
        f"{model}_time_constraint_summary.json"
    )

    existing = read_lines(results_path)

    done = {
        (
            int(r["anchor_prediction_time_ms"]),
            r["event"]
        )
        for r in existing
    }

    assert len(done) == len(existing)

    selected = {}
    excluded = 0

    for row in sorted(
        observations,
        key=lambda r: int(r["observed_at_ms"])
    ):
        state = row.get("observation_state")

        if state not in EVENTS:
            continue

        anchor_id = row.get(
            "anchor_prediction_time_ms"
        )

        if anchor_id is None:
            continue

        anchor_id = int(anchor_id)
        anchor = anchors.get(anchor_id)

        if anchor is None or not valid_observation(
            model, row, anchor, now
        ):
            excluded += 1
            continue

        key = (anchor_id, state)

        selected.setdefault(
            key, (row, anchor)
        )

    added = []
    pending = 0
    missing_endpoint = 0

    for key, (row, anchor) in selected.items():
        if key in done:
            continue

        anchor_id, state = key

        observed = int(row["observed_at_ms"])
        candidate = row["candidate"]

        target = int(anchor[target_key])

        rolling_target = int(
            candidate[target_key]
        )

        # Both evaluation windows must mature.
        if now < max(
            target, rolling_target
        ) + M15:
            pending += 1
            continue

        fixed = endpoint(bars, target, now)
        rolling = endpoint(
            bars, rolling_target, now
        )

        if fixed is None or rolling is None:
            missing_endpoint += 1
            continue

        close_ms, price = fixed
        rolling_ms, rolling_price = rolling

        old_signal = anchor["signal"]
        new_signal = candidate["signal"]

        old_result = outcome(
            old_signal,
            anchor["reference_price"],
            price
        )

        new_result = outcome(
            new_signal,
            candidate["reference_price"],
            price
        )

        rolling_result = outcome(
            new_signal,
            candidate["reference_price"],
            rolling_price
        )

        label = classify(
            state, old_result, new_result
        )

        later = [
            p for p in predictions
            if anchor_id < int(p[time_key])
            <= min(now, rolling_target)
        ]

        next_formal = (
            later[0] if later else None
        )

        lead = None

        if next_formal is not None:
            lead = (
                int(next_formal[time_key])
                - observed
            ) / 60000

        result = {
            "model": model,
            "protocol_version": "1.0",
            "rule_version": row["rule_version"],
            "event": state,
            "anchor_prediction_time_ms":
                anchor_id,
            "observed_at_ms": observed,
            "anchor_target_time_ms": target,
            "verification_time_ms": close_ms,
            "verification_price": price,
            "original_signal": old_signal,
            "candidate_signal": new_signal,
            "original_reference_price":
                anchor["reference_price"],
            "candidate_reference_price":
                candidate["reference_price"],
            "original_result": old_result,
            "candidate_same_endpoint_result":
                new_result,
            "candidate_60m_result":
                rolling_result,
            "candidate_60m_target_time_ms":
                rolling_target,
            "candidate_60m_verification_time_ms":
                rolling_ms,
            "candidate_60m_verification_price":
                rolling_price,
            "evaluation": label,
            "minutes_before_anchor_target":
                round(
                    (target - observed) / 60000,
                    4
                ),
            "minutes_before_next_formal": (
                round(lead, 4)
                if lead is not None else None
            ),
            "next_formal_signal": (
                next_formal["signal"]
                if next_formal else None
            ),
            "verified_at_ms": now,
            "simulation_only": True
        }

        added.append(result)

    if added:
        with results_path.open(
            "a", encoding="utf-8"
        ) as file:
            for row in added:
                file.write(
                    json.dumps(row) + "\n"
                )

    results = existing + added

    summary = {
        "model": model,
        "protocol_version": "1.0",
        "updated_at_ms": now,
        "observations_total":
            len(observations),
        "selected_events": len(selected),
        "excluded_observations": excluded,
        "verified_events": len(results),
        "pending_events": pending,
        "missing_market_endpoints":
            missing_endpoint,
        "by_evaluation": dict(
            Counter(
                r["evaluation"]
                for r in results
            )
        ),
        "by_rule_version": {},
        "simulation_only": True,
        "warning": (
            "One first event per anchor and "
            "event type. Events may be correlated. "
            "15m close verification has up to "
            "15m delay. No trading PnL."
        )
    }

    versions = {
        r["rule_version"] for r in results
    }

    for version in sorted(versions):
        rows = [
            r for r in results
            if r["rule_version"] == version
        ]

        successful = [
            r for r in rows
            if r["evaluation"].startswith("VALID_")
        ]

        leads = [
            r["minutes_before_anchor_target"]
            for r in successful
        ]

        false_withdrawals = sum(
            r["evaluation"] == "FALSE_WITHDRAWAL"
            for r in rows
        )

        valid_withdrawals = sum(
            r["evaluation"] == "VALID_WITHDRAWAL"
            for r in rows
        )

        total_withdrawals = (
            false_withdrawals
            + valid_withdrawals
        )

        summary["by_rule_version"][version] = {
            "verified_events": len(rows),
            "by_evaluation": dict(
                Counter(
                    r["evaluation"]
                    for r in rows
                )
            ),
            "false_withdrawal_rate_pct": (
                round(
                    100 * false_withdrawals
                    / total_withdrawals,
                    2
                )
                if total_withdrawals else None
            ),
            "median_minutes_before_anchor_target": (
                round(median(leads), 2)
                if leads else None
            )
        }

    temporary = summary_path.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    temporary.replace(summary_path)

    print("Time constraint verification completed")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in CONFIG:
        raise SystemExit("Specify v1 or cg2")

    run(sys.argv[1])
