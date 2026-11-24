"""Forward-only Observer Ladder verification, version-aware statistics.

Historical prediction and verification rows are never rewritten.
Each rule version is reported separately.
Legacy top-level accuracy fields remain available.
"""

import json
import time
from collections import defaultdict
from pathlib import Path

PREDICTIONS = Path("observer_early_predictions.jsonl")
CANDLES = Path("okx_candles.json")
RESULTS = Path("observer_verifications.jsonl")
ACCURACY = Path("observer_accuracy.json")

M15 = 900_000
FLAT = 0.0005
RULE = "1.0_restored_2026_11_24"


def read_lines(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def counts(rows):
    hits = sum(r.get("result") == "HIT" for r in rows)
    misses = sum(r.get("result") == "MISS" for r in rows)
    flats = sum(r.get("result") == "FLAT" for r in rows)
    abstains = sum(r.get("result") == "ABSTAIN" for r in rows)

    total = hits + misses

    return {
        "hits": hits,
        "misses": misses,
        "flats": flats,
        "abstains": abstains,
        "directional_samples": total,
        "hit_rate_pct": (
            round(100 * hits / total, 2)
            if total else None
        ),
    }


def version_of(record):
    return str(record.get("rule_version") or "1.0")


def run(now_ms=None):
    now = (
        int(time.time() * 1000)
        if now_ms is None else int(now_ms)
    )

    predictions = read_lines(PREDICTIONS)
    existing = read_lines(RESULTS)

    prediction_by_time = {
        int(p["prediction_time"]): p
        for p in predictions
    }

    finished = {
        int(r["prediction_time"])
        for r in existing
    }

    data = json.loads(
        CANDLES.read_text(encoding="utf-8")
    )

    if (
        data.get("exchange") != "OKX"
        or data.get("symbol") != "ETH-USDT-SWAP"
    ):
        raise RuntimeError("Wrong candle market")

    candles = sorted(
        (
            c for c in data.get("candles_15m", [])
            if c.get("confirmed") is True
            and int(c["time"]) + M15 <= now
        ),
        key=lambda c: int(c["time"]),
    )

    added = []

    for prediction in predictions:
        start = int(prediction["prediction_time"])
        target = int(prediction["target_time"])

        if start in finished or target > now:
            continue

        signal = prediction.get("signal")
        version = version_of(prediction)

        if signal == "UNCERTAIN":
            row = {
                "model": prediction.get(
                    "model", "observer_early_v1"
                ),
                "rule_version": version,
                "prediction_time": start,
                "target_time": target,
                "signal": signal,
                "result": "ABSTAIN",
                "verified_at": now,
            }

        elif signal in ("EARLY_UP", "EARLY_DOWN"):
            possible = [
                c for c in candles
                if target <= int(c["time"]) + M15
                < target + M15
            ]

            if not possible:
                continue

            candle = min(
                possible,
                key=lambda c: int(c["time"])
            )

            close_ms = int(candle["time"]) + M15
            reference = float(
                prediction["reference_price"]
            )
            actual = float(candle["close"])

            if reference <= 0:
                raise RuntimeError(
                    "Invalid prediction reference"
                )

            change = actual / reference - 1

            if abs(change) <= FLAT:
                result = "FLAT"
            elif (
                signal == "EARLY_UP" and change > 0
            ) or (
                signal == "EARLY_DOWN" and change < 0
            ):
                result = "HIT"
            else:
                result = "MISS"

            row = {
                "model": prediction.get(
                    "model", "observer_early_v1"
                ),
                "rule_version": version,
                "prediction_time": start,
                "target_time": target,
                "signal": signal,
                "reference_price": reference,
                "verification_price": actual,
                "verification_time": close_ms,
                "verification_method":
                    "first_confirmed_15m_close_after_target",
                "return_pct": round(change * 100, 5),
                "result": result,
                "verified_at": now,
            }

        else:
            continue

        added.append(row)
        finished.add(start)

    if added:
        with RESULTS.open(
            "a", encoding="utf-8"
        ) as file:
            for row in added:
                file.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                    ) + "\n"
                )

    all_results = existing + added

    predictions_by_version = defaultdict(list)
    results_by_version = defaultdict(list)

    for prediction in predictions:
        predictions_by_version[
            version_of(prediction)
        ].append(prediction)

    for result in all_results:
        timestamp = int(result["prediction_time"])
        original = prediction_by_time.get(timestamp)

        version = (
            version_of(original)
            if original is not None
            else version_of(result)
        )

        results_by_version[version].append(result)

    versions = sorted(
        set(predictions_by_version)
        | set(results_by_version)
        | {RULE}
    )

    history = {}

    for version in versions:
        version_predictions = (
            predictions_by_version[version]
        )
        version_results = results_by_version[version]

        label = (
            f"{version}_active"
            if version == RULE
            else f"{version}_archived"
        )

        statistics = counts(version_results)

        history[label] = {
            **statistics,
            "predictions_total": len(
                version_predictions
            ),
            "verified_total": len(version_results),
            "pending": max(
                0,
                len(version_predictions)
                - len(version_results),
            ),
        }

    overall = counts(all_results)

    accuracy = {
        "model": "observer_early_v1",
        "active_rule_version": RULE,
        "verified_directional_samples":
            overall["directional_samples"],
        "hits": overall["hits"],
        "misses": overall["misses"],
        "flats": overall["flats"],
        "abstains": overall["abstains"],
        "pending_or_unverified": max(
            0,
            len(predictions) - len(all_results),
        ),
        "hit_rate_pct": overall["hit_rate_pct"],
        "history_by_rule_version": history,
        "updated_at_ms": now,
        "warning": (
            "Research only; next confirmed 15m "
            "close after 60m target. "
            "No live fills or net PnL."
        ),
    }

    ACCURACY.write_text(
        json.dumps(
            accuracy,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8"
    )

    print("New Observer verifications:", len(added))

    for label, statistics in history.items():
        print(label, statistics)

    return accuracy


if __name__ == "__main__":
    run()
