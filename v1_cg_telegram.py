"""Observer Ladder V1-CG independent Telegram notifier.

Only new conflict alerts and their verification results.
Research only. Does not edit V1 or send trading orders.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

PRED = Path("v1_cg_predictions.jsonl")
VERIFY = Path("v1_cg_verifications.jsonl")
STATE = Path("v1_cg_telegram_state.json")

MODEL = "observer_v1_cg_shadow_v1"
MAX_ALERT_AGE = 15 * 60 * 1000
MAX_VERIFY_AGE = 20 * 60 * 1000


def read_lines(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def load_state():
    if not STATE.exists():
        return {"sent_events": []}

    return json.loads(
        STATE.read_text(encoding="utf-8")
    )


def save_state(state):
    STATE.write_text(
        json.dumps(
            state, ensure_ascii=False, indent=2
        ) + "\n",
        encoding="utf-8"
    )


def format_time(ms):
    return datetime.fromtimestamp(
        int(ms) / 1000,
        timezone.utc
    ).strftime("%m-%d %H:%M UTC")


def send_message(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Telegram secrets are missing"
        )

    url = (
        "https://api.telegram.org/bot"
        + token + "/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(
        url, data=payload, method="POST"
    )

    try:
        with urllib.request.urlopen(
            request, timeout=20
        ) as response:
            result = json.load(response)
    except Exception:
        raise RuntimeError(
            "Telegram delivery failed"
        ) from None

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram rejected message"
        )


def prediction_message(p):
    original = {
        "EARLY_UP": "预测上涨",
        "EARLY_DOWN": "预测下跌"
    }.get(
        p["original_v1_signal"], "不确定"
    )

    buy = p.get("all_taker_buy_pct")
    buy_text = (
        f"{float(buy):.2f}%"
        if buy is not None else "未知"
    )

    return "\n".join([
        "【Observer Ladder｜V1-CG 冲突预警】",
        "",
        f"时间：{format_time(p['prediction_time'])}",
        f"V1 原始方向：{original}",
        f"参考价格：{p['reference_price']} USDT",
        f"CoinGlass 主动买入：{buy_text}",
        "",
        "V1-CG 判断：FILTER（过滤）",
        f"冲突原因：{p['decision_reason']}",
        "预测期限：60分钟",
        "",
        "原始 V1 不变。",
        "60分钟后验证过滤是否合理。",
        "研究提醒，不是交易指令。"
    ])


def verification_message(v):
    original_result = v["baseline_result"]

    if original_result == "MISS":
        judgment = "过滤了一次错误方向预测"
    elif original_result == "HIT":
        judgment = "过滤了一次正确方向预测"
    else:
        judgment = "暂不能评价过滤效果"

    return "\n".join([
        "【Observer Ladder｜V1-CG 过滤核验】",
        "",
        f"原预测时间：{format_time(v['prediction_time'])}",
        f"核验时间：{format_time(v['verification_time'])}",
        f"V1 原始结果：{original_result}",
        f"价格变化：{v['return_pct']:+.3f}%",
        "",
        f"过滤评价：{judgment}",
        "",
        "仅验证价格方向。",
        "不代表实际交易收益。"
    ])


def collect_events(now):
    events = []

    for p in read_lines(PRED):
        if p.get("model") != MODEL:
            continue

        if p.get("cg_decision") != "FILTER":
            continue

        ts = int(p["prediction_time"])

        if not 0 <= now - ts <= MAX_ALERT_AGE:
            continue

        events.append((
            f"prediction:{ts}",
            prediction_message(p)
        ))

    for v in read_lines(VERIFY):
        if v.get("model") != MODEL:
            continue

        if v.get("cg_decision") != "FILTER":
            continue

        ts = int(v.get("verified_at_ms", 0))

        if not 0 <= now - ts <= MAX_VERIFY_AGE:
            continue

        event_id = (
            f"verification:{v['prediction_time']}"
        )

        events.append((
            event_id,
            verification_message(v)
        ))

    return events


def run():
    now = int(time.time() * 1000)
    state = load_state()
    sent = set(state.get("sent_events", []))

    events = collect_events(now)
    count = 0

    for event_id, message in events:
        if event_id in sent:
            continue

        send_message(message)

        sent.add(event_id)
        state["sent_events"] = sorted(sent)
        save_state(state)

        count += 1

    print("V1-CG new Telegram messages:", count)
    print("Other models untouched")


if __name__ == "__main__":
    run()
