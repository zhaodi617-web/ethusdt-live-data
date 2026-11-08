import json
import time
from pathlib import Path

CANDLES = Path("okx_candles.json")
PRICE = Path("okx_latest.json")
OI_HISTORY = Path("okx_derivatives_history.jsonl")
OUTPUT = Path("observer_early_predictions.jsonl")
LATEST = Path("observer_early_latest.json")

now = int(time.time() * 1000)

def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

candles = load(CANDLES)
ticker = load(PRICE)

if (
    ticker.get("exchange") != "OKX"
    or ticker.get("symbol") != "ETH-USDT-SWAP"
):
    raise RuntimeError("Wrong market source")

price_ts = int(ticker["market_time"])

if not 0 <= now - price_ts <= 900000:
    raise RuntimeError("Price is stale")

def closed(name):
    rows = [
        x for x in candles[name]
        if x["confirmed"]
    ]
    return sorted(rows, key=lambda x: x["time"])

m15 = closed("candles_15m")
h1 = closed("candles_1h")

if len(m15) < 9 or len(h1) < 3:
    raise RuntimeError("Insufficient candles")

price = float(ticker["price"])

# Use completed candles only
momentum_15m = (
    float(m15[-1]["close"])
    / float(m15[-5]["close"]) - 1
)

momentum_1h = (
    float(h1[-1]["close"])
    / float(h1[-2]["close"]) - 1
)

vol_new = sum(
    float(x["volume_usdt"]) for x in m15[-4:]
)
vol_old = sum(
    float(x["volume_usdt"]) for x in m15[-8:-4]
)

volume_ratio = (
    vol_new / vol_old if vol_old > 0 else 0
)

support = min(float(x["low"]) for x in m15[-8:])
resistance = max(float(x["high"]) for x in m15[-8:])

oi_change = None
funding_rate = None

if OI_HISTORY.exists():
    history = [
        json.loads(line)
        for line in OI_HISTORY.read_text(
            encoding="utf-8"
        ).splitlines() if line.strip()
    ]

    history = sorted(
        history, key=lambda x: x["oi_timestamp"]
    )

    valid = [
        x for x in history
        if 0 <= now - x["oi_timestamp"] <= 900000
    ]

    if valid:
        current = valid[-1]
        current_ts = current["oi_timestamp"]

        # Prefer a comparison roughly one hour earlier
        candidates = [
            x for x in history
            if 20 * 60000 <= (
                current_ts - x["oi_timestamp"]
            ) <= 100 * 60000
        ]

        if candidates:
            previous = min(
                candidates,
                key=lambda x: abs(
                    current_ts - x["oi_timestamp"]
                    - 3600000
                )
            )

            old_oi = float(
                previous["open_interest_eth"]
            )
            new_oi = float(
                current["open_interest_eth"]
            )

            if old_oi > 0:
                oi_change = new_oi / old_oi - 1

        if current.get("funding_rate") is not None:
            funding_rate = float(
                current["funding_rate"]
            )

# Baseline scoring: research hypothesis
score = 0
reasons = []
counterevidence = []

if momentum_15m > 0.0005:
    score += 1
    reasons.append("15m upward momentum")
elif momentum_15m < -0.0005:
    score -= 1
    reasons.append("15m downward momentum")

if momentum_1h > 0.0005:
    score += 1
    reasons.append("1h upward momentum")
elif momentum_1h < -0.0005:
    score -= 1
    reasons.append("1h downward momentum")

if oi_change is not None and volume_ratio > 1:
    if oi_change > 0.002 and momentum_15m > 0:
        score += 1
        reasons.append("OI and price rising")
    elif oi_change > 0.002 and momentum_15m < 0:
        score -= 1
        reasons.append("OI rising with falling price")

if volume_ratio < 0.8:
    counterevidence.append("Weak recent volume")

if momentum_15m * momentum_1h < 0:
    counterevidence.append("Timeframes disagree")

if funding_rate is None:
    counterevidence.append("Funding data unavailable")

signal = "UNCERTAIN"

if score >= 2:
    signal = "EARLY_UP"
elif score <= -2:
    signal = "EARLY_DOWN"

record = {
    "model": "observer_early_v1",
    "exchange": "OKX",
    "symbol": "ETH-USDT-SWAP",
    "prediction_time": now,
    "target_time": now + 3600000,
    "reference_price": price,
    "reference_type": "ticker_last",
    "signal": signal,
    "score": score,
    "momentum_15m": momentum_15m,
    "momentum_1h": momentum_1h,
    "volume_ratio": volume_ratio,
    "oi_change": oi_change,
    "funding_rate": funding_rate,
    "support": support,
    "resistance": resistance,
    "reasons": reasons,
    "counterevidence": counterevidence,
    "status": "PENDING",
    "simulation_only": True
}

# Preserve a single prediction per UTC hour
existing = []
if OUTPUT.exists():
    existing = [
        json.loads(line)
        for line in OUTPUT.read_text(
            encoding="utf-8"
        ).splitlines() if line.strip()
    ]

hour = now // 3600000

already = [
    x for x in existing
    if x["prediction_time"] // 3600000 == hour
]

if already:
    print("Existing prediction this hour")
else:
    with OUTPUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    LATEST.write_text(
        json.dumps(record, indent=2),
        encoding="utf-8"
    )
    print("Early warning recorded:", signal)
