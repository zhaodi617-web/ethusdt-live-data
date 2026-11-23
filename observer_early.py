"""Observer Ladder original V1, prospective rule 1.2 (research only).

V1.2 fixes the neutral-background confirmed-breakout exclusion.
Preserve existing prediction history; new forecasts are forward-only.
No profitability or predictive advantage is established.
"""
import json
import math
import time
from pathlib import Path

ROOT = Path('.')
RULE = '1.2'
M15 = 900_000
HOUR = 3_600_000
PRED = ROOT / 'observer_early_predictions.jsonl'
LATEST = ROOT / 'observer_early_latest.json'


def read_json(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def rows(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]


def numeric(raw):
    try:
        val = float(raw)
        return val if math.isfinite(val) else None
    except (ValueError, TypeError, OverflowError):
        return None


def confirmed(data, key, now, period):
    out = sorted(
        (
            x for x in data.get(key, [])
            if x.get('confirmed') is True
            and int(x['time']) + period <= now
        ),
        key=lambda x: int(x['time'])
    )
    tail = out[-10:]
    if any(
        int(b['time']) - int(a['time']) != period
        for a, b in zip(tail, tail[1:])
    ):
        raise RuntimeError(
            'Missing or duplicated recent confirmed candles'
        )
    return out


def oi_at_time(now):
    history = rows(ROOT / 'okx_derivatives_history.jsonl')
    valid = [
        r for r in history
        if r.get('oi_timestamp') is not None
        and 0 <= now - int(r['oi_timestamp']) <= 15 * 60_000
    ]
    if not valid:
        return None, None

    current = max(
        valid,
        key=lambda x: int(x['oi_timestamp'])
    )
    ts = int(current['oi_timestamp'])
    older = [
        r for r in history
        if r.get('oi_timestamp') is not None
        and 20 * 60_000 <=
        ts - int(r['oi_timestamp']) <= 100 * 60_000
    ]

    change = None
    if older:
        prev = min(
            older,
            key=lambda x: abs(
                ts - int(x['oi_timestamp']) - HOUR
            )
        )
        old_oi = numeric(prev.get('open_interest_eth'))
        new_oi = numeric(current.get('open_interest_eth'))

        if (
            old_oi is not None
            and new_oi is not None
            and old_oi > 0
            and new_oi >= 0
        ):
            change = new_oi / old_oi - 1

    return change, numeric(current.get('funding_rate'))


def make_prediction(now):
    ticker = read_json('okx_latest.json')
    data = read_json('okx_candles.json')

    if (
        ticker.get('exchange') != 'OKX'
        or ticker.get('symbol') != 'ETH-USDT-SWAP'
        or data.get('exchange') != 'OKX'
        or data.get('symbol') != 'ETH-USDT-SWAP'
    ):
        raise RuntimeError('Wrong market or exchange source')

    ts = int(ticker['market_time'])
    price = numeric(ticker.get('price'))

    if (
        price is None
        or price <= 0
        or not 0 <= now - ts <= 5 * 60_000
    ):
        raise RuntimeError(
            'Price stale, from future, or invalid'
        )

    m15 = confirmed(data, 'candles_15m', now, M15)
    h1 = confirmed(data, 'candles_1h', now, HOUR)

    if len(m15) < 10 or len(h1) < 6:
        raise RuntimeError(
            'Insufficient confirmed candles'
        )

    if not 0 <= now - (
        int(m15[-1]['time']) + M15
    ) <= 20 * 60_000:
        raise RuntimeError(
            'Stale latest confirmed 15m candle'
        )

    if not 0 <= now - (
        int(h1[-1]['time']) + HOUR
    ) <= 75 * 60_000:
        raise RuntimeError(
            'Stale latest confirmed 1h candle'
        )

    closes = [
        numeric(x.get('close'))
        for x in m15[-10:]
    ]
    hcloses = [
        numeric(x.get('close'))
        for x in h1[-6:]
    ]
    volumes = [
        numeric(x.get('volume_usdt'))
        for x in m15[-9:]
    ]
    highs = [
        numeric(x.get('high'))
        for x in m15[-9:-1]
    ]
    lows = [
        numeric(x.get('low'))
        for x in m15[-9:-1]
    ]

    if (
        any(
            x is None or x <= 0
            for x in closes + hcloses + highs + lows
        )
        or any(
            x is None or x < 0
            for x in volumes
        )
    ):
        raise RuntimeError(
            'Invalid confirmed candle features'
        )

    prev_1h_volume = sum(volumes[-8:-4])
    recent_1h_volume = sum(volumes[-4:])

    vol_ratio = (
        recent_1h_volume / prev_1h_volume
        if prev_1h_volume > 0 else 0.0
    )

    # Latest confirmed 15m impulse.
    short_mom = closes[-1] / closes[-2] - 1

    # Diagnostic only; not independently scored.
    hour_mom = hcloses[-1] / hcloses[-2] - 1

    # Older, nonoverlapping three-hour background.
    background = hcloses[-2] / hcloses[-5] - 1

    hi = max(highs)
    lo = min(lows)
    last_close = closes[-1]

    break_up = last_close > hi
    break_down = last_close < lo

    oi_change, funding = oi_at_time(now)

    score = 0
    reasons = []
    counter = []

    short_sign = (
        1 if short_mom >= .001
        else -1 if short_mom <= -.001
        else 0
    )

    background_sign = (
        1 if background >= .003
        else -1 if background <= -.003
        else 0
    )

    if short_sign:
        score += 2 * short_sign
        reasons.append(
            '最近15分钟动量向上'
            if short_sign > 0
            else '最近15分钟动量向下'
        )

    if background_sign:
        score += background_sign
        reasons.append(
            '较早的3小时趋势向上'
            if background_sign > 0
            else '较早的3小时趋势向下'
        )

    structure_sign = (
        1 if break_up
        else -1 if break_down
        else 0
    ) if vol_ratio >= 1.2 else 0

    if structure_sign:
        score += structure_sign
        reasons.append(
            '放量收盘突破上方结构'
            if structure_sign > 0
            else '放量收盘跌破下方结构'
        )

    if oi_change is not None and oi_change >= .002:
        reasons.append(
            '持仓量上升（本身不代表方向）'
        )

    if oi_change is None:
        counter.append('持仓量变化缺失')

    if funding is None:
        counter.append('资金费率数据缺失')

    if vol_ratio < .8:
        counter.append('近期成交量偏弱')

    countertrend = (
        short_sign != 0
        and background_sign != 0
        and short_sign != background_sign
    )

    if countertrend:
        counter.append(
            '短期动量与较早趋势冲突，可能出现反转'
        )

    if background_sign == 0:
        counter.append('较早趋势未提供明确方向')

    if short_sign == 0:
        counter.append('最新15分钟动量不足')

    if (
        short_sign > 0
        and last_close < hi
        and vol_ratio < 1.2
    ):
        counter.append('上涨尚无放量突破确认')
    elif (
        short_sign < 0
        and last_close > lo
        and vol_ratio < 1.2
    ):
        counter.append('下跌尚无放量跌破确认')

    # V1.1 existing gates.
    active = vol_ratio >= .8

    aligned = (
        background_sign == short_sign
        and short_sign != 0
    )

    reversal_confirmed = (
        countertrend
        and structure_sign == short_sign
        and vol_ratio >= 1.5
    )

    # V1.2 FIX:
    # An older neutral trend must not automatically block
    # a confirmed, strong, high-volume structure breakout.
    # Symmetric for UP and DOWN.
    neutral_breakout_confirmed = (
        background_sign == 0
        and short_sign != 0
        and structure_sign == short_sign
        and vol_ratio >= 1.5
        and abs(short_mom) >= .003
    )

    if neutral_breakout_confirmed:
        reasons.append(
            'V1.2：中性背景下强动量、放量结构突破确认'
        )

    direction_ok = (
        short_sign != 0
        and active
        and (
            aligned
            or reversal_confirmed
            or neutral_breakout_confirmed
        )
    )

    if direction_ok and score >= 3:
        signal = 'EARLY_UP'
    elif direction_ok and score <= -3:
        signal = 'EARLY_DOWN'
    else:
        signal = 'UNCERTAIN'

    context = (
        'CONFIRMED_REVERSAL_CANDIDATE'
        if reversal_confirmed
        else 'CONFIRMED_NEUTRAL_BREAKOUT'
        if neutral_breakout_confirmed
        else 'COUNTERTREND_WATCH'
        if countertrend
        else 'TREND_CONTINUATION'
        if aligned
        else 'NO_STRONG_ALIGNMENT'
    )

    return {
        'model': 'observer_early_v1',
        'rule_version': RULE,
        'exchange': 'OKX',
        'symbol': 'ETH-USDT-SWAP',
        'prediction_time': now,
        'target_time': now + HOUR,
        'reference_price': price,
        'reference_type': 'ticker_last',
        'signal': signal,
        'score': score,
        'momentum_15m': short_mom,
        'momentum_1h': hour_mom,
        'momentum_background_3h': background,
        'score_components': {
            'short_15m': 2 * short_sign,
            'older_nonoverlapping_3h': background_sign,
            'structure_confirmed': structure_sign,
            'oi': 0,
            'volume': 0
        },
        'volume_ratio': round(vol_ratio, 5),
        'oi_change': oi_change,
        'funding_rate': funding,
        'support': min(
            float(x['low']) for x in m15[-8:]
        ),
        'resistance': max(
            float(x['high']) for x in m15[-8:]
        ),
        'breakout_up': break_up,
        'breakout_down': break_down,
        'context': context,
        'reasons': reasons,
        'counterevidence': counter,
        'status': 'PENDING',
        'simulation_only': True,
        'unvalidated_thresholds': True,
    }


def run(now_ms=None):
    now = (
        int(time.time() * 1000)
        if now_ms is None
        else int(now_ms)
    )

    existing = rows(PRED)

    # Preserve all historical forecasts.
    # Never overwrite a prediction for the same hour.
    if any(
        int(x['prediction_time']) // HOUR == now // HOUR
        for x in existing
    ):
        print(
            'Existing prediction this hour; '
            'preserved without changes'
        )
        return None

    prediction = make_prediction(now)

    with PRED.open('a', encoding='utf-8') as f:
        f.write(
            json.dumps(
                prediction,
                ensure_ascii=False,
                sort_keys=True
            ) + '\n'
        )

    LATEST.write_text(
        json.dumps(
            prediction,
            ensure_ascii=False,
            indent=2
        ) + '\n',
        encoding='utf-8'
    )

    print(
        'Observer V1 rule',
        RULE,
        'forward-only prediction:',
        prediction['signal']
    )

    return prediction


if __name__ == '__main__':
    run()
