"""Observer Ladder V1-R2 shadow precision gate, forward-only.

Consumes immutable V1-R predictions and their subsequent verifications.
Does not modify V1/V1-R, market data, other models, or Telegram.
No trades and no claim of net profitability.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

MODEL = 'observer_v1_r2_precision_gate_v0'
SOURCE = 'observer_v1_r_shadow_v0'
HORIZON = 15
WATCH_THRESHOLD = 0.00050
SIGNAL_THRESHOLD = 0.00075
MAX_FORWARD_READ_LAG_MS = 25 * 60_000


def load_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def append_jsonl(path, rows):
    if rows:
        with Path(path).open('a', encoding='utf-8') as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n')


def decide(v1r_prediction):
    """Prespecified V1-R 15m score-only filter. No ex post data."""
    if v1r_prediction['model'] != SOURCE or v1r_prediction['market'] != 'OKX ETH-USDT-SWAP':
        raise ValueError('Unrecognized V1-R source/market')
    prediction_time = int(v1r_prediction['prediction_time_ms'])
    f = v1r_prediction['features']
    if not int(f['quote_time_ms']) <= prediction_time:
        raise ValueError('Quote timestamp later than prediction')
    if not int(f['latest_closed_bar_end_ms']) <= prediction_time:
        raise ValueError('Bar closed after prediction time')
    horizons = [p for p in v1r_prediction['predictions'] if p['horizon_minutes'] == HORIZON]
    if len(horizons) != 1:
        raise ValueError('Expected one 15min forecast')
    original = horizons[0]
    if int(original['target_time_ms']) != prediction_time + HORIZON * 60_000:
        raise ValueError('Unexpected prediction target time')
    score = float(original['uncalibrated_score'])
    strength = abs(score)
    if strength >= SIGNAL_THRESHOLD and original['direction'] in ('UP', 'DOWN'):
        decision = 'SIGNAL'
        direction = original['direction']
    elif strength >= WATCH_THRESHOLD:
        decision = 'WATCH'
        direction = 'UNCERTAIN'
    else:
        decision = 'ABSTAIN'
        direction = 'UNCERTAIN'
    return {
        'model': MODEL,
        'source_model': SOURCE,
        'market': v1r_prediction['market'],
        'prediction_time_ms': prediction_time,
        'target_time_ms': int(original['target_time_ms']),
        'reference_price': float(v1r_prediction['reference_price']),
        'horizon_minutes': HORIZON,
        'source_direction': original['direction'],
        'direction': direction,
        'decision': decision,
        'score_at_prediction': score,
        'volume_ratio_context_only': f.get('volume_ratio'),
        'context_only': f.get('context'),
        'simulation_only': True,
        'live_order_allowed': False,
        'rules_version': 'v0_score_abs_ge_0.00075',
    }


def validate_source_verification(rec, prediction):
    if int(rec['prediction_time_ms']) != int(prediction['prediction_time_ms']):
        raise ValueError('Wrong source verification id')
    if int(rec['horizon_minutes']) != HORIZON:
        raise ValueError('Wrong horizon')
    if int(rec['target_time_ms']) != int(prediction['target_time_ms']):
        raise ValueError('Target mismatch')
    if abs(float(rec['reference_price']) - float(prediction['reference_price'])) > 0.000001:
        raise ValueError('Reference mismatch')
    if rec['result'] not in ('HIT', 'MISS', 'FLAT', 'ABSTAIN'):
        raise ValueError('Unknown original result')
    if rec['direction'] != prediction['source_direction']:
        raise ValueError('Original direction mismatch')


def run(base='.', now_ms=None):
    base = Path(base)
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    activation_path = base/'v1_r2_activation.json'
    if not activation_path.exists():
        activation = {
            'model': MODEL,
            'activated_at_ms': now_ms,
            'activated_at_utc': datetime.fromtimestamp(now_ms/1000, timezone.utc).isoformat(),
            'policy': 'Do not retrospectively create predictions on activation; forward only.',
        }
        activation_path.write_text(json.dumps(activation, indent=2)+'\n', encoding='utf-8')
    activation = json.loads(activation_path.read_text(encoding='utf-8'))
    activation_ms = int(activation['activated_at_ms'])
    prediction_path = base/'v1_r2_predictions.jsonl'
    verification_path = base/'v1_r2_verifications.jsonl'
    source_predictions = load_jsonl(base/'v1_r_predictions.jsonl')
    recorded = load_jsonl(prediction_path)
    existing_ids = {int(p['prediction_time_ms']) for p in recorded}
    # Only record source predictions that postdate the activation AND are still
    # recent at capture time. Do not retrospectively claim old signals.
    fresh = []
    for source in source_predictions:
        t = int(source['prediction_time_ms'])
        if t < activation_ms or t in existing_ids or t > now_ms:
            continue
        if now_ms - t > MAX_FORWARD_READ_LAG_MS:
            continue
        row = decide(source)
        row['recorded_at_ms'] = now_ms
        fresh.append(row)
        existing_ids.add(t)
    append_jsonl(prediction_path, fresh)
    recorded += fresh

    verified = load_jsonl(verification_path)
    verified_ids = {int(x['prediction_time_ms']) for x in verified}
    originals = {
        int(x['prediction_time_ms']): x
        for x in load_jsonl(base/'v1_r_verifications.jsonl')
        if int(x['horizon_minutes']) == HORIZON
    }
    new_results = []
    for row in recorded:
        tid = int(row['prediction_time_ms'])
        if tid in verified_ids or tid not in originals:
            continue
        origin = originals[tid]
        validate_source_verification(origin, row)
        source_result = origin['result']
        result = source_result if row['decision'] == 'SIGNAL' else 'ABSTAIN'
        new_results.append({
            'model': MODEL,
            'prediction_time_ms': tid,
            'target_time_ms': row['target_time_ms'],
            'horizon_minutes': HORIZON,
            'decision': row['decision'],
            'direction': row['direction'],
            'source_direction': row['source_direction'],
            'source_result': source_result,
            'result': result,
            'reference_price': row['reference_price'],
            'verification_price': origin.get('verification_price'),
            'verification_time_ms': origin.get('verification_time_ms'),
            'verification_lag_minutes': origin.get('verification_lag_minutes'),
            'verified_at_ms': now_ms,
            'simulation_only': True,
        })
        verified_ids.add(tid)
    append_jsonl(verification_path, new_results)
    verified += new_results

    # Compare on *identical selected opportunities*. Never count abstentions as HIT.
    selected = [r for r in verified if r['decision'] == 'SIGNAL']
    h = sum(r['result'] == 'HIT' for r in selected)
    m = sum(r['result'] == 'MISS' for r in selected)
    flats = sum(r['result'] == 'FLAT' for r in selected)
    summary = {
        'model': MODEL,
        'generated_at_utc': datetime.fromtimestamp(now_ms/1000, timezone.utc).isoformat(),
        'activated_at_utc': activation['activated_at_utc'],
        'opportunities_recorded': len(recorded),
        'predictions_selected': sum(r['decision']=='SIGNAL' for r in recorded),
        'watch': sum(r['decision']=='WATCH' for r in recorded),
        'abstains': sum(r['decision']=='ABSTAIN' for r in recorded),
        'signal_coverage_pct': round(100*sum(r['decision']=='SIGNAL' for r in recorded)/len(recorded),2) if recorded else None,
        'verified_selected_hit': h,
        'verified_selected_miss': m,
        'verified_selected_flat': flats,
        'directional_hit_rate_pct': round(100*h/(h+m),2) if h+m else None,
        'source_v1r_on_same_selected_opportunities_hit': sum(r['source_result']=='HIT' for r in selected),
        'source_v1r_on_same_selected_opportunities_miss': sum(r['source_result']=='MISS' for r in selected),
        'selected_pending': sum(r['decision']=='SIGNAL' for r in recorded) - len(selected),
        'new_predictions': len(fresh),
        'new_verifications': len(new_results),
        'warning': 'Selection only: selected direction equals V1-R direction. This design cannot improve conditional accuracy versus V1-R on identical selected predictions; compare selected accuracy to V1-R full coverage and signal coverage. No net PnL.'
    }
    (base/'v1_r2_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    return summary


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=2))
