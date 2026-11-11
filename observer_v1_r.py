"""Independent Observer Ladder V1-R multi-horizon shadow forecasting.

Never alters original V1, V2, V3 or Telegram. No live orders.
All features use confirmed candles known as of the prediction timestamp.
The horizons and thresholds are uncalibrated research hypotheses.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

SYMBOL = 'ETH-USDT-SWAP'
HORIZONS = (15, 30, 45, 60)
M15 = 900_000
HOUR = 3_600_000
FLAT_THRESHOLD = 0.0005  # same endpoint dead zone as original V1


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def read_lines(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []


def append_line(path, item):
    with Path(path).open('a', encoding='utf-8') as f:
        f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n')


def closed(candles, key):
    return sorted((b for b in candles[key] if b.get('confirmed') is True), key=lambda b: int(b['time']))


def evaluate_features(candles, ticker, now_ms):
    if candles.get('exchange') != 'OKX' or candles.get('symbol') != SYMBOL:
        raise ValueError('Candle market mismatch')
    if ticker.get('exchange') != 'OKX' or ticker.get('symbol') != SYMBOL:
        raise ValueError('Quote market mismatch')
    quote_time = int(ticker['market_time'])
    if not 0 <= now_ms - quote_time <= 10 * 60_000:
        raise ValueError('Stale or future-dated quote')
    bars = closed(candles, 'candles_15m')
    hours = closed(candles, 'candles_1h')
    if len(bars) < 10 or len(hours) < 6:
        raise ValueError('Insufficient confirmed candles')
    last = bars[-1]
    last_end = int(last['time']) + M15
    if not 0 <= now_ms - last_end <= 20 * 60_000:
        raise ValueError('Stale or future-dated confirmed candle')
    # Features must be independent by construction: the hourly background ends
    # before the newest 15-minute candle STARTS.
    prior_hours = [b for b in hours if int(b['time']) + HOUR <= int(last['time'])]
    if len(prior_hours) < 4:
        raise ValueError('Insufficient historical non-overlap hourly candles')
    short = float(last['close']) / float(bars[-2]['close']) - 1
    background = float(prior_hours[-1]['close']) / float(prior_hours[-4]['close']) - 1
    previous = bars[-9:-1]
    avg_v = sum(float(b['volume_usdt']) for b in previous) / len(previous)
    volume_ratio = float(last['volume_usdt']) / avg_v if avg_v > 0 else None
    high = max(float(b['high']) for b in previous)
    low = min(float(b['low']) for b in previous)
    price = float(ticker['price'])
    if price <= 0:
        raise ValueError('Non-positive quote price')
    breakout_up = float(last['close']) > high
    breakout_down = float(last['close']) < low
    # Classify context separately from directional forecasts. Not a trade filter.
    if background < -0.001 and short > 0.0005:
        context = 'COUNTERTREND_BOUNCE'
    elif background > 0.001 and short < -0.0005:
        context = 'COUNTERTREND_PULLBACK'
    elif short > 0.0005 and background > 0.001:
        context = 'UPTREND_CONTINUATION_HYPOTHESIS'
    elif short < -0.0005 and background < -0.001:
        context = 'DOWNTREND_CONTINUATION_HYPOTHESIS'
    else:
        context = 'MIXED_OR_SIDEWAYS'
    return {
        'short_15m': round(short, 8),
        'historical_nonoverlap_3h': round(background, 8),
        'volume_ratio': round(volume_ratio, 5) if volume_ratio is not None else None,
        'breakout_up': breakout_up,
        'breakout_down': breakout_down,
        'prior_structure_high': high,
        'prior_structure_low': low,
        'context': context,
        'reference_price': price,
        'quote_time_ms': quote_time,
        'latest_closed_bar_end_ms': last_end,
        'hourly_background_last_end_ms': int(prior_hours[-1]['time']) + HOUR,
    }


def forecasts(features, now_ms):
    short = features['short_15m']
    bg = features['historical_nonoverlap_3h']
    vol = features['volume_ratio'] or 0
    # Predeclared exploratory scores; longer horizons put more weight on
    # the non-overlapping historical background. No fitted probabilities.
    weights = {15: (0.8, 0.2), 30: (0.65, 0.35), 45: (0.45, 0.55), 60: (0.3, 0.7)}
    results = []
    for minutes in HORIZONS:
        ws, wb = weights[minutes]
        # Reweight 3h background to a per-15-minute-equivalent component.
        composite = ws * short + wb * bg / 12
        # Weak-volume signal retained as counterevidence, not a hidden veto.
        direction = 'UP' if composite > 0.00025 else 'DOWN' if composite < -0.00025 else 'UNCERTAIN'
        results.append({
            'horizon_minutes': minutes,
            'target_time_ms': now_ms + minutes * 60_000,
            'direction': direction,
            'uncalibrated_score': round(composite, 9),
            'weak_volume': vol < 0.8,
            'countertrend': (direction == 'UP' and bg < -0.001) or (direction == 'DOWN' and bg > 0.001),
            'weight_short': ws, 'weight_background': wb,
        })
    return results


def make_prediction(candles, ticker, now_ms):
    f = evaluate_features(candles, ticker, now_ms)
    return {
        'model': 'observer_v1_r_shadow_v0',
        'prediction_time_ms': now_ms,
        'prediction_time_utc': datetime.fromtimestamp(now_ms/1000, timezone.utc).isoformat(),
        'market': 'OKX ETH-USDT-SWAP',
        'reference_price': f['reference_price'],
        'features': f,
        'predictions': forecasts(f, now_ms),
        'uncalibrated': True,
        'simulation_only': True,
        'live_order_allowed': False,
        'note': 'Shadow hypothesis, not a buy/sell signal; no proof of positive trading expectancy',
    }


def verify(predictions, existing, candles, now_ms):
    bars = closed(candles, 'candles_15m')
    completed = {(int(r['prediction_time_ms']), int(r['horizon_minutes'])) for r in existing}
    new = []
    for prediction in predictions:
        t = int(prediction['prediction_time_ms'])
        for f in prediction['predictions']:
            minutes = int(f['horizon_minutes'])
            target = int(f['target_time_ms'])
            if now_ms < target or (t, minutes) in completed:
                continue
            # First fully confirmed 15m close after the exact target. Note
            # 0–15m verification lag, never use a close before the target.
            eligible = [b for b in bars if target <= int(b['time']) + M15 < target + M15 and
                        int(b['time']) + M15 <= now_ms]
            if not eligible:
                continue
            b = min(eligible, key=lambda x: int(x['time']))
            closed_ms = int(b['time']) + M15
            change = float(b['close']) / float(prediction['reference_price']) - 1
            if abs(change) <= FLAT_THRESHOLD:
                result = 'FLAT'
            elif f['direction'] == 'UNCERTAIN':
                result = 'ABSTAIN'
            elif (change > 0 and f['direction'] == 'UP') or (change < 0 and f['direction'] == 'DOWN'):
                result = 'HIT'
            else:
                result = 'MISS'
            new.append({
                'model': 'observer_v1_r_shadow_v0',
                'prediction_time_ms': t,
                'horizon_minutes': minutes,
                'target_time_ms': target,
                'direction': f['direction'],
                'reference_price': prediction['reference_price'],
                'verification_price': float(b['close']),
                'verification_time_ms': closed_ms,
                'verification_lag_minutes': round((closed_ms-target)/60_000, 4),
                'gross_price_change_pct': round(change*100, 5),
                'result': result,
                'paper_only': True,
            })
            completed.add((t, minutes))
    return new


def run(base=Path('.'), now_ms=None):
    base = Path(base)
    now_ms = int(time.time()*1000) if now_ms is None else int(now_ms)
    candles = read_json(base/'okx_candles.json')
    ticker = read_json(base/'okx_latest.json')
    pred_path = base/'v1_r_predictions.jsonl'
    val_path = base/'v1_r_verifications.jsonl'
    history = read_lines(pred_path)
    verified = read_lines(val_path)
    new_verified = verify(history, verified, candles, now_ms)
    for item in new_verified:
        append_line(val_path, item)
    # Immutable hourly shadow prediction. Do not backfill past prediction slots.
    current_hour = now_ms // HOUR
    new_prediction = None
    if not any(int(p['prediction_time_ms'])//HOUR == current_hour for p in history):
        new_prediction = make_prediction(candles, ticker, now_ms)
        append_line(pred_path, new_prediction)
        (base/'v1_r_latest.json').write_text(json.dumps(new_prediction,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    all_verifications = verified + new_verified
    per_horizon = {}
    for m in HORIZONS:
        a = [x for x in all_verifications if x['horizon_minutes'] == m]
        hits = sum(x['result'] == 'HIT' for x in a)
        misses = sum(x['result'] == 'MISS' for x in a)
        per_horizon[str(m)] = {'hits': hits, 'misses': misses,
             'flats': sum(x['result']=='FLAT' for x in a),
             'abstains': sum(x['result']=='ABSTAIN' for x in a),
             'direction_hit_rate_pct': round(100*hits/(hits+misses),2) if hits+misses else None}
    summary = {'model':'observer_v1_r_shadow_v0','evaluated_at_utc':datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat(),
               'predictions_total':len(history)+(new_prediction is not None),
               'new_predictions':int(new_prediction is not None),
               'new_verifications':len(new_verified), 'by_horizon':per_horizon,
               'warning':'Research-only. V1 untouched; no path or fill verification, no net PnL.'}
    (base/'v1_r_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return summary


if __name__ == '__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
