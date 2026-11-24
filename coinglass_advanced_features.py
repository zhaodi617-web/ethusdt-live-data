import json
import time
import math
import statistics
from pathlib import Path

SOURCE = Path("coinglass_advanced_latest.json")
LATEST = Path("coinglass_features_latest.json")
HISTORY = Path("coinglass_features_history.jsonl")

VERSION = "1.0"
NOW = int(time.time() * 1000)

def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None

def closed_rows(item, interval_ms, cutoff):
    if item.get("status") != "OK":
        return []

    rows = item.get("data")

    if not isinstance(rows, list):
        return []

    unique = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        t = number(row.get("time"))

        if t is None:
            continue

        t = int(t)

        if t < 10**12:
            t *= 1000

        # Conservative cutoff:
        # exclude potentially unfinished periods.
        if t + interval_ms <= cutoff:
            unique[t] = dict(row, time=t)

    return [unique[t] for t in sorted(unique)]

def cvd_features(rows):
    valid = []

    for row in rows:
        buy = number(row.get("agg_taker_buy_vol"))
        sell = number(row.get("agg_taker_sell_vol"))

        if buy is None or sell is None:
            continue

        if buy < 0 or sell < 0 or buy + sell <= 0:
            continue

        valid.append({
            "time": row["time"],
            "buy": buy,
            "sell": sell,
            "delta": buy - sell,
            "imbalance": (buy - sell) / (buy + sell)
        })

    if len(valid) < 5:
        return {"status": "INSUFFICIENT_DATA"}

    last = valid[-1]
    previous = valid[-2]

    recent = valid[-4:]
    prior = valid[-5:-1]

    buy = sum(r["buy"] for r in recent)
    sell = sum(r["sell"] for r in recent)

    return {
        "status": "OK",
        "time": last["time"],
        "sample_count": len(valid),
        "buy_volume": last["buy"],
        "sell_volume": last["sell"],
        "net_delta": last["delta"],
        "buy_ratio": last["buy"] / (
            last["buy"] + last["sell"]
        ),
        "imbalance": last["imbalance"],
        "imbalance_change": (
            last["imbalance"] - previous["imbalance"]
        ),
        "four_period_delta": buy - sell,
        "four_period_imbalance": (
            (buy - sell) / (buy + sell)
        ),
        "volume_ratio": (
            (last["buy"] + last["sell"]) /
            statistics.mean(
                r["buy"] + r["sell"] for r in prior
            )
        )
    }

def liquidation_features(rows):
    valid = []

    for row in rows:
        long_usd = number(
            row.get("aggregated_long_liquidation_usd")
        )
        short_usd = number(
            row.get("aggregated_short_liquidation_usd")
        )

        if long_usd is None or short_usd is None:
            continue

        if long_usd < 0 or short_usd < 0:
            continue

        valid.append({
            "time": row["time"],
            "long": long_usd,
            "short": short_usd,
            "total": long_usd + short_usd
        })

    if len(valid) < 9:
        return {"status": "INSUFFICIENT_DATA"}

    last = valid[-1]
    previous = valid[-2]

    baseline = statistics.median(
        r["total"] for r in valid[-9:-1]
    )

    return {
        "status": "OK",
        "time": last["time"],
        "long_liquidation": last["long"],
        "short_liquidation": last["short"],
        "total_liquidation": last["total"],
        "liquidation_imbalance": (
            (last["short"] - last["long"]) /
            last["total"]
            if last["total"] > 0 else None
        ),
        "liquidation_intensity": (
            last["total"] / baseline
            if baseline > 0 else None
        ),
        "liquidation_change": (
            last["total"] - previous["total"]
        )
    }

