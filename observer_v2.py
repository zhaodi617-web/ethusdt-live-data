"""Observer Ladder V2: standalone, paper-only 15m confirmation research.

Reads existing OKX files, writes separate v2 JSON/JSONL outputs.
No trading API, no Telegram notification, and no changes to V1.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

CANDLES = Path('okx_candles.json')
TICKER = Path('okx_latest.json')
DERIVATIVES = Path('okx_derivatives.json')
LATEST = Path('observer_v2_latest.json')
HISTORY = Path('observer_v2_observations.jsonl')


def read(path):
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def bars(source, key):
    return sorted((x for x in source[key] if x['confirmed']),
                  key=lambda x: int(x['time']))


def close(x):
    return float(x['close'])


def val(x, name):
    return float(x[name])


def output(record):
    LATEST.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    previous = []
    if HISTORY.exists():
        previous = [json.loads(s) for s in HISTORY.read_text(encoding='utf-8').splitlines() if s.strip()]
    if not any(x['observation_id'] == record['observation_id'] for x in previous):
        with HISTORY.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    print('V2 paper observation:', record['status'], record['direction'])


now = int(time.time() * 1000)
candles = read(CANDLES)
ticker = read(TICKER)
derivatives = read(DERIVATIVES)
for data in (candles, ticker, derivatives):
    if data.get('exchange') != 'OKX' or data.get('symbol') != 'ETH-USDT-SWAP':
        raise RuntimeError('Market identity mismatch')

m15 = bars(candles, 'candles_15m')
h1 = bars(candles, 'candles_1h')
if len(m15) < 12 or len(h1) < 5:
    raise RuntimeError('Insufficient confirmed candles')

last = m15[-1]
last_end = int(last['time']) + 15 * 60_000
hour_end = int(h1[-1]['time']) + 60 * 60_000
price_time = int(ticker['market_time'])
oi_time = int(derivatives['open_interest']['timestamp'])
funding_time = int(derivatives['funding']['timestamp'])
# Never silently use old or future data.
for label, ts, max_age in (
    ('15m candle', last_end, 20 * 60_000),
    ('1h candle', hour_end, 70 * 60_000),
    ('ticker', price_time, 10 * 60_000),
    ('open interest', oi_time, 20 * 60_000),
    ('funding', funding_time, 20 * 60_000),
):
    age = now - ts
    if age < 0 or age > max_age:
        raise RuntimeError(f'{label} is stale or future-dated')

# Closed 15m candle, with a preceding 8-candle structure that excludes it.
previous = m15[-9:-1]
structure_high = max(val(x, 'high') for x in previous)
structure_low = min(val(x, 'low') for x in previous)
last_close = close(last)
prev_close = close(m15[-2])
# 1h direction uses actual completed 1h closes; avoids double-counting
# the 15m change as two independent confirmations.
h1_momentum = close(h1[-1]) / close(h1[-4]) - 1
volume_current = val(last, 'volume_usdt')
volume_previous_mean = sum(val(x, 'volume_usdt') for x in m15[-9:-1]) / 8
volume_ratio = volume_current / volume_previous_mean if volume_previous_mean > 0 else None

up_break = prev_close <= structure_high and last_close > structure_high
down_break = prev_close >= structure_low and last_close < structure_low
volume_confirmed = volume_ratio is not None and volume_ratio >= 1.2
up_confirmed = up_break and volume_confirmed and h1_momentum > 0.001
down_confirmed = down_break and volume_confirmed and h1_momentum < -0.001

status = 'WATCH'
direction = 'UNCERTAIN'
reason = []
counterevidence = []
if up_confirmed:
    status, direction = 'PAPER_SETUP', 'UP'
    reason = ['15m close broke prior 8-bar high', '15m volume >= 1.2x prior average', 'independent 1h trend upward']
elif down_confirmed:
    status, direction = 'PAPER_SETUP', 'DOWN'
    reason = ['15m close broke prior 8-bar low', '15m volume >= 1.2x prior average', 'independent 1h trend downward']
else:
    if up_break or down_break:
        reason.append('15m structure breakout observed, confirmation incomplete')
    if not volume_confirmed:
        counterevidence.append('volume confirmation absent')
    if abs(h1_momentum) <= 0.001:
        counterevidence.append('1h directional trend too weak')
    if not (up_break or down_break):
        counterevidence.append('no new 15m structure breakout')

# Explicitly refuse to translate historical breakout boundaries into a live order.
# The hypothetical stop and target are research-only, based on structure and 1.5R.
entry = stop = target = risk_pct = None
if status == 'PAPER_SETUP':
    entry = last_close
    stop = structure_low if direction == 'UP' else structure_high
    distance = abs(entry - stop)
    if distance <= 0 or distance / entry > 0.015:
        status = 'WATCH'
        counterevidence.append('structure stop invalid or excessively far (over 1.5%)')
        entry = stop = None
    else:
        target = entry + 1.5 * distance if direction == 'UP' else entry - 1.5 * distance
        risk_pct = round(distance / entry * 100, 4)

record = {
    'model': 'observer_ladder_v2_paper_v0',
    'market': 'OKX ETH-USDT-SWAP',
    'observation_id': str(int(last['time'])),
    'generated_at_utc': datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
    'reference_candle_close_time': last_end,
    'status': status,
    'direction': direction,
    'last_confirmed_15m_close': last_close,
    'latest_ticker_price': float(ticker['price']),
    'prior_structure_low': structure_low,
    'prior_structure_high': structure_high,
    'volume_ratio': round(volume_ratio, 4) if volume_ratio is not None else None,
    'h1_momentum_3h': round(h1_momentum, 6),
    'oi_usd_snapshot': derivatives['open_interest'].get('usd_value'),
    'funding_rate_snapshot': derivatives['funding'].get('rate'),
    'paper_entry_at_closed_candle': entry,
    'paper_invalidation_level': stop,
    'paper_target_1_5R': round(target, 3) if target is not None else None,
    'paper_risk_distance_pct': risk_pct,
    'reasons': reason,
    'counterevidence': counterevidence,
    'not_used_for_confirmation': ['OI delta unavailable without aligned snapshots', 'funding is descriptive, not a trading trigger'],
    'assumptions': ['1.2x volume and 0.1% 1h thresholds are unvalidated hypotheses', 'paper entry assumes filled at the confirmed candle close', 'fees/slippage/funding not yet modeled'],
    'simulation_only': True,
    'live_order_allowed': False,
}
output(record)
