"""Observer Ladder CG-Direction V2 independent prospective research.

Runs every ~5 minutes, records at most one 60-minute hypothesis per UTC hour.
Does not modify CG V1 / V1 / V3, send alerts, or trade. Thresholds are
predeclared exploratory hypotheses, not calibrated probabilities.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.')
VERSION = 'cg_direction_v2_shadow_1.0'
HOUR = 3_600_000
M15 = 900_000
PRED = ROOT / 'cg_direction_v2_predictions.jsonl'
VERIFY = ROOT / 'cg_direction_v2_verifications.jsonl'
LATEST = ROOT / 'cg_direction_v2_latest.json'
SUMMARY = ROOT / 'cg_direction_v2_summary.json'
ACTIVATION = ROOT / 'cg_direction_v2_activation.json'


def json_file(name, default=None):
    p = ROOT / name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def records(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()] if path.exists() else []


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def append(path, obj):
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + '\n')


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def latest_asof(history, cutoff, max_age):
    possible = [x for x in history if 0 <= cutoff - int(x['collected_at_ms']) <= max_age]
    return max(possible, key=lambda x: int(x['collected_at_ms'])) if possible else None


def taker_change_30m(history, at_ms, current):
    if not current:
        return None, None
    baseline_at = at_ms - 30 * 60_000
    older = [x for x in history if baseline_at - 7 * 60_000 <= int(x['collected_at_ms']) <= baseline_at + 7 * 60_000 and int(x['collected_at_ms']) < int(current['collected_at_ms'])]
    if not older:
        return None, None
    old = min(older, key=lambda x: abs(int(x['collected_at_ms']) - baseline_at))
    diff = float(current['data']['All']['buy_pct']) - float(old['data']['All']['buy_pct'])
    return round(diff, 5), int(old['collected_at_ms'])


def confirmed_m15(candles, now):
    if not candles or candles.get('exchange') != 'OKX' or candles.get('symbol') != 'ETH-USDT-SWAP':
        return []
    return sorted((x for x in candles.get('candles_15m', []) if x.get('confirmed') is True and int(x['time']) + M15 <= now), key=lambda x: int(x['time']))


def score_from_features(taker, oi, drift, m15, now):
    votes = {'all_taker': 0, 'binance_taker': 0, 'okx_taker': 0,
             'taker_30m_drift': 0, 'price_momentum_15m': 0,
             'confirmed_structure': 0, 'oi_price_confirmation': 0}
    metrics = {}
    if taker is None or oi is None or len(m15) < 10:
        return 0, votes, metrics, 'MISSING_CG_OR_CONFIRMED_CANDLES'
    if not 0 <= now - (int(m15[-1]['time']) + M15) <= 20 * 60_000:
        return 0, votes, metrics, 'STALE_CONFIRMED_CANDLE'
    all_buy = float(taker['data']['All']['buy_pct'])
    b_buy = float(taker['data']['Binance']['buy_pct'])
    o_buy = float(taker['data']['OKX']['buy_pct'])
    oi_change = float(oi['data']['All']['open_interest_change_percent_1h'])
    if not all(0 <= x <= 100 for x in (all_buy, b_buy, o_buy)):
        return 0, votes, metrics, 'INVALID_TAKER_PERCENT'
    last, before = m15[-1], m15[-9:-1]
    momentum = float(last['close']) / float(m15[-2]['close']) - 1
    baseline_volume = sum(float(x['volume_usdt']) for x in before) / 8
    if baseline_volume <= 0:
        return 0, votes, metrics, 'INVALID_VOLUME_BASELINE'
    vol_ratio = float(last['volume_usdt']) / baseline_volume
    high = max(float(x['high']) for x in before)
    low = min(float(x['low']) for x in before)
    up_break = float(last['close']) > high
    down_break = float(last['close']) < low
    votes['all_taker'] = 2 if all_buy >= 51 else -2 if all_buy <= 49 else 0
    votes['binance_taker'] = 1 if b_buy >= 50.5 else -1 if b_buy <= 49.5 else 0
    votes['okx_taker'] = 1 if o_buy >= 50.5 else -1 if o_buy <= 49.5 else 0
    if drift is not None:
        votes['taker_30m_drift'] = 1 if drift >= .75 else -1 if drift <= -.75 else 0
    votes['price_momentum_15m'] = 1 if momentum >= .001 else -1 if momentum <= -.001 else 0
    if vol_ratio >= 1.2:
        votes['confirmed_structure'] = 2 if up_break else -2 if down_break else 0
    # OI expansion is directionless on its own; score it only with contemporaneous price movement.
    if oi_change >= .4:
        votes['oi_price_confirmation'] = 1 if momentum > .0005 else -1 if momentum < -.0005 else 0
    score = sum(votes.values())
    metrics = {'all_taker_buy_pct': all_buy, 'binance_taker_buy_pct': b_buy, 'okx_taker_buy_pct': o_buy,
               'all_oi_change_1h_pct': oi_change, 'taker_30m_buy_share_change_pp': drift,
               'price_return_15m_pct': round(100 * momentum, 5), 'volume_ratio': round(vol_ratio, 4),
               'prior_2h_high': high, 'prior_2h_low': low,
               'confirmed_breakout_up': up_break, 'confirmed_breakout_down': down_break,
               'deleveraging_oi': oi_change <= -.5}
    # Do not claim consensus when the all-market taker side conflicts with the direction.
    if score >= 4 and all_buy >= 50 and momentum > 0:
        signal = 'CG_V2_UP'
    elif score <= -4 and all_buy <= 50 and momentum < 0:
        signal = 'CG_V2_DOWN'
    else:
        signal = 'UNCERTAIN'
    return score, votes, metrics, signal


def prediction(now):
    ticker = json_file('okx_latest.json')
    candles = json_file('okx_candles.json')
    taker_hist = records(ROOT / 'coinglass_taker_history.jsonl')
    oi_hist = records(ROOT / 'coinglass_oi_history.jsonl')
    taker = latest_asof(taker_hist, now, 20 * 60_000)
    oi = latest_asof(oi_hist, now, 20 * 60_000)
    quote_good = bool(ticker and ticker.get('exchange') == 'OKX' and ticker.get('symbol') == 'ETH-USDT-SWAP'
                      and 0 <= now - int(ticker['market_time']) <= 5 * 60_000 and float(ticker['price']) > 0)
    drift, baseline_ms = taker_change_30m(taker_hist, now, taker)
    bars = confirmed_m15(candles, now)
    score, votes, metrics, verdict = score_from_features(taker, oi, drift, bars, now)
    if not quote_good or taker is None or oi is None or not bars:
        verdict = 'MISSING_OR_STALE_INPUT'
    signal = verdict if verdict in ('CG_V2_UP', 'CG_V2_DOWN') else 'UNCERTAIN'
    return {'model': VERSION, 'prediction_time_ms': now, 'prediction_time_utc': utc(now),
            'target_time_ms': now + HOUR, 'horizon_minutes': 60,
            'signal': signal, 'score': score, 'score_votes': votes, 'features': metrics,
            'reason': verdict, 'reference_price': float(ticker['price']) if quote_good else None,
            'reference_market_time_ms': int(ticker['market_time']) if quote_good else None,
            'cg_taker_snapshot_ms': int(taker['collected_at_ms']) if taker else None,
            'cg_oi_snapshot_ms': int(oi['collected_at_ms']) if oi else None,
            'taker_baseline_30m_snapshot_ms': baseline_ms,
            'source_exchange_timestamps_verified': False,
            'predeclared_thresholds_uncalibrated': True,
            'simulation_only': True, 'live_order_allowed': False, 'telegram_enabled': False}


def verify(preds, done, bars, now):
    results = []
    finished = {int(v['prediction_time_ms']) for v in done}
    for p in preds:
        t, target = int(p['prediction_time_ms']), int(p['target_time_ms'])
        if t in finished or now < target:
            continue
        valid = [x for x in bars if target <= int(x['time']) + M15 < target + M15]
        if not valid:
            continue
        bar = min(valid, key=lambda x: int(x['time']))
        close_ms = int(bar['time']) + M15
        if close_ms > now:
            continue
        reference = p.get('reference_price')
        if reference is None:
            result, change, direction = 'UNVERIFIABLE', None, 'UNKNOWN'
        else:
            change = float(bar['close']) / float(reference) - 1
            direction = 'FLAT' if abs(change) <= .0005 else 'UP' if change > 0 else 'DOWN'
            if p['signal'] == 'UNCERTAIN':
                result = 'ABSTAIN'
            elif direction == 'FLAT':
                result = 'FLAT'
            elif (p['signal'] == 'CG_V2_UP' and direction == 'UP') or (p['signal'] == 'CG_V2_DOWN' and direction == 'DOWN'):
                result = 'HIT'
            else:
                result = 'MISS'
        result_row = {'model': VERSION, 'prediction_time_ms': t, 'target_time_ms': target,
                      'verification_time_ms': close_ms, 'verified_at_ms': now,
                      'verification_lag_minutes': round((close_ms - target) / 60_000, 4),
                      'signal': p['signal'], 'reference_price': reference,
                      'verification_price': float(bar['close']),
                      'return_pct': round(100 * change, 5) if change is not None else None,
                      'market_direction': direction, 'result': result,
                      'method': 'first_confirmed_15m_close_after_60m_target', 'simulation_only': True}
        append(VERIFY, result_row)
        results.append(result_row)
        finished.add(t)
    return results


def matched_comparison(v2_predictions, v2_verified):
    v1_preds = records(ROOT / 'cg_direction_v1_predictions.jsonl')
    v1_results = {int(x['prediction_time_ms']): x for x in records(ROOT / 'cg_direction_v1_verifications.jsonl')}
    matched = []
    for p in v2_predictions:
        verification = next((x for x in v2_verified if x['prediction_time_ms'] == p['prediction_time_ms']), None)
        if not verification:
            continue
        options = [x for x in v1_preds if abs(int(x['prediction_time_ms']) - int(p['prediction_time_ms'])) <= 120_000
                   and x.get('reference_price') is not None and p.get('reference_price') is not None
                   and abs(float(x['reference_price']) - float(p['reference_price'])) <= .01]
        if len(options) != 1:
            continue
        other = v1_results.get(int(options[0]['prediction_time_ms']))
        if not other or int(other['verification_time_ms']) != int(verification['verification_time_ms']):
            continue
        matched.append((verification['result'], other['result']))
    both = [(a, b) for a, b in matched if a in ('HIT','MISS') and b in ('HIT','MISS')]
    return {'matched_same_price_endpoint': len(matched), 'both_directional': len(both),
            'both_hit': sum(a == 'HIT' and b == 'HIT' for a,b in both),
            'both_miss': sum(a == 'MISS' and b == 'MISS' for a,b in both),
            'v2_only_hit': sum(a == 'HIT' and b == 'MISS' for a,b in both),
            'v1_only_hit': sum(a == 'MISS' and b == 'HIT' for a,b in both)}


def run(now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if not ACTIVATION.exists():
        save(ACTIVATION, {'model': VERSION, 'activated_at_ms': now, 'activated_at_utc': utc(now),
                          'mode': 'prospective_shadow', 'predeclared_thresholds_uncalibrated': True,
                          'telegram_enabled': False, 'trade_execution': False})
    activated = int(json_file(ACTIVATION.name)['activated_at_ms'])
    if now < activated:
        raise ValueError('Clock before V2 activation')
    history = records(PRED)
    new_prediction = None
    if not any(int(x['prediction_time_ms']) // HOUR == now // HOUR for x in history):
        new_prediction = prediction(now)
        append(PRED, new_prediction)
        save(LATEST, new_prediction)
        history.append(new_prediction)
    done = records(VERIFY)
    bar_data = json_file('okx_candles.json')
    bars = confirmed_m15(bar_data, now)
    verified_now = verify(history, done, bars, now)
    all_results = done + verified_now
    hits = sum(x['result'] == 'HIT' for x in all_results)
    misses = sum(x['result'] == 'MISS' for x in all_results)
    summary = {'model': VERSION, 'evaluated_at_utc': utc(now), 'predictions_total': len(history),
               'new_predictions': int(new_prediction is not None), 'verified_total': len(all_results),
               'new_verifications': len(verified_now), 'pending': len(history) - len(all_results),
               'hits': hits, 'misses': misses, 'flats': sum(x['result'] == 'FLAT' for x in all_results),
               'abstains': sum(x['result'] == 'ABSTAIN' for x in all_results),
               'unverifiable': sum(x['result'] == 'UNVERIFIABLE' for x in all_results),
               'direction_hit_rate_pct': round(100 * hits / (hits + misses), 2) if hits + misses else None,
               'comparison_with_cg_v1': matched_comparison(history, all_results),
               'warning': 'Research only. Rolling 4h taker and unverified source timestamps; no exact 60m endpoint, fills, fees or PnL.'}
    save(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    run()
