import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import observer_v1_r as v


class Tests(unittest.TestCase):
    def fixture(self):
        base=1_800_000_000_000
        m=[]
        for i in range(25):
            t=base+i*v.M15
            price=100+i*.10
            m.append({'time':t,'open':str(price),'close':str(price+.10),'high':str(price+.18),'low':str(price-.18),'volume_usdt':'200','confirmed':True})
        h=[]
        for i in range(10):
            t=base+(i-7)*v.HOUR
            h.append({'time':t,'close':str(98+i*.4),'confirmed':True})
        now=m[-1]['time']+v.M15+120_000
        return ({'exchange':'OKX','symbol':v.SYMBOL,'candles_15m':m,'candles_1h':h},
                {'exchange':'OKX','symbol':v.SYMBOL,'market_time':now-1000,'price':'102.6'},now)

    def test_four_horizons(self):
        c,t,n=self.fixture()
        a=v.make_prediction(c,t,n)
        self.assertEqual([x['horizon_minutes'] for x in a['predictions']],[15,30,45,60])
        self.assertFalse(a['live_order_allowed'])

    def test_nonoverlapping_hourly_background(self):
        c,t,n=self.fixture()
        f=v.evaluate_features(c,t,n)
        self.assertLessEqual(f['hourly_background_last_end_ms'],c['candles_15m'][-1]['time'])
        self.assertNotEqual(f['short_15m'],f['historical_nonoverlap_3h'])

    def test_stale_quote_refused(self):
        c,t,n=self.fixture()
        t['market_time']=n-11*60_000
        with self.assertRaises(ValueError):v.make_prediction(c,t,n)

    def test_no_future_candle_in_verification(self):
        c,t,n=self.fixture()
        prediction=v.make_prediction(c,t,n)
        # No confirmed future candle exists for the new prediction.
        self.assertEqual(v.verify([prediction],[],c,n+2*v.HOUR),[])

    def test_verification_first_close_after_target(self):
        c,t,n=self.fixture()
        prediction=v.make_prediction(c,t,n)
        candle_end=n+20*60_000
        start=candle_end-v.M15
        c['candles_15m'].append({'time':start,'open':'102.6','close':'103.0','high':'103.1','low':'102.5','confirmed':True})
        out=v.verify([prediction],[],c,n+31*60_000)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['horizon_minutes'],15)
        self.assertEqual(out[0]['verification_time_ms'],candle_end)
        self.assertEqual(v.verify([prediction],out,c,n+31*60_000),[])

    def test_hourly_dedup_and_unmodified_v1(self):
        c,t,n=self.fixture()
        with TemporaryDirectory() as td:
            base=Path(td)
            (base/'okx_candles.json').write_text(json.dumps(c))
            (base/'okx_latest.json').write_text(json.dumps(t))
            original=base/'observer_early_latest.json'
            original.write_text('DO NOT TOUCH')
            first=v.run(base,n)
            second=v.run(base,n+10_000)
            self.assertEqual(first['new_predictions'],1)
            self.assertEqual(second['new_predictions'],0)
            self.assertEqual(original.read_text(),'DO NOT TOUCH')
            self.assertEqual(len(v.read_lines(base/'v1_r_predictions.jsonl')),1)


if __name__=='__main__': unittest.main()
