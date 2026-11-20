"""Observer Ladder CG-Direction V1
Independent, prospective, research-only model.
Does not modify existing models or send Telegram alerts.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(".")
ACTIVATION = ROOT / "cg_direction_v1_activation.json"
HISTORY = ROOT / "cg_direction_v1_predictions.jsonl"
LATEST = ROOT / "cg_direction_v1_latest.json"

MAX_CG_AGE_MS = 20 * 60 * 1000
MAX_PRICE_AGE_MS = 5 * 60 * 1000
HORIZON_MS = 60 * 60 * 1000


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


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


def utc(ms):
    return datetime.fromtimestamp(
        ms / 1000, timezone.utc
    ).isoformat()


def asof(path, now):
    rows = read_jsonl(path)
    valid = [
        row for row in rows
        if 0 <= now - int(
            row["collected_at_ms"]
        ) <= MAX_CG_AGE_MS
    ]
    if not valid:
        return None
    return max(
        valid,
        key=lambda row: int(row["collected_at_ms"])
    )


def direction_vote(value, high, low, weight):
    if value >= high:
        return weight
    if value <= low:
        return -weight
    return 0


def main():
    now = int(time.time() * 1000)

    if not ACTIVATION.exists():
        activation = {
            "model": "cg_direction_v1",
            "activated_at_ms": now,
            "activated_at_utc": utc(now),
            "mode": "prospective_shadow",
            "horizon_minutes": 60,
            "telegram_enabled": False,
            "trade_execution": False,
            "rule_version": "1.0"
        }
        ACTIVATION.write_text(
            json.dumps(
                activation, indent=2
            ) + "\n",
            encoding="utf-8"
        )

    activation = read_json(ACTIVATION)
    if now < int(activation["activated_at_ms"]):
        raise ValueError("Clock before activation")

    history = read_jsonl(HISTORY)
    current_hour = now // HORIZON_MS

    if any(
        int(row["prediction_time_ms"])
        // HORIZON_MS == current_hour
        for row in history
    ):
        print("Already recorded this UTC hour")
        return

    taker = asof(
        ROOT / "coinglass_taker_history.jsonl",
        now
    )
    oi = asof(
        ROOT / "coinglass_oi_history.jsonl",
        now
    )

    quote = read_json(ROOT / "okx_latest.json")
    market_ms = int(quote["market_time"])

    price_fresh = (
        quote["exchange"] == "OKX"
        and quote["symbol"] == "ETH-USDT-SWAP"
        and 0 <= now - market_ms
        <= MAX_PRICE_AGE_MS
    )

    score = 0
    votes = {}
    buy_values = {}
    oi_values = {}

    if taker:
        for name in ("All", "Binance", "OKX"):
            buy_values[name] = float(
                taker["data"][name]["buy_pct"]
            )

        votes["All"] = direction_vote(
            buy_values["All"], 51, 49, 2
        )
        votes["Binance"] = direction_vote(
            buy_values["Binance"], 50.5, 49.5, 1
        )
        votes["OKX"] = direction_vote(
            buy_values["OKX"], 50.5, 49.5, 1
        )
        score = sum(votes.values())

    if oi:
        for name in ("All", "Binance", "OKX"):
            oi_values[name] = float(
                oi["data"][name][
                    "open_interest_change_percent_1h"
                ]
            )

    if not taker or not oi or not price_fresh:
        signal = "UNCERTAIN"
        reason = "MISSING_OR_STALE_SOURCE"
    elif score >= 3:
        signal = "CG_UP"
        reason = "TAKER_BUY_CONSENSUS"
    elif score <= -3:
        signal = "CG_DOWN"
        reason = "TAKER_SELL_CONSENSUS"
    else:
        signal = "UNCERTAIN"
        reason = "INSUFFICIENT_TAKER_CONSENSUS"

    record = {
        "model": "cg_direction_v1",
        "rule_version": "1.0",
        "prediction_time_ms": now,
        "prediction_time_utc": utc(now),
        "target_time_ms": now + HORIZON_MS,
        "horizon_minutes": 60,
        "signal": signal,
        "score": score,
        "score_votes": votes,
        "reason": reason,
        "reference_price": (
            float(quote["price"])
            if price_fresh else None
        ),
        "reference_market_time_ms": (
            market_ms if price_fresh else None
        ),
        "cg_taker_snapshot_ms": (
            int(taker["collected_at_ms"])
            if taker else None
        ),
        "cg_oi_snapshot_ms": (
            int(oi["collected_at_ms"])
            if oi else None
        ),
        "taker_buy_pct": buy_values,
        "oi_change_1h_pct": oi_values,
        "oi_used_in_direction_score": False,
        "exchange_source_time_verified": False,
        "outcome": "PENDING",
        "simulation_only": True,
        "telegram_enabled": False,
        "trade_execution": False
    }

    with HISTORY.open(
        "a", encoding="utf-8"
    ) as f:
        f.write(
            json.dumps(record, sort_keys=True)
            + "\n"
        )

    LATEST.write_text(
        json.dumps(
            record, indent=2
        ) + "\n",
        encoding="utf-8"
    )

    print(
        "CG-Direction:",
        signal,
        "score:",
        score
    )
    print("Existing models untouched")
    print("Telegram disabled")


if __name__ == "__main__":
    main()
