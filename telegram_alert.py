import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LATEST = Path("observer_early_latest.json")
SENT = Path("telegram_last_sent.json")

token = os.environ.get("TELEGRAM_BOT_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID")

if not token or not chat_id:
    raise RuntimeError("Telegram secrets missing")


def send(message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=payload, method="POST"
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError("Telegram send failed")


if "--test" in sys.argv:
    send(
        "Observer Ladder ETH\n\n"
        "Telegram connection test successful.\n"
        "This is not a trading signal."
    )
    print("Telegram test sent")
    sys.exit(0)


if not LATEST.exists():
    raise RuntimeError("Prediction file missing")

prediction = json.loads(
    LATEST.read_text(encoding="utf-8")
)

signal = prediction["signal"]
prediction_id = str(prediction["prediction_time"])
now = int(time.time() * 1000)

age = now - int(prediction["prediction_time"])

if age < 0 or age > 15 * 60000:
    print("Prediction too old, skipping")
    sys.exit(0)

if signal not in ("EARLY_UP", "EARLY_DOWN"):
    print("No directional alert")
    sys.exit(0)

if SENT.exists():
    previous = json.loads(
        SENT.read_text(encoding="utf-8")
    )

    if previous.get("prediction_id") == prediction_id:
        print("Alert already sent")
        sys.exit(0)


direction = (
    "EARLY_UP - Potential upside"
    if signal == "EARLY_UP"
    else "EARLY_DOWN - Potential downside"
)

prediction_time = datetime.fromtimestamp(
    int(prediction_id) / 1000,
    timezone.utc
).strftime("%Y-%m-%d %H:%M:%S UTC")

reasons = ", ".join(
    prediction.get("reasons", [])
) or "Not available"

counter = ", ".join(
    prediction.get("counterevidence", [])
) or "None recorded"

message = (
    "Observer Ladder ETH Alert\n\n"
    f"Prediction ID: {prediction_id}\n"
    f"Prediction Time: {prediction_time}\n\n"
    f"Signal: {direction}\n"
    f"Reference: {prediction['reference_price']} USDT\n"
    f"Score: {prediction['score']}\n"
    f"Support: {prediction['support']}\n"
    f"Resistance: {prediction['resistance']}\n"
    f"Evidence: {reasons}\n"
    f"Counterevidence: {counter}\n"
    "Horizon: 60 minutes\n\n"
    "Research alert only. Not an order."
)

send(message)

SENT.write_text(
    json.dumps({
        "prediction_id": prediction_id,
        "sent_at": now,
        "signal": signal
    }, indent=2),
    encoding="utf-8"
)

print("Observer Telegram alert sent")
