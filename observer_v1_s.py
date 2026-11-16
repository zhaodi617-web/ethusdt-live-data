"""Observer Ladder V1-S: independent, forward-only 60-minute shadow challenger.

Pre-registered v0 hypothesis; NOT a trade signal or a profit claim.
Does not import/modify V1, V1-R, or V1-R2 modules or their files.
Historical predictions from before activation are never backfilled.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

MODEL = 'observer_v1_s_structural_shadow_v0'
MARKET = 'OKX ETH-USDT-SWAP'
M15 = 900_000
HOUR = 3_600_000
FLAT = .0005
SHORT_MIN = .0005
BACKGROUND_MIN = .0015
VOLUME_MIN = .8
MAX_QUOTE_AGE = 10 * 60_000
MAX_15M_AGE = 20 * 60_000


def load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def jsonl(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []


def append(path, rows):
    if rows:
        with Path(path).open('a', encoding='utf-8') as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def confirmed(data, label, period, now):
    if data.get('exchange') != 'OKX' or data.get('symbol') != 'ETH-USDT-SWAP':
        raise ValueError('Wrong candle market')
    rows = sorted((r for r in data[label] if r.get('confirmed') is True), key=lambda x: int(x['time']))
    if any(int(r['time']) + period > now for r in rows):
        raise ValueError('Future confirmed candle in input')
    return rows


def decide(candles, quote, now):
    if quote.get('exchange') != 'OKX' or quote.get('symbol') != 'ETH-USDT-SWAP':
        raise ValueError('Wrong quote market')
    qts = int(quote['market_time'])
    if not 0 <= now-qts <= MAX_QUOTE_AGE:
        raise ValueError('Stale/future quote')
    bars = confirmed(candles, 'candles_15m', M15, now)
    hours = confirmed(candles, 'candles_1h', HOUR, now)
    if len(bars) < 10:
        raise ValueError('Insufficient 15m bars')
    latest = bars[-1]
    last_end = int(latest['time'])+M15
    if not 0 <= now-last_end <= MAX_15M_AGE:
        raise ValueError('Stale confirmed 15m bar')
    # 30-minute short leg: latest two completed 15m candles.
    short = float(latest['close']) / float(bars[-3]['close']) - 1
    # Background finishes BEFORE most recent two 15m candles begin.
    short_start = int(bars[-2]['time'])
    prior = [h for h in hours if int(h['time']) + HOUR <= short_start]
    if len(prior) < 4:
        raise ValueError('Insufficient non-overlapping hourly history')
    # 3-hour background, sampled from completed hours only.
    bg = float(prior[-1]['close']) / float(prior[-4]['close']) - 1
    vol_recent = sum(float(r['volume_usdt']) for r in bars[-2:])
    vol_prior = sum(float(r['volume_usdt']) for r in bars[-10:-2]) / 4
    vol = vol_recent/vol_prior if vol_prior > 0 else None
    sign = lambda x, threshold: 1 if x >= threshold else -1 if x <= -threshold else 0
    s = sign(short, SHORT_MIN)
    b = sign(bg, BACKGROUND_MIN)
    if not s or not b:
        state, direction, reason = 'ABSTAIN','UNCERTAIN','MOMENTUM_TOO_WEAK'
    elif s != b:
        state, direction, reason = 'ABSTAIN','UNCERTAIN','NONOVERLAP_TRENDS_DISAGREE'
    elif vol is None or vol < VOLUME_MIN:
        state, direction, reason = 'WATCH','UNCERTAIN','WEAK_OR_MISSING_VOLUME'
    else:
        state, direction, reason = 'SIGNAL','UP' if s>0 else 'DOWN','INDEPENDENT_TRENDS_AND_VOLUME_AGREE'
    price = float(quote['price'])
    if price <= 0:
        raise ValueError('Invalid quote price')
    return {
        'model': MODEL, 'rules_version':'v0.0.1_predeclared',
        'market': MARKET, 'prediction_time_ms':now,
        'target_time_ms':now+HOUR, 'reference_price':price,
        'decision':state,'direction':direction,'reason':reason,
        'features':{'short_30m':round(short,9),'nonoverlap_background_3h':round(bg,9),
                    'volume_ratio':round(vol,6) if vol is not None else None,
                    'short_leg_start_ms':short_start,
                    'background_last_end_ms':int(prior[-1]['time'])+HOUR,
                    'last_confirmed_bar_end_ms':last_end,
                    'quote_time_ms':qts},
        'simulation_only':True,'live_order_allowed':False,
    }


def verify(rows, verified, candles, now):
    bars = confirmed(candles,'candles_15m',M15,now)
    done={int(x['prediction_time_ms']) for x in verified}
    out=[]
    for p in rows:
        t=int(p['prediction_time_ms']); target=int(p['target_time_ms'])
        if t in done or target>now:
            continue
        # Same frozen legacy endpoint policy as V1: first confirmed 15m
        # close at/after target, with exact measured lag.
        chosen=[b for b in bars if target<=int(b['time'])+M15<target+M15]
        if not chosen:
            continue
        b=min(chosen,key=lambda x:int(x['time']))
        end=int(b['time'])+M15
        ret=float(b['close'])/float(p['reference_price'])-1
        if abs(ret)<=FLAT: result='FLAT'
        elif p['decision']!='SIGNAL': result='ABSTAIN'
        elif (ret>0)==(p['direction']=='UP'): result='HIT'
        else: result='MISS'
        out.append({'model':MODEL,'prediction_time_ms':t,'target_time_ms':target,
                    'decision':p['decision'],'direction':p['direction'],
                    'reference_price':p['reference_price'],
                    'verification_price':float(b['close']),'verification_time_ms':end,
                    'verification_lag_minutes':round((end-target)/60_000,5),
                    'return_pct':round(ret*100,5),'result':result,
                    'verification_method':'first_confirmed_15m_close_after_target',
                    'simulation_only':True})
        done.add(t)
    return out


def run(base='.',now_ms=None):
    base=Path(base)
    now=int(time.time()*1000) if now_ms is None else int(now_ms)
    activation_path=base/'v1_s_activation.json'
    first=not activation_path.exists()
    if first:
        activation={'model':MODEL,'activated_at_ms':now,'activated_at_utc':utc(now),
                    'policy':'Activation run writes zero forecasts; next UTC hour begins prospective collection.'}
        activation_path.write_text(json.dumps(activation,indent=2)+'\n',encoding='utf-8')
    activation=load(activation_path)
    predictions=jsonl(base/'v1_s_predictions.jsonl')
    prior_verifications=jsonl(base/'v1_s_verifications.jsonl')
    candles=load(base/'okx_candles.json')
    new=[]
    if not first and now//HOUR > int(activation['activated_at_ms'])//HOUR:
        if not any(int(x['prediction_time_ms'])//HOUR == now//HOUR for x in predictions):
            new=[decide(candles,load(base/'okx_latest.json'),now)]
            new[0]['activation_at_ms']=int(activation['activated_at_ms'])
            new[0]['recorded_at_ms']=now
            # Write before any verification; no retroactive decision.
            append(base/'v1_s_predictions.jsonl',new)
    all_predictions=predictions+new
    vnew=verify(all_predictions,prior_verifications,candles,now) if candles else []
    append(base/'v1_s_verifications.jsonl',vnew)
    verified=prior_verifications+vnew
    signals=[x for x in all_predictions if x['decision']=='SIGNAL']
    signal_results=[x for x in verified if x['decision']=='SIGNAL']
    hit=sum(x['result']=='HIT' for x in signal_results)
    miss=sum(x['result']=='MISS' for x in signal_results)
    summary={'model':MODEL,'activated_at_utc':activation['activated_at_utc'],
             'generated_at_utc':utc(now),'opportunities':len(all_predictions),
             'signals':len(signals),'watch':sum(x['decision']=='WATCH' for x in all_predictions),
             'abstains':sum(x['decision']=='ABSTAIN' for x in all_predictions),
             'signal_coverage_pct':round(100*len(signals)/len(all_predictions),2) if all_predictions else None,
             'hit':hit,'miss':miss,'flat':sum(x['result']=='FLAT' for x in signal_results),
             'hit_rate_pct':round(100*hit/(hit+miss),2) if hit+miss else None,
             'pending_signals':len(signals)-len(signal_results),
             'new_predictions':len(new),'new_verifications':len(vnew),
             'warning':'Legacy 15m endpoint after 60m target; delay recorded. No exact 1m endpoint, no fills, no PnL.'}
    (base/'v1_s_summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary


if __name__=='__main__':
    print(json.dumps(run(),indent=2))
