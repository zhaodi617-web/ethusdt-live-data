"""Observer Ladder V1+ shadow risk overlay (research only).

Reads unchanged V1 prediction and official BLS event schedule via the existing
`event_risk_alert` module. Never changes V1 direction or score. Produces
transparent risk-gated view and a history. Telegram notices only when the
risk window is active, deduplicated by event / V1 prediction / risk stage.
"""
import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import event_risk_alert as events_module

V1_FILE = Path('observer_early_latest.json')
OUTPUT = Path('observer_v1_plus_latest.json')
HISTORY = Path('observer_v1_plus_history.jsonl')
STATE = Path('observer_v1_plus_sent.json')
MAX_V1_AGE = timedelta(minutes=75)


def evaluate(v1, events, now):
    if now.tzinfo is None:
        raise ValueError('now must have timezone')
    now = now.astimezone(timezone.utc)
    created = datetime.fromtimestamp(int(v1['prediction_time']) / 1000, timezone.utc)
    target = datetime.fromtimestamp(int(v1['target_time']) / 1000, timezone.utc)
    age = now - created
    if age < timedelta(0) or age > MAX_V1_AGE:
        raise ValueError('V1 record stale or future-dated')
    if target <= created:
        raise ValueError('Invalid prediction horizon')

    scored = []
    for evt in events:
        release = datetime.fromisoformat(evt['utc']).astimezone(timezone.utc)
        delta_minutes = (release - now).total_seconds() / 60
        if 0 <= delta_minutes <= 120:
            scored.append((release, evt, delta_minutes))
    scored.sort(key=lambda x: x[0])

    # Separate the event's schedule from unknown publication results.
    # A release within the original prediction's horizon is a stronger risk.
    crossing = [x for x in scored if created <= x[0] <= target]
    relevant = crossing[0] if crossing else (scored[0] if scored else None)
    risk = 'NONE'
    reason = 'No verified upcoming high-impact BLS release in 120-minute lookahead'
    if relevant:
        release, event, minutes_left = relevant
        if crossing:
            risk = 'PREDICTION_CROSSES_EVENT'
            reason = 'V1 60-minute horizon overlaps a scheduled high-impact release; ex-ante direction reliability unvalidated'
        else:
            risk = 'UPCOMING_WITHIN_120_MIN'
            reason = 'High-impact release upcoming; V1 forecast does not cross its scheduled time'

    # Do not claim that the event predicts UP/DOWN. Keep original V1 direction.
    direction = v1['signal']
    conf = ('EVENT_RISK_DOWNGRADED' if risk == 'PREDICTION_CROSSES_EVENT'
            else 'CAUTION_EVENT_NEAR' if risk != 'NONE'
            else 'UNADJUSTED_UNCALIBRATED')
    return {
        'model': 'observer_v1_plus_shadow_v0',
        'generated_at_utc': now.isoformat(),
        'v1_prediction_id': str(v1['prediction_time']),
        'v1_prediction_time_utc': created.isoformat(),
        'v1_target_time_utc': target.isoformat(),
        'v1_signal_original': direction,
        'v1_score_original': v1['score'],
        'v1_reference_price': v1['reference_price'],
        'event_risk': risk,
        'event_risk_reason': reason,
        'confidence_label': conf,
        'next_event': ({'name': relevant[1]['name'],
                        'utc': relevant[0].isoformat(),
                        'minutes_to_release': round(relevant[2], 1),
                        'source': relevant[1]['source'],
                        'source_status': relevant[1]['source_status']}
                       if relevant else None),
        'event_result_known': False,
        'action': ('RESEARCH_ONLY_EVENT_CAUTION' if risk == 'PREDICTION_CROSSES_EVENT'
                   else 'RESEARCH_ONLY_NO_NEW_DIRECTION'),
        'no_live_trading': True,
        'source_warning': 'Only BLS CPI/PPI/Employment releases currently covered; not an exhaustive macro-event feed',
    }


def append_once(rec):
    old = []
    if HISTORY.exists():
        old = [json.loads(x) for x in HISTORY.read_text(encoding='utf-8').splitlines() if x.strip()]
    identity = (rec['v1_prediction_id'], rec['event_risk'],
                rec['next_event']['utc'] if rec['next_event'] else '')
    if not any((x['v1_prediction_id'], x['event_risk'],
                x['next_event']['utc'] if x.get('next_event') else '') == identity for x in old):
        with HISTORY.open('a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def send_telegram(rec):
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        raise RuntimeError('Telegram credentials not configured')
    evt = rec['next_event']
    message = (
        '【Observer Ladder V1+｜事件风险综合预警】\n\n'
        f"V1 原始方向：{rec['v1_signal_original']}\n"
        f"V1 原始评分：{rec['v1_score_original']}\n"
        f"预测编号：{rec['v1_prediction_id']}\n"
        f"原始参考价：{rec['v1_reference_price']} USDT\n"
        f"重大事件：{evt['name']}\n"
        f"官方计划时间：{evt['utc']}\n"
        f"距离发布：约{evt['minutes_to_release']}分钟\n"
        f"来源：{evt['source']}\n"
        f"风险状态：{rec['event_risk']}\n\n"
        '事件实际结果尚未知。V1 方向保持不变，'
        '但其预测窗口跨越重大数据公布，应降低方向可信度；'
        '不能据此断言上涨或下跌，也不是交易指令。'
    )
    body = urllib.parse.urlencode({'chat_id': chat, 'text': message}).encode('utf-8')
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=body, method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp:
        response = json.load(resp)
    if not response.get('ok'):
        raise RuntimeError('Telegram acknowledgement missing')


def main(now=None, notify=True):
    now = now or datetime.now(timezone.utc)
    v1 = json.loads(V1_FILE.read_text(encoding='utf-8'))
    upcoming = events_module.get_events()
    rec = evaluate(v1, upcoming, now)
    OUTPUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    append_once(rec)
    if notify and rec['event_risk'] == 'PREDICTION_CROSSES_EVENT':
        evt = rec['next_event']
        key = f"{rec['v1_prediction_id']}|{evt['utc']}|{rec['event_risk']}"
        state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
        if key not in state:
            send_telegram(rec)
            state[key] = now.isoformat()
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print('V1+ crossing-event message delivered')
        else:
            print('V1+ already notified')
    print('V1+ risk:', rec['event_risk'], '| original:', rec['v1_signal_original'], rec['v1_score_original'])
    return rec


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-notify', action='store_true', help='Generate a research snapshot without Telegram')
    args = parser.parse_args()
    main(notify=not args.no_notify)
