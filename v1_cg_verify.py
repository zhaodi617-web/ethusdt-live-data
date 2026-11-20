"""V1-CG independent 60m endpoint verification. No edits to original forecasts."""
import json
import time
from pathlib import Path

BASE = Path('.')
PRED = BASE / 'v1_cg_predictions.jsonl'
OUT = BASE / 'v1_cg_verifications.jsonl'
SUMMARY = BASE / 'v1_cg_summary.json'
CANDLES = BASE / 'okx_candles.json'
M15 = 900000
FLAT = 0.0005


def lines(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def verify(predictions, prior, candle_data, now):
    if candle_data.get('exchange') != 'OKX' or candle_data.get('symbol') != 'ETH-USDT-SWAP':
        raise ValueError('Market mismatch')
    closed = sorted((x for x in candle_data['candles_15m'] if x.get('confirmed') is True),
                    key=lambda x: int(x['time']))
    done = {int(v['prediction_time']) for v in prior}
    added = []
    for p in predictions:
        ts = int(p['prediction_time'])
        target = int(p['target_time'])
        if ts in done or target > now:
            continue
        if p.get('model') != 'observer_v1_cg_shadow_v1' or target != ts + 3600000:
            raise ValueError('Unexpected model or horizon')
        # Same endpoint policy as original V1: first confirmed 15m bar closing at or after target.
        eligible = [b for b in closed if target <= int(b['time']) + M15 < target + M15
                    and int(b['time']) + M15 <= now]
        if not eligible:
            continue
        bar = min(eligible, key=lambda x: int(x['time']) + M15)
        endpoint = int(bar['time']) + M15
        ref = float(p['reference_price'])
        if ref <= 0:
            raise ValueError('Nonpositive reference')
        price = float(bar['close'])
        change = price / ref - 1
        if abs(change) <= FLAT:
            market = 'FLAT'
        else:
            market = 'UP' if change > 0 else 'DOWN'
        original = p['original_v1_signal']
        if original not in ('EARLY_UP', 'EARLY_DOWN', 'UNCERTAIN'):
            raise ValueError('Unknown original signal')
        baseline = ('ABSTAIN' if original == 'UNCERTAIN' else 'FLAT' if market == 'FLAT'
                    else 'HIT' if (original == 'EARLY_UP') == (market == 'UP') else 'MISS')
        decision = p['cg_decision']
        if decision == 'FILTER':
            cg_result = 'FILTERED_BASELINE_' + baseline
        elif decision in ('EARLY_UP', 'EARLY_DOWN'):
            cg_result = 'FLAT' if market == 'FLAT' else 'HIT' if (decision == 'EARLY_UP') == (market == 'UP') else 'MISS'
        elif decision in ('NO_CG_DECISION', 'ABSTAIN', 'UNCERTAIN'):
            cg_result = 'ABSTAIN'
        else:
            raise ValueError('Unknown CG decision')
        added.append({
            'model':'observer_v1_cg_shadow_v1', 'prediction_time':ts,
            'target_time':target,'reference_price':ref,'verification_price':price,
            'verification_time':endpoint,'verification_lag_minutes':round((endpoint-target)/60000,4),
            'return_pct':round(100*change,5),'market_direction':market,
            'original_v1_signal':original,'baseline_result':baseline,
            'cg_decision':decision,'cg_result':cg_result,
            'decision_reason':p['decision_reason'],
            'verification_method':'first_confirmed_15m_close_after_target',
            'simulation_only':True,'verified_at_ms':now,
        })
        done.add(ts)
    return added


def summarize(predictions, verified):
    outcome = [v for v in verified if v['baseline_result'] in ('HIT','MISS')]
    filtered = [v for v in outcome if v['cg_decision'] == 'FILTER']
    retained = [v for v in outcome if v['cg_decision'] in ('EARLY_UP','EARLY_DOWN')]
    def stats(rows):
        h=sum(x['baseline_result']=='HIT' for x in rows)
        m=sum(x['baseline_result']=='MISS' for x in rows)
        return {'sample':h+m,'baseline_hits':h,'baseline_misses':m,
                'baseline_hit_rate_pct':round(100*h/(h+m),2) if h+m else None}
    return {'model':'observer_v1_cg_shadow_v1','predictions_total':len(predictions),
            'verified_total':len(verified),'pending_or_unverified':len(predictions)-len(verified),
            'filtered':stats(filtered),'retained':stats(retained),
            'no_cg_decision_count':sum(x['cg_decision']=='NO_CG_DECISION' for x in verified),
            'flats':sum(x['market_direction']=='FLAT' for x in verified),
            'warning':'Directional endpoints only, not live fills/PnL. CoinGlass exchange data timestamps unverified. Compare matched samples and coverage.'}


def run(base=BASE, now_ms=None):
    global BASE
    base=Path(base)
    now=int(time.time()*1000) if now_ms is None else int(now_ms)
    predictions=lines(base/'v1_cg_predictions.jsonl')
    old=lines(base/'v1_cg_verifications.jsonl')
    data=json.loads((base/'okx_candles.json').read_text(encoding='utf-8'))
    new=verify(predictions,old,data,now)
    if new:
        with (base/'v1_cg_verifications.jsonl').open('a',encoding='utf-8') as f:
            for item in new:
                f.write(json.dumps(item,sort_keys=True)+'\n')
    summary=summarize(predictions,old+new)
    (base/'v1_cg_summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'new_verifications':len(new), **summary},indent=2))
    return summary


if __name__=='__main__':
    run()
