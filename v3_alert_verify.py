"""V3 shadow Telegram candidate alerts and forward-only price verification.

Standalone: reads existing V3 files and OKX market snapshot. Writes V3-only
notification and observation files. Never places orders or modifies V1/V2.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = 'v3_alert_state.json'
QUOTE_FILE = 'v3_quote_observations.jsonl'
RESULT_FILE = 'v3_alert_results.jsonl'
CURRENT_FILE = 'v3_alert_latest.json'
MAX_QUOTE_AGE_MS = 10 * 60_000
MAX_CANDIDATE_AGE_MS = 20 * 60_000
EXPIRY_MS = 60 * 60_000


def read_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def rows(path):
    p = Path(path)
    return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()] if p.exists() else []


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def append(path, data):
    with Path(path).open('a', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + '\n')


def to_ms(s):
    return int(datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp() * 1000)


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%m-%d %H:%M UTC')


def send_telegram(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        raise RuntimeError('Missing Telegram secrets: no notifications delivered')
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': message}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=payload, method='POST')
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError('Telegram API rejected message')


def fresh_quote(ticker, now_ms):
    if not ticker or ticker.get('exchange') != 'OKX' or ticker.get('symbol') != 'ETH-USDT-SWAP':
        raise ValueError('Unexpected market in OKX ticker')
    t = int(ticker['market_time'])
    if not 0 <= now_ms - t <= MAX_QUOTE_AGE_MS:
        raise ValueError('Market quote stale or future-dated')
    return {'market_time': t, 'price': float(ticker['price']), 'exchange':'OKX','symbol':'ETH-USDT-SWAP'}


def same_candidate(a, candidate):
    return str(a.get('id')) == str(candidate.get('observation_id'))


def eligible_candidate(obs, now_ms):
    if not obs or obs.get('status') != 'PAPER_LONG_WATCH':
        return False
    if obs.get('exchange') != 'OKX' or obs.get('symbol') != 'ETH-USDT-SWAP' or not obs.get('simulation_only'):
        return False
    created = to_ms(obs['observed_at_utc'])
    closed = to_ms(obs['reference_closed_15m_end_utc'])
    return 0 <= now_ms - created <= MAX_CANDIDATE_AGE_MS and created >= closed


def candidate_message(obs):
    return ("【V3｜新的做多研究候选】\n"
            f"编号：{obs['observation_id']}\n"
            f"生成：{obs['observed_at_utc']}\n"
            f"当时报价：{obs['ticker_snapshot']:.2f} USDT\n"
            f"后续突破观察价：{obs['paper_trigger_threshold']:.2f} USDT\n"
            f"历史结构低点：{obs['structure_low_before_signal']:.2f} USDT\n"
            f"量比：{obs['volume_ratio']}\n"
            "这是研究候选，不是实盘买入通知。只有信号产生之后的新报价才能用于验证触发；不能保证成交。")


def trigger_message(candidate, event):
    return ("【V3｜研究触发观察】\n"
            f"候选：{candidate['id']}\n"
            f"后续报价时间：{fmt(event['market_time'])}\n"
            f"观察报价：{event['price']:.2f} USDT\n"
            f"触发门槛：{candidate['threshold']:.2f} USDT\n"
            "价格已在两次采样之间越过门槛，仅表示观察到触发；不是已成交，也不代表实际入场价格或盈利。")


def run(base='.', now_ms=None, sender=send_telegram):
    base = Path(base)
    now_ms = int(time.time()*1000) if now_ms is None else int(now_ms)
    quote = fresh_quote(read_json(base/'okx_latest.json'), now_ms)
    state = read_json(base/STATE_FILE, {'candidates': {}})
    state.setdefault('candidates', {})
    history = rows(base/'v3_shadow_history.jsonl')
    obs = read_json(base/'v3_shadow_latest.json')
    events = rows(base/QUOTE_FILE)
    # Idempotent quote observation for a market timestamp; never invent intervening ticks.
    if not any(x.get('market_time') == quote['market_time'] for x in events):
        append(base/QUOTE_FILE, quote)
        events.append(quote)
    events.sort(key=lambda x: x['market_time'])
    recent = {x['observation_id']: x for x in history if eligible_candidate(x, now_ms)}
    if eligible_candidate(obs, now_ms):
        recent[obs['observation_id']] = obs
    notified = []
    for key, item in sorted(recent.items()):
        if key in state['candidates']:
            continue
        # Notify only after Telegram confirms successful delivery.
        sender(candidate_message(item))
        state['candidates'][key] = {
            'id':key, 'created_ms': to_ms(item['observed_at_utc']),
            'threshold':float(item['paper_trigger_threshold']),
            'structure_low':float(item['structure_low_before_signal']),
            'status':'WATCH', 'sent_ms':now_ms,
            'signal_quote':float(item['ticker_snapshot']),
        }
        save_json(base/STATE_FILE, state)
        notified.append(key)
    updates = []
    for key, c in sorted(state['candidates'].items()):
        if c['status'] in ('EXPIRED_NO_OBSERVED_CROSS', 'TRIGGER_OBSERVED', 'EXPIRED_NOT_EVALUABLE'):
            continue
        start = c['created_ms']
        # The first post-signal quote must be after the actual generation time.
        seq = [q for q in events if start < q['market_time'] <= start + EXPIRY_MS]
        # Use starting quote at generation only as baseline, never assume that market traded there after signal.
        below_seen = c['signal_quote'] < c['threshold']
        trigger = None
        for q in seq:
            p = float(q['price'])
            if below_seen and p >= c['threshold']:
                trigger = q
                break
            if p < c['threshold']:
                below_seen = True
        if trigger:
            sender(trigger_message(c,trigger))
            c.update({'status':'TRIGGER_OBSERVED','trigger_quote_time':trigger['market_time'],
                      'trigger_quote_price':trigger['price'], 'real_fill_confirmed':False,
                      'net_pnl':None})
            result={'id':key,'status':c['status'],'trigger_quote_time':trigger['market_time'],
                    'trigger_quote_price':trigger['price'], 'net_pnl':None,
                    'warning':'Observed sampling cross, not a fill; OHLC cannot establish intra-bar execution'}
            append(base/RESULT_FILE,result)
            updates.append(result)
        elif now_ms > start + EXPIRY_MS:
            # End-of-window quote gaps cannot prove that the threshold was never crossed.
            sufficient = len(seq) >= 2 and (seq[-1]['market_time'] - seq[0]['market_time'] >= 30*60_000)
            c['status'] = 'EXPIRED_NO_OBSERVED_CROSS' if sufficient else 'EXPIRED_NOT_EVALUABLE'
            sender("【V3｜候选观察结束】\n" + f"候选：{key}\n" +
                   ("观察窗口内采样报价未记录到有效突破，不能排除采样间隙曾触及。"
                    if sufficient else "报价覆盖不足，无法判断是否发生突破。") +
                   "\n研究记录已结束；不是止盈止损结果，也不代表实盘盈亏。")
            result={'id':key,'status':c['status'],'observations':len(seq),'net_pnl':None,
                    'warning':'Sampled quotes do not prove no intervening touches'}
            append(base/RESULT_FILE,result)
            updates.append(result)
        save_json(base/STATE_FILE,state)
    latest = {'evaluated_at_utc':datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat(),
              'market_quote_time':quote['market_time'],'market_quote':quote['price'],
              'total_candidates':len(state['candidates']),'new_alerts':notified,
              'updates':updates,'pending':sum(x['status']=='WATCH' for x in state['candidates'].values()),
              'note':'No live trading and no verified fills or net P&L; sampled market observations only'}
    save_json(base/CURRENT_FILE,latest)
    return latest


if __name__ == '__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
