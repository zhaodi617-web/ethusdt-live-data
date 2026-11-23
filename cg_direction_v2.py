"""CG-Direction V2: prospective, research-only direction experiment.

Rule 1.1 is a revision INSIDE the existing CG2 model. No new workflow,
Telegram notification, or live trading. Predictions/verifications from rule
1.0 are immutable and are reported separately from rule 1.1.

Rule 1.1 is a forward-only exploratory hypothesis. Thresholds are not tuned
or validated using old V2 outcomes. 4h CoinGlass ratios are NOT 5m flow.
"""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.')
VERSION = 'cg_direction_v2_shadow_1.1'
HOUR = 3_600_000
M15 = 900_000
PRED = ROOT / 'cg_direction_v2_predictions.jsonl'
VERIFY = ROOT / 'cg_direction_v2_verifications.jsonl'
LATEST = ROOT / 'cg_direction_v2_latest.json'
SUMMARY = ROOT / 'cg_direction_v2_summary.json'
ACTIVATION = ROOT / 'cg_direction_v2_activation.json'
MAX_CG_AGE = 20 * 60_000
MAX_QUOTE_AGE = 5 * 60_000


def json_file(name, default=None):
    path = ROOT / name
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def save(path, obj):
    path.write_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def append(path, obj):
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(obj, sort_keys=True, ensure_ascii=False) + '\n')


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def latest_asof(history, cutoff, max_age):
    eligible = [x for x in history if 0 <= cutoff - int(x['collected_at_ms']) <= max_age]
    return max(eligible, key=lambda x: int(x['collected_at_ms'])) if eligible else None


def taker_change_30m(history, at_ms, current):
    """Change over 30m in a ROLLING FOUR-HOUR buy share, not 30m taker flow."""
    if not current:
        return None, None
    desired = at_ms - 30 * 60_000
    matches = [x for x in history if abs(int(x['collected_at_ms']) - desired) <= 7 * 60_000
               and int(x['collected_at_ms']) < int(current['collected_at_ms'])]
    if not matches:
        return None, None
    older = min(matches, key=lambda x: abs(int(x['collected_at_ms']) - desired))
    now_buy = number(current.get('data', {}).get('All', {}).get('buy_pct'))
    old_buy = number(older.get('data', {}).get('All', {}).get('buy_pct'))
    if now_buy is None or old_buy is None:
        return None, None
    return round(now_buy - old_buy, 5), int(older['collected_at_ms'])


def confirmed_m15(candles, now):
    if not candles or candles.get('exchange') != 'OKX' or candles.get('symbol') != 'ETH-USDT-SWAP':
        return []
    return sorted((x for x in candles.get('candles_15m', [])
                   if x.get('confirmed') is True and int(x['time']) + M15 <= now),
                  key=lambda x: int(x['time']))


