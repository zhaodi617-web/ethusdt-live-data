import json
from datetime import datetime, timezone
from pathlib import Path

now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

with open("okx_candles.json", encoding="utf-8") as f:
    data = json.load(f)

def confirmed_candles(name):
    rows = [c for c in data[name] if c["confirmed"]]
    return sorted(rows, key=lambda c: c["time"])

m15 = confirmed_candles("candles_15m")
h1 = confirmed_candles("candles_1h")

if len(m15) < 8 or len(h1) < 4:
    raise RuntimeError("Not enough confirmed candles")

# Check whether market data is fresh
source_time = datetime.fromisoformat(
    data["collected_at"]
)
age_seconds = (
    datetime.now(timezone.utc) - source_time
).total_seconds()

if age_seconds < 0 or age_seconds > 900:
    raise RuntimeError("Market data is stale")

# Use only closed candles
last_15m_close = float(m15[-1]["close"])
momentum_15m = (
    last_15m_close / float(m15[-5]["close"]) - 1
)
momentum_1h = (
    float(h1[-1]["close"]) / float(h1[-4]["close"]) - 1
)

recent_volume = sum(
    float(c["volume_usdt"]) for c in m15[-4:]
)
previous_volume = sum(
    float(c["volume_usdt"]) for c in m15[-8:-4]
)
volume_ratio = (
    recent_volume / previous_volume
    if previous_volume > 0 else 0
)

# Simple baseline, not the full Observer Ladder
direction = "NO_SIGNAL"

if (
    momentum_15m > 0.0015
    and momentum_1h > 0
    and volume_ratio > 1
):
    direction = "UP"
elif (
    momentum_15m < -0.0015
    and momentum_1h < 0
    and volume_ratio > 1
):
    direction = "DOWN"

invalid_level = None
if direction == "UP":
    invalid_level = min(
        float(c["low"]) for c in m15[-4:]
    )
elif direction == "DOWN":
    invalid_level = max(
        float(c["high"]) for c in m15[-4:]
    )

prediction = {
    "model": "baseline_v1",
    "exchange": "OKX",
    "symbol": "ETH-USDT-SWAP",
    "prediction_time": now_ms,
    "target_time": now_ms + 3600000,
    "reference_price": last_15m_close,
    "reference_type": "last_confirmed_15m_close",
    "direction": direction,
    "invalid_level": invalid_level,
    "momentum_15m": round(momentum_15m, 6),
    "momentum_1h": round(momentum_1h, 6),
    "volume_ratio": round(volume_ratio, 4),
    "status": "PENDING"
}

# Preserve previous predictions
path = Path("eth_predictions.jsonl")
previous = []

if path.exists():
    previous = path.read_text(
        encoding="utf-8"
    ).splitlines()

# Only create one prediction per UTC hour
hour_key = now_ms // 3600000

for line in previous:
    old = json.loads(line)
    if old["prediction_time"] // 3600000 == hour_key:
        print("Prediction already exists this hour")
        raise SystemExit(0)

with path.open("a", encoding="utf-8") as f:
    f.write(json.dumps(prediction) + "\n")

print("Prediction recorded:", prediction)
