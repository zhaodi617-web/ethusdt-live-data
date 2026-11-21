"""Independent CG-Direction Telegram research alerts.

Does not alter predictions, verifications, V1 or V1-CG.
Only sends fresh CG_UP / CG_DOWN predictions and
their verification results. No trading instructions.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PREDICTIONS = Path("cg_direction_v1_predictions.jsonl")
VERIFICATIONS = Path("cg_direction_v1_verifications.jsonl")
STATE = Path("cg_direction_telegram_state.json")

PREDICTION_AGE_MS = 15 * 60 * 1000
VERIFICATION_AGE_MS = 20 * 60 * 1000
VALID_SIGNALS = {"CG_UP", "CG_DOWN"}


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


def utc_time(ms):
    return datetime.fromtimestamp(
        int(ms) / 1000,
        timezone.utc
    ).strftime("%m-%d %H:%M UTC")


def load_state():
    if not STATE.exists():
        return {"sent_events": []}

    return json.loads(
        STATE.read_text(encoding="utf-8")
    )


def save_state(state):
    STATE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )


def direction_name(signal):
    return {
        "CG_UP": "偏上涨",
        "CG_DOWN": "偏下跌"
    }.get(signal, "不确定")


def prediction_message(p):
    buy = p.get("taker_buy_pct", {})

    return "\n".join([
        "【Observer Ladder｜CG-Direction V1】",
        "",
        "方向预警：" + direction_name(p["signal"]),
        "预测时间：" + utc_time(p["prediction_time_ms"]),
        "参考价格：" + str(p["reference_price"]) + " USDT",
        "预测周期：未来60分钟",
        "评分：" + str(p["score"]),
        "",
        "CoinGlass 主动买入比例：",
        "全市场：" + str(buy.get("All", "未知")) + "%",
        "Binance：" + str(buy.get("Binance", "未知")) + "%",
        "OKX：" + str(buy.get("OKX", "未知")) + "%",
        "",
        "等待后续价格方向核验。",
        "研究预测，不是交易指令。"
    ])


def verification_message(v):
    result = v["result"]

    price_change = v.get("return_pct")
    change_text = (
        f"{float(price_change):+.3f}%"
        if price_change is not None
        else "无法计算"
    )

    return "\n".join([
        "【Observer Ladder｜CG-Direction 核验】",
        "",
        "原预测：" + direction_name(v["signal"]),
        "预测时间：" + utc_time(v["prediction_time_ms"]),
        "核验时间：" + utc_time(v["verification_time_ms"]),
        "参考价格：" + str(v["reference_price"]) + " USDT",
        "核验价格：" + str(v["verification_price"]) + " USDT",
        "价格变化：" + change_text,
        "",
        "核验结果：" + result,
        "",
        "仅核验方向，不代表实际交易收益。"
    ])


def collect_events(now):
    events = []

    for p in read_jsonl(PREDICTIONS):
        if p.get("model") != "cg_direction_v1":
            continue

        if p.get("signal") not in VALID_SIGNALS:
            continue

        if p.get("reference_price") is None:
            continue

        ts = int(p["prediction_time_ms"])

        if not 0 <= now - ts <= PREDICTION_AGE_MS:
            continue

        events.append((
            ts,
            "prediction:" + str(ts),
            prediction_message(p)
        ))

    for v in read_jsonl(VERIFICATIONS):
        if v.get("model") != "cg_direction_v1":
            continue

        if v.get("signal") not in VALID_SIGNALS:
            continue

        created = int(v.get("verified_at_ms", 0))

        if not 0 <= now - created <= VERIFICATION_AGE_MS:
            continue

        ts = int(v["prediction_time_ms"])

        events.append((
            created,
            "verification:" + str(ts),
            verification_message(v)
        ))

    return sorted(events, key=lambda x: x[0])


def send_message(message):
    token = os.environ.get("CG_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("CG_TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        raise RuntimeError("Missing CG Telegram secrets")

    url = "https://api.telegram.org/bot" + token + "/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:
            result = json.load(response)
    except Exception:
        raise RuntimeError(
            "CG Telegram delivery failed"
        ) from None

    if not result.get("ok"):
        raise RuntimeError(
            "CG Telegram rejected message"
        )


def main():
    now = int(time.time() * 1000)
    state = load_state()

    sent = set(state.get("sent_events", []))
    count = 0

    for _, event_id, message in collect_events(now):
        if event_id in sent:
            continue

        send_message(message)

        sent.add(event_id)
        state["sent_events"] = sorted(sent)
        save_state(state)

        count += 1

    print("CG Telegram new messages:", count)
    print("CG model predictions unchanged")
    print("V1 and V1-CG notifications untouched")


if __name__ == "__main__":
    main()
