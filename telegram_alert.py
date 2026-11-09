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
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=data, method="POST"
    )
    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        result = json.load(response)

    if not result.get("ok"):
        raise RuntimeError("Telegram send failed")


def chinese_reason(value):
    translations = {
        "15m upward momentum": "15分钟动量向上",
        "15m downward momentum": "15分钟动量向下",
        "1h upward momentum": "1小时动量向上",
        "1h downward momentum": "1小时动量向下",
        "OI and price rising": "持仓量与价格同步上升",
        "OI rising with falling price": "持仓量上升、价格下降",
        "Weak recent volume": "近期成交量偏弱",
        "Timeframes disagree": "不同周期方向不一致",
        "Funding data unavailable": "资金费率数据缺失"
    }
    return translations.get(value, value)


if "--test" in sys.argv:
    send(
        "Observer Ladder ETH\n\n"
        "中文通知测试成功。\n"
        "这是一条测试消息，不是交易信号。"
    )
    print("Telegram Chinese test sent")
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
    "偏上涨 EARLY_UP"
    if signal == "EARLY_UP"
    else "偏下跌 EARLY_DOWN"
)

prediction_time = datetime.fromtimestamp(
    int(prediction_id) / 1000,
    timezone.utc
).strftime("%Y-%m-%d %H:%M:%S UTC")

reasons = "、".join(
    chinese_reason(x)
    for x in prediction.get("reasons", [])
) or "暂无"

counter = "、".join(
    chinese_reason(x)
    for x in prediction.get("counterevidence", [])
) or "暂无记录"

message = (
    "【Observer Ladder ETH｜方向预警】\n\n"
    f"预测编号：{prediction_id}\n"
    f"预测时间：{prediction_time}\n\n"
    f"预测方向：{direction}\n"
    f"参考价格：{prediction['reference_price']} USDT\n"
    f"模型评分：{prediction['score']}\n"
    f"参考支撑：{prediction['support']} USDT\n"
    f"参考阻力：{prediction['resistance']} USDT\n"
    f"支持依据：{reasons}\n"
    f"反向证据：{counter}\n"
    "预测期限：未来60分钟\n\n"
    "注意：仅为研究预警，"
    "不是实盘买卖指令。"
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
