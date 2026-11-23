"""Version-aware forward-only verification for Observer Ladder V1.

Historic verification rows are never rewritten. Old rules keep their results;
new rule 1.1 results are identified by immutable prediction timestamp.
Other consumers retain the original top-level accuracy keys.
"""
import json
import time
from pathlib import Path

PREDICTIONS = Path('observer_early_predictions.jsonl')
CANDLES = Path('okx_candles.json')
RESULTS = Path('observer_verifications.jsonl')
ACCURACY = Path('observer_accuracy.json')
M15 = 900_000
HOUR = 3_600_000
FLAT = 0.0005
RULE = '1.1'


def read_lines(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def counts(rows):
    h = sum(x.get('result') == 'HIT' for x in rows)
    m = sum(x.get('result') == 'MISS' for x in rows)
    return {'hits': h, 'misses': m,
            'flats': sum(x.get('result') == 'FLAT' for x in rows),
            'abstains': sum(x.get('result') == 'ABSTAIN' for x in rows),
            'directional_samples': h + m,
            'hit_rate_pct': round(100 * h / (h + m), 2) if h + m else None}


def run(now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    preds = read_lines(PREDICTIONS)
    old = read_lines(RESULTS)
    finished = {int(r['prediction_time']) for r in old}
    data = json.loads(CANDLES.read_text(encoding='utf-8'))
    if data.get('exchange') != 'OKX' or data.get('symbol') != 'ETH-USDT-SWAP':
        raise RuntimeError('Wrong candle market')
    candles = sorted((x for x in data.get('candles_15m', [])
                      if x.get('confirmed') is True and int(x['time']) + M15 <= now),
                     key=lambda x: int(x['time']))
    added = []
    for p in preds:
        start, target = int(p['prediction_time']), int(p['target_time'])
        if start in finished or target > now:
            continue
        signal = p.get('signal')
        if signal == 'UNCERTAIN':
            row = {'model': p.get('model', 'observer_early_v1'),
                   'rule_version': p.get('rule_version', '1.0'),
                   'prediction_time': start, 'target_time': target,
                   'signal': signal, 'result': 'ABSTAIN', 'verified_at': now}
        elif signal in ('EARLY_UP', 'EARLY_DOWN'):
            possible = [c for c in candles
                        if target <= int(c['time']) + M15 < target + M15]
            if not possible:
                continue
            candle = min(possible, key=lambda c: int(c['time']))
            close_ms = int(candle['time']) + M15
            reference, actual = float(p['reference_price']), float(candle['close'])
            if reference <= 0:
                raise RuntimeError('Invalid prediction reference')
            change = actual / reference - 1
            result = ('FLAT' if abs(change) <= FLAT else
                      'HIT' if ((signal == 'EARLY_UP' and change > 0) or
                                (signal == 'EARLY_DOWN' and change < 0)) else 'MISS')
            row = {'model': p.get('model', 'observer_early_v1'),
                   'rule_version': p.get('rule_version', '1.0'),
                   'prediction_time': start, 'target_time': target,
                   'signal': signal, 'reference_price': reference,
                   'verification_price': actual, 'verification_time': close_ms,
                   'verification_method': 'first_confirmed_15m_close_after_target',
                   'return_pct': round(change * 100, 5), 'result': result,
                   'verified_at': now}
        else:
            continue
        added.append(row)
        finished.add(start)
    if added:
        with RESULTS.open('a', encoding='utf-8') as f:
            for row in added:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    all_results = old + added
    pred_by_t = {int(p['prediction_time']): p for p in preds}
    archived = [x for x in all_results if pred_by_t.get(int(x['prediction_time']), {}).get('rule_version', '1.0') != RULE]
    new_results = [x for x in all_results if pred_by_t.get(int(x['prediction_time']), {}).get('rule_version', '1.0') == RULE]
    old_preds = [p for p in preds if p.get('rule_version', '1.0') != RULE]
    new_preds = [p for p in preds if p.get('rule_version', '1.0') == RULE]
    overall = counts(all_results)
    acc = {'model': 'observer_early_v1', 'active_rule_version': RULE,
           'verified_directional_samples': overall['directional_samples'],
           'hits': overall['hits'], 'misses': overall['misses'],
           'flats': overall['flats'], 'abstains': overall['abstains'],
           'pending_or_unverified': len(preds) - len(all_results),
           'hit_rate_pct': overall['hit_rate_pct'],
           'history_by_rule_version': {
               '1.0_archived': {**counts(archived), 'predictions_total': len(old_preds),
                                'verified_total': len(archived),
                                'pending': len(old_preds) - len(archived)},
               '1.1_active': {**counts(new_results), 'predictions_total': len(new_preds),
                              'verified_total': len(new_results),
                              'pending': len(new_preds) - len(new_results)}},
           'updated_at_ms': now,
           'warning': 'Research only; next confirmed 15m close after 60m target. No live fills or net PnL.'}
    ACCURACY.write_text(json.dumps(acc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('New V1 verifications:', len(added))
    print('V1 rule 1.0:', acc['history_by_rule_version']['1.0_archived'])
    print('V1 rule 1.1:', acc['history_by_rule_version']['1.1_active'])
    return acc


if __name__ == '__main__':
    run()
