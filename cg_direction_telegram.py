"""Independent CG-Direction Telegram research alerts.

Display-only revision: add a stable sequential CG prediction number,
reused in corresponding verification messages. Existing signal, freshness,
Telegram delivery, and sent-event deduplication rules are unchanged.
No trading instructions; does not modify prediction/verification JSONL.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PREDICTIONS = Path("cg_direction_v1_predictions.jsonl")
VERIFICATIONS = Path("cg_direction_v1_verifications.jsonl")
STATE = Path("cg_direction_telegram_state.json")

PREDICTION_AGE_MS = 15 * 60 * 1000
VERIFICATION_AGE_MS = 20 * 60 * 1000
VALID_SIGNALS = {"CG_UP", "CG_DOWN"}

# Display timezone only.
# Model timestamps and verification remain in UTC.
DISPLAY_TZ = timezone(timedelta(hours=6, minutes=30))


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


def local_time(ms):
    return datetime.fromtimestamp(
        int(ms) / 1000,
        DISPLAY_TZ
    ).strftime("%m-%d %H:%M") + "（缅甸时间）"


def prediction_id_map(predictions):
    """Assign stable IDs from original prediction history."""

    timestamps = sorted({
        int(p["prediction_time_ms"])
        for p in predictions
        if p.get("model") == "cg_direction_v1"
        and p.get("prediction_time_ms") is not None
    })

    return {
        ts: "CG-" + str(i).zfill(4)
        for i, ts in enumerate(timestamps, start=1)
    }


def label_for(ts, id_map):
    return id_map.get(
        int(ts),
        "CG-TS-" + str(int(ts))
    )


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


def prediction_message(p, id_map):
    buy = p.get("taker_buy_pct", {})
    ts = int(p["prediction_time_ms"])
    label = label_for(ts, id_map)

    return "\n".join([
        "【Observer Ladder｜CG 方向预测】",
        "编号：" + label,
        "",
        "方向：" + direction_name(p["signal"]),
        "评分：" + str(p["score"]) + "（不是胜率）",
        "参考价：" + str(p["reference_price"]) + " USDT",
        "预测：" + local_time(ts),
        "UTC：" + utc_time(ts),
        "周期：未来60分钟",
        "",
        "CoinGlass 近4小时主动买入比例：",
        "全市场：" + str(buy.get("All", "未知")) + "%",
        "Binance：" + str(buy.get("Binance", "未知")) + "%",
        "OKX：" + str(buy.get("OKX", "未知")) + "%",
        "",
        "状态：等待核验",
        "核验消息将使用同一编号。",
        "仅供研究，不是交易指令。"
    ])


def verification_message(v, id_map):
    ts = int(v["prediction_time_ms"])
    label = label_for(ts, id_map)

    result = v["result"]
    price_change = v.get("return_pct")

    change_text = (
        f"{float(price_change):+.3f}%"
        if price_change is not None
        else "无法计算"
    )

    result_text = {
        "HIT": "HIT｜方向命中",
        "MISS": "MISS｜方向错误",
        "FLAT": "FLAT｜价格变化很小",
        "ABSTAIN": "ABSTAIN｜弃权"
    }.get(result, str(result))

    return "\n".join([
        "【Observer Ladder｜CG 方向核验】",
        "对应预测：" + label,
        "",
        "核验结果：" + result_text,
        "原预测：" + direction_name(v["signal"]),
        "预测：" + local_time(ts),
        "核验：" + local_time(v["verification_time_ms"]),
        "UTC核验：" + utc_time(v["verification_time_ms"]),
        "参考价：" + str(v["reference_price"]) + " USDT",
        "核验价：" + str(v["verification_price"]) + " USDT",
        "价格变化：" + change_text,
        "",
        "只核验方向，不代表实际交易收益。"
    ])


def collect_events(now):
    events = []

    predictions = read_jsonl(PREDICTIONS)
    id_map = prediction_id_map(predictions)

    for p in predictions:
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
            prediction_message(p, id_map)
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
            verification_message(v, id_map)
        ))

    return sorted(events, key=lambda x: x[0])


# Display-only formatter shared by CG1 and the three CG2 notification mechanisms.
# Never writes research data or changes the underlying event identifier.

def _field(lines, prefix):
    return next((s[len(prefix):] for s in lines if s.startswith(prefix)), "")


def _stamp(ms):
    return datetime.fromtimestamp(int(ms) / 1000, DISPLAY_TZ).strftime("%m-%d %H:%M") + " (UTC+06:30)"


def _lookup_event(folder, key, field):
    try:
        p = Path("private-storage/research") / folder / "state.json"
        events = json.loads(p.read_text(encoding="utf-8"))["events"]
        return next((e for e in events if str(e.get(field)) == str(key)), {})
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _direction_title(model, direction, action):
    if direction == 1:
        return f"🟢【{model}｜上涨{action}】"
    if direction == -1:
        return f"🔴【{model}｜下跌{action}】"
    return f"⚪【{model}｜方向{action}】"


def _result_title(model, result):
    symbol, word = {"HIT": ("✅", "命中"), "MISS": ("❌", "失败"),
                    "FLAT": ("⚪", "平局"), "ABSTAIN": ("⚪", "弃权")}.get(result, ("⚪", result or "待确认"))
    return f"{symbol}【{model}｜{word} {result}】"


def _direction(text):
    return 1 if "上涨" in text else -1 if "下跌" in text else 0


def _format_notification(message):
    lines = message.splitlines()
    if not lines:
        return message
    old = lines[0]
    key = _field(lines, "编号：")
    if old == "【Observer Ladder｜CG 方向预测】":
        lines[0] = _direction_title("CG1", _direction(_field(lines, "方向：")), "预警")
    elif old == "【Observer Ladder｜CG 方向核验】":
        result = _field(lines, "核验结果：").split("｜", 1)[0]
        lines[0] = _result_title("CG1", result)
    elif old in ("【Observer Ladder｜CG2 方向预警】", "【Observer Ladder｜CG2 规则确认】", "【Observer Ladder｜CG2 核验】", "【Observer Ladder｜CG2方向冲突】"):
        ts = int(key.removeprefix("CG2-"))
        e = _lookup_event("cg2_alert", ts, "observed_at_ms")
        level = {"【Observer Ladder｜CG2 方向预警】": "EARLY", "【Observer Ladder｜CG2 规则确认】": "CONFIRMED"}.get(old, e.get("level", ""))
        model = "CG2-" + level if level in ("EARLY", "CONFIRMED") else "CG2"
        if old.endswith("核验】"):
            lines[0] = _result_title(model, _field(lines, "结果："))
        elif old.endswith("冲突】"):
            lines[0] = f"⚠️【{model}｜方向冲突】"
        else:
            lines[0] = _direction_title(model, _direction(_field(lines, "方向：")), "预警" if level == "EARLY" else "确认")
        # IDs are deliberately unchanged so older messages and future results still match.
        lines = [s for s in lines if not s.startswith("观察：")]
        lines.insert(2, "预测：" + _stamp(ts))
        if e.get("target_time_ms"):
            lines = [s for s in lines if not s.startswith("目标：")]
            lines.insert(3, "目标：" + _stamp(e["target_time_ms"]))
        if old.endswith("核验】") and e.get("result_time_ms"):
            lines.insert(4 if e.get("target_time_ms") else 3, "核验：" + _stamp(e["result_time_ms"]))
        if old.endswith("核验】") and e.get("direction") in (1, -1):
            lines.append("原预测：" + ("上涨" if e["direction"] == 1 else "下跌"))
    elif old in ("【Observer Ladder｜CG2 REVERSAL】", "【Observer Ladder｜CG2 REVERSAL 核验】"):
        e = _lookup_event("cg2_reversal", key.removeprefix("REV-"), "id")
        if old.endswith("核验】"):
            lines[0] = _result_title("CG2-REVERSAL", _field(lines, "结果："))
        else:
            lines[0] = _direction_title("CG2-REVERSAL", _direction(_field(lines, "方向：")), "反转")
        if e.get("time"):
            lines.insert(2, "预测：" + _stamp(e["time"]))
        if old.endswith("核验】") and e.get("direction") in (1, -1):
            lines.append("原预测：" + ("上涨反转" if e["direction"] == 1 else "下跌反转"))
    else:
        return message
    return "\n".join(s.replace("（缅甸时间）", " (UTC+06:30)") for s in lines if not s.startswith(("UTC：", "UTC核验：")))


def format_notification(message):
    # Formatting errors must never prevent an otherwise valid alert from being sent.
    try:
        return _format_notification(message)
    except Exception:
        return message


def send_message(message):
    message = format_notification(message)
    token = os.environ.get(
        "CG_TELEGRAM_BOT_TOKEN", ""
    )

    chat_id = os.environ.get(
        "CG_TELEGRAM_CHAT_ID", ""
    )

    if not token or not chat_id:
        raise RuntimeError("Missing CG Telegram secrets")

    url = (
        "https://api.telegram.org/bot"
        + token
        + "/sendMessage"
    )

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
