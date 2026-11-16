import json
import tempfile
import unittest
from pathlib import Path
from observer_v1_r2 import decide, run


def sample(t=1_800_000_000_000, score=.0008, direction='UP'):
    return {'model':'observer_v1_r_shadow_v0','market':'OKX ETH-USDT-SWAP',
            'prediction_time_ms':t, 'reference_price':2000,
            'features':{'quote_time_ms':t-1000,'latest_closed_bar_end_ms':t-60_000,'volume_ratio':0.4,'context':'MIXED_OR_SIDEWAYS'},
            'predictions':[{'horizon_minutes':15,'target_time_ms':t+900_000,
                            'direction':direction,'uncalibrated_score':score}]}


class PrecisionGateTests(unittest.TestCase):
    def test_exact_thresholds(self):
        self.assertEqual(decide(sample(score=.00075))['decision'], 'SIGNAL')
        self.assertEqual(decide(sample(score=-.00075, direction='DOWN'))['direction'], 'DOWN')
        self.assertEqual(decide(sample(score=.00050))['decision'], 'WATCH')
        self.assertEqual(decide(sample(score=.00049))['decision'], 'ABSTAIN')
        self.assertEqual(decide(sample(score=.00070))['direction'], 'UNCERTAIN')

    def test_future_data_rejected(self):
        s=sample()
        s['features']['quote_time_ms']=s['prediction_time_ms']+1000
        with self.assertRaises(ValueError): decide(s)
        s=sample()
        s['features']['latest_closed_bar_end_ms']=s['prediction_time_ms']+1000
        with self.assertRaises(ValueError): decide(s)

    def test_activation_never_backfills(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            t=1_800_000_000_000
            (p/'v1_r_predictions.jsonl').write_text(json.dumps(sample(t-3600_000))+'\n')
            a=run(p,now_ms=t)
            self.assertEqual(a['opportunities_recorded'],0)
            (p/'v1_r_predictions.jsonl').write_text(json.dumps(sample(t-3600_000))+'\n'+json.dumps(sample(t+60_000))+'\n')
            b=run(p,now_ms=t+120_000)
            self.assertEqual(b['opportunities_recorded'],1)
            c=run(p,now_ms=t+120_000)
            self.assertEqual(c['opportunities_recorded'],1)

    def test_matching_verification_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder); t=1_800_000_000_000
            run(p,now_ms=t)
            (p/'v1_r_predictions.jsonl').write_text(json.dumps(sample(t+60000))+'\n')
            run(p,now_ms=t+120000)
            verification={'prediction_time_ms':t+60000,'target_time_ms':t+960000,
                          'horizon_minutes':15,'direction':'UP','reference_price':2000,
                          'result':'HIT','verification_price':2010,'verification_time_ms':t+1020000}
            (p/'v1_r_verifications.jsonl').write_text(json.dumps(verification)+'\n')
            s=run(p,now_ms=t+1020000)
            self.assertEqual((s['verified_selected_hit'],s['verified_selected_miss']),(1,0))
            self.assertEqual(run(p,now_ms=t+1020000)['verified_selected_hit'],1)


if __name__=='__main__': unittest.main()
