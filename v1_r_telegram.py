"""V1-R significant-disagreement Telegram notifier.

Independent of V1/V3 alert code. Research alerts only, never trading instructions.
Only sends on new, same-UTC-hour predictions with material directional disagreement.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

V1 = Path('observer_early_latest.json')
V1R = Path('v1_r_latest.json')
SENT = Path('v1_r_telegram_state.json')
HOUR_MS = 3600000
MAX_AGE_MS = 90 * 60000


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def hour(ms):
    return int(ms) // HOUR_MS


def evaluate(v1, v1r, now_ms):
    """Return (event_kind, message, id) or None, with no I/O."""
    t1 = int(v1['prediction_time'])
    tr = int(v1r['prediction_time_ms'])
    if not (0 <= now_ms - t1 <= MAX_AGE_MS and 0 <= now_ms - tr <= MAX_AGE_MS):
        return None
    if hour(t1) != hour(tr):
        return None
    if v1.get('model') != 'observer_early_v1' or v1r.get('model') != 'observer_v1_r_shadow_v0':
        return None
    if v1.get('exchange') != 'OKX' or v1.get('symbol') != 'ETH-USDT-SWAP' or v1r.get('market') != 'OKX ETH-USDT-SWAP':
        return None
    signals = {int(p['horizon_minutes']): p['direction'] for p in v1r['predictions']}
    if set(signals) != {15, 30, 45, 60} or any(s not in ('UP', 'DOWN', 'UNCERTAIN') for s in signals.values()):
        return None
    a, b, d = signals[15], signals[30], signals[60]
    v = v1.get('signal')
    v1_dir = {'EARLY_UP': 'UP', 'EARLY_DOWN': 'DOWN'}.get(v)
    if v1_dir in ('UP', 'DOWN') and d in ('UP', 'DOWN') and v1_dir != d:
        kind = 'V1_V1R_60M_OPPOSITE'
        headline = 'V1 与 V1-R 的60分钟方向相反'
    elif a in ('UP', 'DOWN') and a == b and d in ('UP', 'DOWN') and a != d:
        kind = 'V1R_SHORT_LONG_OPPOSITE'
        headline = 'V1-R 短期与60分钟方向冲突'
    else:
        return None
    name = {'UP': '涨', 'DOWN': '跌', 'UNCERTAIN': '不确定'}
    v1label = name.get(v1_dir, '不确定')
    when = datetime.fromtimestamp(tr / 1000, tz=timezone.utc).strftime('%m-%d %H:%M UTC')
    lines = [
        '【ETH｜V1-R 重要分歧】',
        f'时间：{when}',
        f'V1（60分钟）：{v1label}',
        'V1-R：' + '｜'.join(f'{m}分{name[signals[m]]}' for m in (15, 30, 45, 60)),
        f'关键变化：{headline}',
        '研究预警，非买入或平仓指令。',
    ]
    return kind, '\n'.join(lines), str(hour(tr))


def send_telegram(msg, token, chat_id):
    url = 'https://api.telegram.org/bot' + token + '/sendMessage'
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': msg}).encode()
    request = urllib.request.Request(url, data=payload, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
        if not result.get('ok'):
            raise RuntimeError('Telegram returned unsuccessful response')
    except Exception as e:
        # Do not expose token-bearing URL in workflow logs.
        raise RuntimeError('Telegram delivery failed; check bot credentials and connectivity') from None


def run(now_ms=None):
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not V1.exists() or not V1R.exists():
        print('Waiting for both model outputs')
        return 'no_data'
    v1, v1r = load(V1), load(V1R)
    event = evaluate(v1, v1r, now_ms)
    if not event:
        print('No significant disagreement; staying silent')
        return 'quiet'
    kind, message, hour_id = event
    if SENT.exists() and load(SENT).get('utc_hour') == hour_id:
        print('Already notified this UTC hour; staying silent')
        return 'duplicate'
    token, chat_id = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        raise RuntimeError('Telegram secrets missing')
    send_telegram(message, token, chat_id)
    SENT.write_text(json.dumps({'utc_hour': hour_id, 'kind': kind,
                                 'v1_prediction_time': int(v1['prediction_time']),
                                 'v1r_prediction_time': int(v1r['prediction_time_ms']),
                                 'sent_at_ms': now_ms}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'Chinese V1-R disagreement alert sent: {kind}')
    return 'sent'


if __name__ == '__main__':
    run()
