import json
import math
from pathlib import Path

RULE_VERSION = "1.2"
MODEL_VERSION = "cg_direction_v2_shadow_1.2"

MAX_SNAPSHOT_AGE = 20 * 60_000
M30 = 30 * 60_000
HOUR = 60 * 60_000

SOURCE = Path("coinglass_features_latest.json")


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def load_advanced(now):
    if not SOURCE.exists():
        return None

    data = json.loads(
        SOURCE.read_text(encoding="utf-8")
    )

    collected = int(data["source_collected_at_ms"])
    generated = int(data["generated_at_ms"])

    if not 0 <= now - collected <= MAX_SNAPSHOT_AGE:
        return None

    if not collected <= generated <= now:
        return None

    if data.get("symbol") != "ETH":
        return None

    if data.get("feature_version") != "1.0":
        return None

    return data


def valid_feature(item, duration, cutoff):
    if item.get("status") != "OK":
        return False

    t = number(item.get("time"))

    if t is None:
        return False

    age = cutoff - (t + duration)

    return 0 <= age <= duration + 600000


def direction(value, threshold):
    value = number(value)

    if value is None:
        return 0

    if value >= threshold:
        return 1

    if value <= -threshold:
        return -1

    return 0


def evaluate(base, now):
    advanced = load_advanced(now)

    result = {
        **base,
        "model": MODEL_VERSION,
        "rule_version": RULE_VERSION,
        "signal": "UNCERTAIN",
        "score": 0,
        "score_votes": {},
        "reason": "ADVANCED_DATA_UNAVAILABLE",
        "counterevidence": [],
        "advanced_features": {},
        "simulation_only": True,
        "live_order_allowed": False,
        "telegram_enabled": False,
        "predeclared_thresholds_uncalibrated": True
    }

    if advanced is None:
        return result

    if base.get("reason") == "MISSING_OR_STALE_INPUT":
        result["reason"] = "BASE_DATA_UNAVAILABLE"
        return result

    if base.get("reference_price") is None:
        result["reason"] = "INVALID_REFERENCE_PRICE"
        return result

    cutoff = advanced["source_collected_at_ms"]
    blocks = advanced["features"]

    short = blocks.get("30m", {})
    long = blocks.get("1h", {})

    spot = short.get("spot", {})
    futures = short.get("futures", {})
    liquidation = short.get("liquidation", {})

    if not all(
        valid_feature(item, M30, cutoff)
        for item in (spot, futures)
    ):
        result["reason"] = "INVALID_CVD_INPUT"
        return result

    if spot["time"] != futures["time"]:
        result["reason"] = "CVD_TIME_MISMATCH"
        return result

    spot_flow = number(spot.get("imbalance"))
    futures_flow = number(futures.get("imbalance"))

    if spot_flow is None or futures_flow is None:
        result["reason"] = "INVALID_FLOW_VALUES"
        return result

    if not all(
        -1 <= v <= 1
        for v in (spot_flow, futures_flow)
    ):
        result["reason"] = "FLOW_OUT_OF_RANGE"
        return result

    features = base.get("features", {})

    p15 = number(features.get("price_return_15m_pct"))
    p60 = number(features.get("price_return_1h_pct"))
    oi = number(features.get("all_oi_change_1h_pct"))
    volume = number(features.get("volume_ratio"))

    if any(v is None for v in (p15, p60, oi, volume)):
        result["reason"] = "INVALID_BASE_FEATURES"
        return result

    spot_dir = direction(spot_flow, 0.05)
    futures_dir = direction(futures_flow, 0.05)

    votes = {
        "cvd_alignment": 0,
        "spot_flow_acceleration": 0,
        "longer_flow_confirmation": 0,
        "oi_price_confirmation": 0,
        "taker_confirmation": 0
    }

    counter = []

    if spot_dir != 0 and spot_dir == futures_dir:
        votes["cvd_alignment"] = 3 * spot_dir

    elif spot_dir * futures_dir < 0:
        counter.append("SPOT_FUTURES_DIVERGENCE")

    acceleration = direction(
        spot.get("imbalance_change"), 0.03
    )

    if acceleration == spot_dir:
        votes["spot_flow_acceleration"] = acceleration

    long_spot = long.get("spot", {})
    long_futures = long.get("futures", {})

    if (
        valid_feature(long_spot, HOUR, cutoff)
        and valid_feature(long_futures, HOUR, cutoff)
        and long_spot["time"] == long_futures["time"]
    ):
        ls = direction(long_spot.get("imbalance"), 0.05)
        lf = direction(long_futures.get("imbalance"), 0.05)

        if ls == lf == spot_dir:
            votes["longer_flow_confirmation"] = spot_dir

        elif ls == lf == -spot_dir and spot_dir != 0:
            counter.append("OPPOSING_1H_FLOW")

    if oi >= 0.4:
        price_dir = direction(p15, 0.05)

        if price_dir == spot_dir:
            votes["oi_price_confirmation"] = price_dir

    if oi <= -0.5:
        counter.append("DELEVERAGING")

    taker = number(features.get("all_taker_buy_pct"))

    if taker is not None:
        taker_dir = direction(taker - 50, 1.5)

        if taker_dir == spot_dir:
            votes["taker_confirmation"] = taker_dir

        elif taker_dir * spot_dir < 0:
            counter.append("OPPOSING_TAKER_PRESSURE")

    liquidation_intensity = None
    liquidation_imbalance = None

    if valid_feature(liquidation, M30, cutoff):
        liquidation_intensity = number(
            liquidation.get("liquidation_intensity")
        )

        liquidation_imbalance = number(
            liquidation.get("liquidation_imbalance")
        )

        if (
            liquidation_intensity is not None
            and liquidation_intensity >= 3
        ):
            counter.append("LIQUIDATION_SHOCK")

    if spot_dir * p15 < -0.15:
        counter.append("OPPOSING_SHORT_PRICE")

    if spot_dir * p60 < -0.5:
        counter.append("OPPOSING_HOURLY_PRICE")

    if volume < 0.8:
        counter.append("WEAK_PRICE_VOLUME")

    score = sum(votes.values())

    result["score"] = score
    result["score_votes"] = votes
    result["counterevidence"] = counter

    result["advanced_features"] = {
        "source_collected_at_ms": cutoff,
        "cvd_period_start_ms": spot["time"],
        "spot_imbalance": spot_flow,
        "futures_imbalance": futures_flow,
        "spot_imbalance_change": spot.get(
            "imbalance_change"
        ),
        "liquidation_intensity": liquidation_intensity,
        "liquidation_imbalance": liquidation_imbalance,
        "funding_used_in_score": False,
        "source_timestamp_verified": False
    }

    blocking = {
        "SPOT_FUTURES_DIVERGENCE",
        "OPPOSING_1H_FLOW",
        "OPPOSING_TAKER_PRESSURE",
        "OPPOSING_SHORT_PRICE",
        "OPPOSING_HOURLY_PRICE",
        "LIQUIDATION_SHOCK",
        "DELEVERAGING",
        "WEAK_PRICE_VOLUME"
    }

    if blocking.intersection(counter):
        result["reason"] = "COUNTEREVIDENCE_GATE"

    elif votes["cvd_alignment"] == 3 and score >= 4:
        result["signal"] = "CG_V2_UP"
        result["reason"] = "BUY_FLOW_CONFIRMATION"

    elif votes["cvd_alignment"] == -3 and score <= -4:
        result["signal"] = "CG_V2_DOWN"
        result["reason"] = "SELL_FLOW_CONFIRMATION"

    else:
        result["reason"] = "INSUFFICIENT_FLOW_CONFIRMATION"

    return result