def funding_features(item):
    if item.get("status") != "OK":
        return {"status": "UNAVAILABLE"}

    rows = item.get("data") or []

    rates = {}

    for row in rows:
        if row.get("symbol") != "ETH":
            continue

        for entry in row.get("stablecoin_margin_list", []):
            rate = number(entry.get("funding_rate"))
            interval = number(
                entry.get("funding_rate_interval")
            )
            exchange = entry.get("exchange")

            if not exchange or rate is None:
                continue

            if interval is None or interval <= 0:
                continue

            rates[exchange] = {
                "raw_rate": rate,
                "interval_hours": interval,
                "normalized_8h_rate": rate * 8 / interval
            }

    if not rates:
        return {"status": "INSUFFICIENT_DATA"}

    values = [
        r["normalized_8h_rate"]
        for r in rates.values()
    ]

    return {
        "status": "OK",
        "exchange_count": len(values),
        "median_8h_rate": statistics.median(values),
        "positive_ratio": sum(
            v > 0 for v in values
        ) / len(values),
        "negative_ratio": sum(
            v < 0 for v in values
        ) / len(values),
        "rate_dispersion": max(values) - min(values),
        "rates": rates,
        "unit_verified": False
    }

def compare_flows(futures, spot):
    if futures.get("status") != "OK":
        return "UNAVAILABLE"

    if spot.get("status") != "OK":
        return "UNAVAILABLE"

    if futures["time"] != spot["time"]:
        return "TIME_MISMATCH"

    f = futures["imbalance"]
    s = spot["imbalance"]

    # Experimental thresholds, not trading signals.
    threshold = 0.05

    if f > threshold and s > threshold:
        return "BUY_FLOW_ALIGNMENT"

    if f < -threshold and s < -threshold:
        return "SELL_FLOW_ALIGNMENT"

    if f > threshold and s < -threshold:
        return "FUTURES_BUY_SPOT_SELL"

    if f < -threshold and s > threshold:
        return "FUTURES_SELL_SPOT_BUY"

    return "MIXED_OR_WEAK"

def main():
    source = json.loads(
        SOURCE.read_text(encoding="utf-8")
    )

    cutoff = int(source["finished_at_ms"])
    age = NOW - cutoff

    if age < 0 or age > 20 * 60 * 1000:
        raise SystemExit("Source data stale or invalid")

    raw = source["data"]

    result = {
        "feature_version": VERSION,
        "generated_at_ms": NOW,
        "source_collected_at_ms": cutoff,
        "symbol": "ETH",
        "simulation_only": True,
        "timestamp_policy": "conservative",
        "source_timestamp_verified": False,
        "prediction_enabled": False,
        "features": {}
    }

    for interval, duration in (
        ("30m", 1800000),
        ("1h", 3600000)
    ):
        block = {}

        for market in ("futures", "spot"):
            key = market + "_cvd_" + interval
            item = raw.get(key, {})

            rows = closed_rows(
                item, duration,
                min(cutoff, item.get("received_at_ms", cutoff))
            )

            block[market] = cvd_features(rows)

        item = raw.get("liquidation_" + interval, {})

        block["liquidation"] = liquidation_features(
            closed_rows(
                item, duration,
                min(cutoff, item.get("received_at_ms", cutoff))
            )
        )

        block["flow_relationship"] = compare_flows(
            block["futures"],
            block["spot"]
        )

        result["features"][interval] = block

    result["features"]["funding"] = funding_features(
        raw.get("funding_rate", {})
    )

    if LATEST.exists():
        previous = json.loads(
            LATEST.read_text(encoding="utf-8")
        )

        if (
            previous.get("source_collected_at_ms") == cutoff
            and previous.get("feature_version") == VERSION
        ):
            print("Snapshot already processed")
            return

    temp = LATEST.with_suffix(".tmp")

    temp.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temp.replace(LATEST)

    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(result, ensure_ascii=False) + "\n"
        )

    for interval in ("30m", "1h"):
        print(
            interval,
            result["features"][interval]["flow_relationship"]
        )

    print("CG advanced features saved")
    print("No prediction rules modified")

if __name__ == "__main__":
    main()