def score_from_features(taker, oi, drift, m15, now):
    """Predeclared 1.1 hypothesis: price leads, 4h taker ratio corroborates.

    Three overlapping taker ratios contribute at most ONE consensus vote.
    Price momentum confirmation and positive volume are required for any call.
    OI is directionless without price movement; decreasing OI is context only.
    All price features use confirmed candles available at prediction time.
    """
    votes = {'taker_consensus_4h': 0, 'taker_rolling4h_share_drift_30m': 0,
             'price_momentum_15m': 0, 'price_momentum_1h': 0,
             'confirmed_structure': 0, 'oi_price_confirmation': 0}
    if taker is None or oi is None or len(m15) < 10:
        return 0, votes, {}, 'MISSING_CG_OR_CONFIRMED_CANDLES'
    if not 0 <= now - (int(m15[-1]['time']) + M15) <= 20 * 60_000:
        return 0, votes, {}, 'STALE_CONFIRMED_CANDLE'
    times = [int(x['time']) for x in m15[-10:]]
    if any(b - a != M15 for a, b in zip(times, times[1:])):
        return 0, votes, {}, 'GAPPED_CONFIRMED_CANDLES'
    buys = [number(taker.get('data', {}).get(e, {}).get('buy_pct'))
            for e in ('All', 'Binance', 'OKX')]
    oi_change = number(oi.get('data', {}).get('All', {}).get('open_interest_change_percent_1h'))
    if any(v is None or not 0 <= v <= 100 for v in buys) or oi_change is None:
        return 0, votes, {}, 'INVALID_CG_INPUT'
    all_buy, binance_buy, okx_buy = buys
    closes = [number(x.get('close')) for x in m15[-10:]]
    if any(x is None or x <= 0 for x in closes):
        return 0, votes, {}, 'INVALID_CONFIRMED_PRICE'
    last = m15[-1]
    before = m15[-9:-1]
    prev_volumes = [number(x.get('volume_usdt')) for x in before]
    current_volume = number(last.get('volume_usdt'))
    highs = [number(x.get('high')) for x in before]
    lows = [number(x.get('low')) for x in before]
    if (any(v is None or v < 0 for v in prev_volumes) or
            current_volume is None or current_volume < 0 or
            any(v is None or v <= 0 for v in highs + lows)):
        return 0, votes, {}, 'INVALID_VOLUME_OR_STRUCTURE'
    avg_volume = sum(prev_volumes) / len(prev_volumes)
    if avg_volume <= 0:
        return 0, votes, {}, 'INVALID_VOLUME_BASELINE'
    momentum_15m = closes[-1] / closes[-2] - 1
    momentum_1h = closes[-1] / closes[-5] - 1
    vol_ratio = current_volume / avg_volume
    prev_high, prev_low = max(highs), min(lows)
    up_break = closes[-1] > prev_high
    down_break = closes[-1] < prev_low

    # Overlapping CoinGlass markets are one evidence family, not three votes.
    if all_buy >= 51.5 and binance_buy >= 50.5 and okx_buy >= 50.5:
        votes['taker_consensus_4h'] = 1
    elif all_buy <= 48.5 and binance_buy <= 49.5 and okx_buy <= 49.5:
        votes['taker_consensus_4h'] = -1
    if drift is not None and number(drift) is not None:
        votes['taker_rolling4h_share_drift_30m'] = 1 if drift >= .75 else -1 if drift <= -.75 else 0
    votes['price_momentum_15m'] = 1 if momentum_15m >= .001 else -1 if momentum_15m <= -.001 else 0
    votes['price_momentum_1h'] = 2 if momentum_1h >= .002 else -2 if momentum_1h <= -.002 else 0
    if vol_ratio >= 1.2:
        votes['confirmed_structure'] = 2 if up_break else -2 if down_break else 0
    if oi_change >= .4:
        votes['oi_price_confirmation'] = 1 if momentum_15m > .0005 else -1 if momentum_15m < -.0005 else 0
    score = sum(votes.values())
    divergence = ('BUY_PRESSURE_PRICE_DOWN' if all_buy >= 51.5 and momentum_1h < 0
                  else 'SELL_PRESSURE_PRICE_UP' if all_buy <= 48.5 and momentum_1h > 0
                  else 'NONE')
    features = {
        'all_taker_buy_pct': all_buy, 'binance_taker_buy_pct': binance_buy,
        'okx_taker_buy_pct': okx_buy, 'all_oi_change_1h_pct': oi_change,
        'taker_30m_buy_share_change_pp': drift,
        'taker_30m_change_is_rolling4h_share_not_30m_flow': True,
        'price_return_15m_pct': round(100 * momentum_15m, 5),
        'price_return_1h_pct': round(100 * momentum_1h, 5),
        'volume_ratio': round(vol_ratio, 4), 'prior_2h_high': prev_high,
        'prior_2h_low': prev_low, 'confirmed_breakout_up': up_break,
        'confirmed_breakout_down': down_break,
        'deleveraging_oi': oi_change <= -.5, 'price_taker_divergence': divergence,
    }
    # Quality gating is NOT evidence of superior performance: evaluate prospectively.
    active_volume = vol_ratio >= .9
    if (score >= 4 and votes['taker_consensus_4h'] > 0 and
            momentum_15m >= .001 and momentum_1h >= .002 and active_volume):
        verdict = 'CG_V2_UP'
    elif (score <= -4 and votes['taker_consensus_4h'] < 0 and
          momentum_15m <= -.001 and momentum_1h <= -.002 and active_volume):
        verdict = 'CG_V2_DOWN'
    else:
        verdict = 'UNCERTAIN'
    return score, votes, features, verdict


