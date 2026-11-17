"""Forward-only V1-S ablation research; no trade orders and no legacy writes."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from observer_v1_s import decide, verify, HOUR, jsonl, load, append

VARIANTS = {'S-A': 'ALLOW_TREND_CONFLICT', 'S-B': 'IGNORE_VOLUME_GATE', 'S-C': 'RELAX_MOMENTUM_LIMITS'}
VERSION='v0.0.1_predeclared'
PREFIX='v1_s_ablation'

def classify(original, variant):
    if variant not in VARIANTS:
        raise ValueError('Unknown frozen variant')
    f=original['features']
    short=float(f['short_30m']); bg=float(f['nonoverlap_background_3h']); volume=f['volume_ratio']
    st,bt=(.0003,.0010) if variant=='S-C' else (.0005,.0015)
    def sign(x,threshold):
        return 1 if x>=threshold else -1 if x<=-threshold else 0
    s,b=sign(short,st),sign(bg,bt)
    if not s or not b:
        return 'ABSTAIN','UNCERTAIN','MOMENTUM_TOO_WEAK'
    if s!=b and variant!='S-A':
        return 'ABSTAIN','UNCERTAIN','NONOVERLAP_TRENDS_DISAGREE'
    if variant!='S-B' and (volume is None or volume<.8):
        return 'WATCH','UNCERTAIN','WEAK_OR_MISSING_VOLUME'
    return 'SIGNAL',('UP' if s>0 else 'DOWN'),('TREND_CONFLICT_ALLOWED' if s!=b else 'ALL_GATES_PASSED')

def run(base='.',now_ms=None):
    base=Path(base)
    now=int(time.time()*1000) if now_ms is None else int(now_ms)
    actpath=base/(PREFIX+'_activation.json')
    predpath=base/(PREFIX+'_predictions.jsonl')
    verpath=base/(PREFIX+'_verifications.jsonl')
    sumpath=base/(PREFIX+'_summary.json')
    first=not actpath.exists()
    if first:
        act={'activated_at_ms':now,'activated_at_utc':datetime.fromtimestamp(now/1000,timezone.utc).isoformat(),
             'version':VERSION,'variants':VARIANTS,
             'policy':'Activation creates no forecasts; first prospective predictions are next UTC hour; zero backfill.'}
        actpath.write_text(json.dumps(act,indent=2)+'\n',encoding='utf-8')
    act=load(actpath)
    predictions=jsonl(predpath)
    previous=jsonl(verpath)
    candles=load(base/'okx_candles.json')
    new=[]
    if not first and now//HOUR>int(act['activated_at_ms'])//HOUR:
        if not any(int(p['prediction_time_ms'])//HOUR==now//HOUR for p in predictions):
            original=decide(candles,load(base/'okx_latest.json'),now)
            for variant in VARIANTS:
                decision,direction,reason=classify(original,variant)
                p=dict(original)
                p.update(model='observer_v1_s_ablation_'+variant.lower().replace('-','_'),variant=variant,
                         decision=decision,direction=direction,reason=reason,rules_version=VERSION,
                         baseline_v1_s_decision=original['decision'],baseline_v1_s_direction=original['direction'],
                         activation_at_ms=int(act['activated_at_ms']),recorded_at_ms=now,
                         simulation_only=True,live_order_allowed=False)
                new.append(p)
            append(predpath,new)
    allp=predictions+new
    newv=[]
    if candles:
        for variant in VARIANTS:
            subset=[p for p in allp if p['variant']==variant]
            prev=[v for v in previous if v.get('variant')==variant]
            # V1-S verification is identical for each candidate; must call separately
            # because legacy verify() deduplicates using prediction_time only.
            verified=verify(subset,prev,candles,now)
            for v in verified:
                v['variant']=variant
                v['rules_version']=VERSION
            newv.extend(verified)
    append(verpath,newv)
    allv=previous+newv
    variants={}
    for variant in VARIANTS:
        p=[x for x in allp if x['variant']==variant]
        v=[x for x in allv if x.get('variant')==variant]
        signals=[x for x in p if x['decision']=='SIGNAL']
        signalver=[x for x in v if x['decision']=='SIGNAL']
        hit=sum(x['result']=='HIT' for x in signalver)
        miss=sum(x['result']=='MISS' for x in signalver)
        variants[variant]={'opportunities':len(p),'signals':len(signals),
            'watch':sum(x['decision']=='WATCH' for x in p),'abstains':sum(x['decision']=='ABSTAIN' for x in p),
            'hit':hit,'miss':miss,'flat':sum(x['result']=='FLAT' for x in signalver),
            'pending':len(signals)-len(signalver),
            'coverage_pct':round(100*len(signals)/len(p),2) if p else None,
            'direction_hit_rate_pct':round(100*hit/(hit+miss),2) if hit+miss else None}
    output={'generated_at_utc':datetime.fromtimestamp(now/1000,timezone.utc).isoformat(),
        'activated_at_utc':act['activated_at_utc'],'version':VERSION,
        'variants':variants,'new_predictions':len(new),'new_verifications':len(newv),
        'warning':'Research only. Uses legacy first confirmed 15m close >= 60m target; no fills or net PnL.'}
    sumpath.write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
    return output

if __name__=='__main__':
    print(json.dumps(run(),indent=2))
