import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import v1_path_verify as p

T0 = 1800000000000 + 52000
T1 = T0 + 3600000

def fixture(bars, signal='EARLY_UP'):
    return {'prediction_time':T0,'target_time':T1,'reference_price':100,'signal':signal}, {'result':'MISS','return_pct':-0.2}, bars

def bar(start, hi, lo):
    return {'time':start, 'high':str(hi), 'low':str(lo), 'confirmed':True}

class PathTests(unittest.TestCase):
    def test_target_interior_but_boundary_uncertain(self):
        start = ((T0 + p.BAR_MS - 1) // p.BAR_MS) * p.BAR_MS
        q,v,b=fixture([bar(start+i*p.BAR_MS,100.5 if i==1 else 100.1,99.95) for i in range(3)])
        x=p.evaluate(q,v,b)
        self.assertEqual(x['first_observed_touch'],'TARGET_FIRST_IN_INTERIOR_BARS')
        self.assertFalse(x['true_first_touch_confirmed'])
        self.assertEqual(x['v1_original_result'],'MISS')
        self.assertIsNone(x['net_pnl'])
    def test_stop_first(self):
        start=((T0+p.BAR_MS-1)//p.BAR_MS)*p.BAR_MS
        q,v,b=fixture([bar(start+i*p.BAR_MS,100.5 if i==2 else 100.1,99.6 if i==0 else 99.9) for i in range(3)])
        self.assertEqual(p.evaluate(q,v,b)['first_observed_touch'],'STOP_FIRST_IN_INTERIOR_BARS')
    def test_both_same_bar_unknown(self):
        start=((T0+p.BAR_MS-1)//p.BAR_MS)*p.BAR_MS
        q,v,b=fixture([bar(start+i*p.BAR_MS,100.5,99.5) for i in range(3)])
        self.assertEqual(p.evaluate(q,v,b)['first_observed_touch'],'BOTH_IN_SAME_15M_BAR_ORDER_UNKNOWN')
    def test_missing_full_bars_is_not_complete(self):
        start=((T0+p.BAR_MS-1)//p.BAR_MS)*p.BAR_MS
        q,v,b=fixture([bar(start,100.5,99.8)])
        self.assertEqual(p.evaluate(q,v,b)['path_validity'],'MISSING_INTERIOR_BARS')
    def test_bear_direction(self):
        start=((T0+p.BAR_MS-1)//p.BAR_MS)*p.BAR_MS
        q,v,b=fixture([bar(start+i*p.BAR_MS,100.1,99.5 if i==0 else 99.9) for i in range(3)], 'EARLY_DOWN')
        self.assertEqual(p.evaluate(q,v,b)['first_observed_touch'],'TARGET_FIRST_IN_INTERIOR_BARS')
    def test_idempotent_and_original_untouched(self):
        start=((T0+p.BAR_MS-1)//p.BAR_MS)*p.BAR_MS
        q,v,b=fixture([bar(start+i*p.BAR_MS,100.5 if i==0 else 100.1,99.9) for i in range(3)])
        with TemporaryDirectory() as td:
            d=Path(td)
            (d/p.PREDICTIONS).write_text(json.dumps(q)+'\n')
            (d/p.VERIFICATIONS).write_text(json.dumps({'prediction_time':q['prediction_time'], **v})+'\n')
            (d/p.ARCHIVE).write_text(''.join(json.dumps(z)+'\n' for z in b))
            self.assertEqual(p.run(d)['new_evaluations'],1)
            self.assertEqual(p.run(d)['new_evaluations'],0)
            self.assertEqual(len(p.rows(d/p.RESULTS)),1)
            self.assertEqual(p.rows(d/p.VERIFICATIONS)[0]['result'],'MISS')

if __name__=='__main__': unittest.main()