def prediction(now):
    ticker = json_file('okx_latest.json')
    candle_data = json_file('okx_candles.json')
    taker_hist = records(ROOT / 'coinglass_taker_history.jsonl')
    oi_hist = records(ROOT / 'coinglass_oi_history.jsonl')
    taker = latest_asof(taker_hist, now, MAX_CG_AGE)
    oi = latest_asof(oi_hist, now, MAX_CG_AGE)
    market_time = int(ticker['market_time']) if ticker and ticker.get('market_time') is not None else None
    quote = number(ticker.get('price')) if ticker else None
    quote_good = bool(ticker and ticker.get('exchange') == 'OKX'
                      and ticker.get('symbol') == 'ETH-USDT-SWAP'
                      and market_time is not None and 0 <= now - market_time <= MAX_QUOTE_AGE
                      and quote is not None and quote > 0)
    drift, baseline_ms = taker_change_30m(taker_hist, now, taker)
    bars = confirmed_m15(candle_data, now)
    score, votes, features, verdict = score_from_features(taker, oi, drift, bars, now)
    if not quote_good or taker is None or oi is None or not bars:
        verdict = 'MISSING_OR_STALE_INPUT'
    signal = verdict if verdict in ('CG_V2_UP', 'CG_V2_DOWN') else 'UNCERTAIN'
    return {
        'model': VERSION, 'rule_version': '1.1', 'prediction_time_ms': now,
        'prediction_time_utc': utc(now), 'target_time_ms': now + HOUR,
        'horizon_minutes': 60, 'signal': signal, 'score': score,
        'score_votes': votes, 'features': features, 'reason': verdict,
        'reference_price': quote if quote_good else None,
        'reference_market_time_ms': market_time if quote_good else None,
        'cg_taker_snapshot_ms': int(taker['collected_at_ms']) if taker else None,
        'cg_oi_snapshot_ms': int(oi['collected_at_ms']) if oi else None,
        'taker_baseline_30m_snapshot_ms': baseline_ms,
        'source_exchange_timestamps_verified': False,
        'predeclared_thresholds_uncalibrated': True,
        'simulation_only': True, 'live_order_allowed': False,
        'telegram_enabled': False,
    }


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
        reference = number(p.get('reference_price'))
        if reference is None or reference <= 0:
            result, change, direction = 'UNVERIFIABLE', None, 'UNKNOWN'
        else:
            change = float(bar['close']) / reference - 1
            direction = 'FLAT' if abs(change) <= .0005 else 'UP' if change > 0 else 'DOWN'
            if p['signal'] == 'UNCERTAIN':
                result = 'ABSTAIN'
            elif direction == 'FLAT':
                result = 'FLAT'
            elif (p['signal'] == 'CG_V2_UP' and direction == 'UP') or (p['signal'] == 'CG_V2_DOWN' and direction == 'DOWN'):
                result = 'HIT'
            else:
                result = 'MISS'
        row = {
            'model': p.get('model', 'cg_direction_v2_shadow_1.0'),
            'prediction_time_ms': t, 'target_time_ms': target,
            'verification_time_ms': close_ms, 'verified_at_ms': now,
            'verification_lag_minutes': round((close_ms - target) / 60_000, 4),
            'signal': p['signal'], 'reference_price': reference,
            'verification_price': float(bar['close']),
            'return_pct': round(100 * change, 5) if change is not None else None,
            'market_direction': direction, 'result': result,
            'method': 'first_confirmed_15m_close_after_60m_target',
            'simulation_only': True,
        }
        append(VERIFY, row)
        results.append(row)
        finished.add(t)
    return results


