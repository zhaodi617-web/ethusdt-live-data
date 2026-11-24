import json
import time
import os
from pathlib import Path

import cg_direction_v2 as core
import cg_direction_v2_rules as rules

ORIGINAL_PREDICTION = core.prediction
ACTIVE_RULE = os.environ.get("CG_V2_RULE", "1.1")

VERSIONS = {
    "1.0": "cg_direction_v2_shadow_1.0",
    "1.1": "cg_direction_v2_shadow_1.1",
    "1.2": "cg_direction_v2_shadow_1.2"
}


def prediction(now):
    base = ORIGINAL_PREDICTION(now)

    if ACTIVE_RULE == "1.1":
        return base

    return rules.evaluate(base, now)


def safety_tests():
    from tempfile import TemporaryDirectory

    now = 1800000000000

    base = {
        "model": VERSIONS["1.1"],
        "rule_version": "1.1",
        "prediction_time_ms": now,
        "target_time_ms": now + core.HOUR,
        "reference_price": 1700,
        "reason": "UNCERTAIN",
        "features": {
            "price_return_15m_pct": 0.02,
            "price_return_1h_pct": 0.03,
            "all_oi_change_1h_pct": 0.1,
            "volume_ratio": 1.1,
            "all_taker_buy_pct": 50
        }
    }

    def flow(value, change):
        return {
            "status": "OK",
            "time": now - rules.M30,
            "imbalance": value,
            "imbalance_change": change
        }

    def snapshot(spot, futures, change):
        return {
            "symbol": "ETH",
            "feature_version": "1.0",
            "source_collected_at_ms": now,
            "generated_at_ms": now,
            "features": {
                "30m": {
                    "spot": flow(spot, change),
                    "futures": flow(futures, change),
                    "liquidation": {
                        "status": "OK",
                        "time": now - rules.M30,
                        "liquidation_intensity": 1,
                        "liquidation_imbalance": 0
                    }
                },
                "1h": {}
            }
        }

    original_source = rules.SOURCE

    with TemporaryDirectory() as folder:
        try:
            rules.SOURCE = Path(folder) / "features.json"

            def check(data, expected, reason=None):
                rules.SOURCE.write_text(json.dumps(data))
                result = rules.evaluate(base, now)
                assert result["signal"] == expected

                if reason is not None:
                    assert result["reason"] == reason

                assert result["model"] == VERSIONS["1.2"]
                assert result["live_order_allowed"] is False

            check(
                snapshot(0.12, 0.10, 0.05),
                "CG_V2_UP"
            )

            check(
                snapshot(-0.12, -0.10, -0.05),
                "CG_V2_DOWN"
            )

            check(
                snapshot(0.12, -0.10, 0.05),
                "UNCERTAIN",
                "COUNTEREVIDENCE_GATE"
            )

            shock = snapshot(0.12, 0.10, 0.05)
            shock["features"]["30m"]["liquidation"][
                "liquidation_intensity"
            ] = 4

            check(
                shock,
                "UNCERTAIN",
                "COUNTEREVIDENCE_GATE"
            )

            stale = snapshot(0.12, 0.10, 0.05)
            stale["source_collected_at_ms"] = (
                now - 3600000
            )

            check(
                stale,
                "UNCERTAIN",
                "ADVANCED_DATA_UNAVAILABLE"
            )

            future = snapshot(0.12, 0.10, 0.05)
            future["generated_at_ms"] = now + 1000

            check(
                future,
                "UNCERTAIN",
                "ADVANCED_DATA_UNAVAILABLE"
            )

            rules.SOURCE.unlink()

            assert rules.evaluate(
                base, now
            )["signal"] == "UNCERTAIN"

        finally:
            rules.SOURCE = original_source

    print("CG V2 rule safety tests passed")


def version_statistics(predictions, verified):
    output = {}

    for version, model in VERSIONS.items():
        selected = [
            p for p in predictions
            if p.get("model") == model
        ]

        ids = {
            int(p["prediction_time_ms"])
            for p in selected
        }

        results = [
            v for v in verified
            if int(v["prediction_time_ms"]) in ids
        ]

        output[version] = core.stats(
            selected, results
        )

    return output


def run():
    if ACTIVE_RULE not in ("1.1", "1.2"):
        raise ValueError("Unsupported CG V2 rule")

    safety_tests()

    now = int(time.time() * 1000)

    if not core.ACTIVATION.exists():
        core.save(core.ACTIVATION, {
            "model": VERSIONS[ACTIVE_RULE],
            "activated_at_ms": now,
            "activated_at_utc": core.utc(now),
            "mode": "prospective_shadow",
            "telegram_enabled": False,
            "trade_execution": False
        })

    activation = core.json_file(core.ACTIVATION.name)

    if now < int(activation["activated_at_ms"]):
        raise ValueError("Clock before activation")

    history = core.records(core.PRED)
    done = core.records(core.VERIFY)

    ids = [
        int(p["prediction_time_ms"])
        for p in history
    ]

    assert len(ids) == len(set(ids))

    verified_ids = [
        int(v["prediction_time_ms"])
        for v in done
    ]

    assert len(verified_ids) == len(set(verified_ids))
    assert set(verified_ids).issubset(set(ids))

    new_prediction = None

    existing_hour = any(
        int(p["prediction_time_ms"]) // core.HOUR
        == now // core.HOUR
        for p in history
    )

    if not existing_hour:
        new_prediction = prediction(now)

        core.append(core.PRED, new_prediction)
        core.save(core.LATEST, new_prediction)

        history.append(new_prediction)

    bars = core.confirmed_m15(
        core.json_file("okx_candles.json"),
        now
    )

    new_results = core.verify(
        history, done, bars, now
    )

    verified = done + new_results

    by_version = version_statistics(
        history, verified
    )

    active_model = VERSIONS[ACTIVE_RULE]

    active_predictions = [
        p for p in history
        if p.get("model") == active_model
    ]

    active_ids = {
        int(p["prediction_time_ms"])
        for p in active_predictions
    }

    active_results = [
        v for v in verified
        if int(v["prediction_time_ms"]) in active_ids
    ]

    summary = {
        "model": active_model,
        "rule_version": ACTIVE_RULE,
        "evaluated_at_utc": core.utc(now),
        **by_version[ACTIVE_RULE],
        "history_by_rule_version": by_version,
        "all_versions_predictions_total": len(history),
        "all_versions_verified_total": len(verified),
        "new_predictions": int(
            new_prediction is not None
        ),
        "new_verifications": len(new_results),
        "comparison_with_cg_v1": core.matched_comparison(
            active_predictions,
            active_results
        ),
        "simulation_only": True,
        "telegram_enabled": False,
        "live_order_allowed": False,
        "thresholds_uncalibrated": True
    }

    core.save(core.SUMMARY, summary)

    print("CG V2 research completed")


if __name__ == "__main__":
    run()
