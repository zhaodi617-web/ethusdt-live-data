"""Observer Ladder V1 path audit (independent, paper-only).

Reads immutable V1 predictions/verifications and archived confirmed OKX 15m bars.
Never changes V1/V2/V3 output, sends no orders, and never retroactively relabels HIT/MISS.
For a prediction starting inside a 15m bar, full-bar high/low can include prices
before the prediction or after the target. We EXCLUDE partial-overlap bars, and
explicitly report that remaining boundary gaps prevent definitive first-touch
claims. A close on a 15m boundary is counted only when inside the window.
"""
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PREDICTIONS = 'observer_early_predictions.jsonl'
VERIFICATIONS = 'observer_verifications.jsonl'
ARCHIVE = 'v3_archive_15m.jsonl'
RESULTS = 'v1_path_results.jsonl'
SUMMARY = 'v1_path_summary.json'
TARGET_PCT = 0.3     # pre-registered research example; not validated thresholds
STOP_PCT = 0.3
BAR_MS = 15 * 60_000


def rows(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]


def evaluate(pred, ver, archive, target_pct=TARGET_PCT, stop_pct=STOP_PCT):
    """Return one separately labeled research evaluation, preserving V1 result."""
    t0 = int(pred['prediction_time'])
    t1 = int(pred['target_time'])
    p0 = float(pred['reference_price'])
    direction = pred['signal']
    if direction not in ('EARLY_UP', 'EARLY_DOWN') or t1 <= t0 or p0 <= 0:
        raise ValueError('Invalid directional V1 prediction')
    if not (0 < target_pct < 10 and 0 < stop_pct < 10):
        raise ValueError('Invalid experiment thresholds')
    if direction == 'EARLY_UP':
        favorable_level = p0 * (1 + target_pct / 100)
        adverse_level = p0 * (1 - stop_pct / 100)
    else:
        favorable_level = p0 * (1 - target_pct / 100)
        adverse_level = p0 * (1 + stop_pct / 100)
    # Entire candle must lie inside prediction interval. Any overlapping edge
    # candle is ambiguous from 15m OHLC and cannot be used as proof of a touch.
    bars = sorted((b for b in archive if b.get('confirmed') is True
                   and int(b['time']) >= t0 and int(b['time']) + BAR_MS <= t1),
                  key=lambda b: int(b['time']))
    # Detect actual absent full bars, not simply the count.
    span = ((t0 + BAR_MS - 1) // BAR_MS) * BAR_MS
    full_starts = list(range(span, t1 - BAR_MS + 1, BAR_MS))
    seen = [int(b['time']) for b in bars]
    gaps = len(full_starts) != len(seen) or set(full_starts) != set(seen)
    max_price = max((float(b['high']) for b in bars), default=None)
    min_price = min((float(b['low']) for b in bars), default=None)
    mfe = None
    mae = None
    if bars:
        mfe = ((max_price / p0 - 1) * 100 if direction == 'EARLY_UP'
               else (1 - min_price / p0) * 100)
        mae = ((1 - min_price / p0) * 100 if direction == 'EARLY_UP'
               else (max_price / p0 - 1) * 100)
    observed = 'NONE_IN_FULL_BARS'
    first_bar = None
    for b in bars:
        hit_target = (float(b['high']) >= favorable_level if direction == 'EARLY_UP'
                      else float(b['low']) <= favorable_level)
        hit_stop = (float(b['low']) <= adverse_level if direction == 'EARLY_UP'
                    else float(b['high']) >= adverse_level)
        if hit_target or hit_stop:
            observed = ('BOTH_IN_SAME_15M_BAR_ORDER_UNKNOWN' if hit_target and hit_stop
                        else 'TARGET_FIRST_IN_INTERIOR_BARS' if hit_target
                        else 'STOP_FIRST_IN_INTERIOR_BARS')
            first_bar = int(b['time'])
            break
    # 60m prediction at arbitrary seconds always has partial overlapping bars.
    # Whether the edge gap saw an earlier stop/target is unknowable from OHLC.
    left_partial = t0 % BAR_MS != 0
    right_partial = t1 % BAR_MS != 0
    path_validity = ('MISSING_INTERIOR_BARS' if gaps
                     else 'ONLY_FULL_INTERIOR_BARS_BOUNDARY_CENSORED' if (left_partial or right_partial)
                     else 'FULL_15M_ALIGNED_WINDOW')
    # Verification comes from the original file; do not derive it from these candles.
    return {
        'prediction_id': str(t0), 'prediction_time_ms': t0, 'target_time_ms': t1,
        'signal': direction, 'reference_price': p0, 'v1_original_result': ver.get('result') if ver else None,
        'v1_original_return_pct': ver.get('return_pct') if ver else None,
        'hypothesis_target_pct': target_pct, 'hypothesis_stop_pct': stop_pct,
        'target_price': round(favorable_level, 4), 'stop_price': round(adverse_level, 4),
        'interior_full_15m_bars': len(bars), 'expected_interior_bars': len(full_starts),
        'path_validity': path_validity,
        'mfe_interior_pct': round(mfe, 5) if mfe is not None else None,
        'mae_interior_pct': round(mae, 5) if mae is not None else None,
        'max_interior_price': max_price, 'min_interior_price': min_price,
        'first_observed_touch': observed, 'first_observed_bar_start_ms': first_bar,
        'true_first_touch_confirmed': observed != 'BOTH_IN_SAME_15M_BAR_ORDER_UNKNOWN'
            and observed != 'NONE_IN_FULL_BARS' and path_validity == 'FULL_15M_ALIGNED_WINDOW',
        'paper_fill_confirmed': False, 'net_pnl': None,
        'warning': '15m OHLC excludes partial boundary bars and cannot establish real fills or net PnL'
    }


def run(base='.'):
    base = Path(base)
    preds = rows(base / PREDICTIONS)
    vers = {int(x['prediction_time']): x for x in rows(base / VERIFICATIONS)}
    archive = rows(base / ARCHIVE)
    if not preds or not archive:
        raise RuntimeError('Missing predictions or archived 15m candles')
    known = {str(x['prediction_id']) for x in rows(base / RESULTS)}
    new = []
    for p in preds:
        if p.get('signal') not in ('EARLY_UP', 'EARLY_DOWN'):
            continue
        pid = int(p['prediction_time'])
        if pid not in vers or str(pid) in known:
            continue
        x = evaluate(p, vers[pid], archive)
        # Do not freeze a result if bars are still missing. This also avoids
        # retrospective claims for old samples whose 15m archive was lost.
        if x['path_validity'] == 'MISSING_INTERIOR_BARS':
            continue
        new.append(x)
        known.add(str(pid))
    with (base / RESULTS).open('a', encoding='utf-8') as f:
        for x in new:
            f.write(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n')
    all_rows = rows(base / RESULTS)
    counts = Counter(x['first_observed_touch'] for x in all_rows)
    summary = {
        'model': 'observer_v1_path_audit_shadow_v0',
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'registered_target_pct': TARGET_PCT, 'registered_stop_pct': STOP_PCT,
        'saved_evaluations': len(all_rows), 'new_evaluations': len(new),
        'up_original_misses': sum(x['signal'] == 'EARLY_UP' and x['v1_original_result'] == 'MISS' for x in all_rows),
        'up_misses_with_interior_target_first': sum(x['signal']=='EARLY_UP' and x['v1_original_result']=='MISS'
             and x['first_observed_touch']=='TARGET_FIRST_IN_INTERIOR_BARS' for x in all_rows),
        'interior_touch_counts': dict(counts),
        'important_limit': 'First touch inside FULL 15m bars only. Partial boundary intervals are unobserved; no live fills, no full-window trading hit rate.'
    }
    (base / SUMMARY).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return summary


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=2))