def matched_comparison(predictions, verified):
    v1_preds = records(ROOT / 'cg_direction_v1_predictions.jsonl')
    v1_results = {int(x['prediction_time_ms']): x for x in records(ROOT / 'cg_direction_v1_verifications.jsonl')}
    own_results = {int(x['prediction_time_ms']): x for x in verified}
    matched = []
    for p in predictions:
        verification = own_results.get(int(p['prediction_time_ms']))
        if verification is None:
            continue
        options = [x for x in v1_preds if abs(int(x['prediction_time_ms']) - int(p['prediction_time_ms'])) <= 120_000
                   and x.get('reference_price') is not None and p.get('reference_price') is not None
                   and abs(float(x['reference_price']) - float(p['reference_price'])) <= .01]
        if len(options) != 1:
            continue
        other = v1_results.get(int(options[0]['prediction_time_ms']))
        if other is None or int(other['verification_time_ms']) != int(verification['verification_time_ms']):
            continue
        matched.append((verification['result'], other['result']))
    both = [(a, b) for a, b in matched if a in ('HIT', 'MISS') and b in ('HIT', 'MISS')]
    return {
        'matched_same_price_endpoint': len(matched), 'both_directional': len(both),
        'both_hit': sum(a == 'HIT' and b == 'HIT' for a, b in both),
        'both_miss': sum(a == 'MISS' and b == 'MISS' for a, b in both),
        'v2_only_hit': sum(a == 'HIT' and b == 'MISS' for a, b in both),
        'v1_only_hit': sum(a == 'MISS' and b == 'HIT' for a, b in both),
    }


def stats(preds, verified):
    hits = sum(r['result'] == 'HIT' for r in verified)
    misses = sum(r['result'] == 'MISS' for r in verified)
    return {
        'predictions_total': len(preds), 'verified_total': len(verified),
        'pending': len(preds) - len(verified), 'hits': hits, 'misses': misses,
        'flats': sum(r['result'] == 'FLAT' for r in verified),
        'abstains': sum(r['result'] == 'ABSTAIN' for r in verified),
        'unverifiable': sum(r['result'] == 'UNVERIFIABLE' for r in verified),
        'direction_hit_rate_pct': round(100 * hits / (hits + misses), 2) if hits + misses else None,
    }


def run(now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if not ACTIVATION.exists():
        save(ACTIVATION, {
            'model': VERSION, 'activated_at_ms': now, 'activated_at_utc': utc(now),
            'mode': 'prospective_shadow', 'predeclared_thresholds_uncalibrated': True,
            'telegram_enabled': False, 'trade_execution': False,
        })
    activated = int(json_file(ACTIVATION.name)['activated_at_ms'])
    if now < activated:
        raise ValueError('Clock before V2 activation')
    history = records(PRED)
    new_prediction = None
    # Existing recorded hours remain immutable, including historical rule 1.0.
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
    current_preds = [p for p in history if p.get('model') == VERSION]
    current_timestamps = {int(p['prediction_time_ms']) for p in current_preds}
    current_results = [v for v in all_results if int(v['prediction_time_ms']) in current_timestamps]
    prior_preds = [p for p in history if p.get('model') != VERSION]
    prior_timestamps = {int(p['prediction_time_ms']) for p in prior_preds}
    prior_results = [v for v in all_results if int(v['prediction_time_ms']) in prior_timestamps]
    summary = {
        'model': VERSION, 'rule_version': '1.1', 'evaluated_at_utc': utc(now),
        **stats(current_preds, current_results),
        'new_predictions': int(new_prediction is not None),
        'new_verifications': len(verified_now),
        'all_versions_predictions_total': len(history),
        'all_versions_verified_total': len(all_results),
        'history_by_rule_version': {
            '1.0_archived_immutable': stats(prior_preds, prior_results),
            '1.1_active': stats(current_preds, current_results),
        },
        'comparison_with_cg_v1': matched_comparison(current_preds, current_results),
        'warning': ('Prospective research only. 4h rolling taker ratio, exchange source timestamps unverified. '
                    'Endpoint is next confirmed 15m close, not exact +60m. No fills, fees or net PnL. '
                    'Do not compare 1.1 and 1.0 without matching forecast times and coverage.'),
    }
    save(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return summary


if __name__ == '__main__':
    run()
