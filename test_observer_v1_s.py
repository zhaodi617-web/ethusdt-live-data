import json
import tempfile
import unittest
from pathlib import Path
from observer_v1_s import decide, verify, run, M15, HOUR

BASE=1_800_000_000_000

def fixtures(now=BASE, short=0.003, bg=0.012, weak=False):
    # Market data is synthetic, for code testing only.
    latest_start=(now//M15-1)*M15
    bars=[]
    for i in range(15):
        start=latest_start-(14-i)*M15
        close=2000.0
        if i==12: close=2000
        if i==14: close=2000*(1+short)
        bars.append({'time':start,'close':str(close),'volume_usdt':'10' if weak and i>=13 else '100','confirmed':True})
    latest_hour=(now//HOUR-1)*HOUR
    hours=[]
    for i in range(12):
        start=latest_hour-(11-i)*HOUR
        close=2000.0*(1+bg) if i>=8 else 2000.0
        hours.append({'time':start,'close':str(close),'confirmed':True})
    candle={'exchange':'OKX','symbol':'ETH-USDT-SWAP','candles_15m':bars,'candles_1h':hours}
    quote={'exchange':'OKX','symbol':'ETH-USDT-SWAP','price':'2005','market_time':now-1000}
    return candle,quote

class StructuralShadowTests(unittest.TestCase):
    def test_source_timestamp_and_nonoverlap(self):
        c,q=fixtures()
        p=decide(c,q,BASE)
        f=p['features']
        self.assertLessEqual(f['background_last_end_ms'],f['short_leg_start_ms'])
        self.assertLessEqual(f['last_confirmed_bar_end_ms'],BASE)
        self.assertEqual(p['target_time_ms'],BASE+HOUR)
        q['market_time']=BASE+1
        with self.assertRaises(ValueError):decide(c,q,BASE)
        c,q=fixtures();c['candles_15m'][-1]['time']=BASE
        with self.assertRaises(ValueError):decide(c,q,BASE)

    def test_agree_disagree_and_weak_volume(self):
        c,q=fixtures()
        self.assertEqual(decide(c,q,BASE)['decision'],'SIGNAL')
        c,q=fixtures(weak=True)
        self.assertEqual(decide(c,q,BASE)['decision'],'WATCH')
        c,q=fixtures(short=-.003,bg=.012)
        self.assertEqual(decide(c,q,BASE)['decision'],'ABSTAIN')
        c,q=fixtures(short=.0001,bg=.012)
        self.assertEqual(decide(c,q,BASE)['decision'],'ABSTAIN')

    def test_no_activation_backfill_or_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,q=fixtures()
            (p/'okx_candles.json').write_text(json.dumps(c))
            (p/'okx_latest.json').write_text(json.dumps(q))
            self.assertEqual(run(p,BASE)['opportunities'],0)
            self.assertEqual(run(p,BASE)['opportunities'],0)
            now=BASE+HOUR
            c,q=fixtures(now=now)
            (p/'okx_candles.json').write_text(json.dumps(c))
            (p/'okx_latest.json').write_text(json.dumps(q))
            self.assertEqual(run(p,now)['opportunities'],1)
            self.assertEqual(run(p,now)['opportunities'],1)

    def test_future_verification_only_and_flat(self):
        c,q=fixtures()
        p=decide(c,q,BASE)
        self.assertEqual(verify([p],[],c,BASE),[])
        t=BASE+HOUR+M15
        # Synthesize a full bar that ends >= target; must not be used until end.
        c2,_=fixtures(now=t)
        self.assertEqual(len(verify([p],[],c2,t)),1)
        result=verify([p],[],c2,t)[0]
        self.assertGreaterEqual(result['verification_time_ms'],p['target_time_ms'])
        self.assertLess(result['verification_lag_minutes'],15)
        self.assertEqual(result['result'],'FLAT') if abs(result['return_pct'])<=0.05 else None

if __name__=='__main__':unittest.main()
