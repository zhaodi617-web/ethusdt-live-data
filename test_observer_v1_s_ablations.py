import json
import tempfile
import unittest
from pathlib import Path
from observer_v1_s_ablations import classify,run,VARIANTS,HOUR

def features(short=.002,bg=.006,volume=1.2):
    return {'features':{'short_30m':short,'nonoverlap_background_3h':bg,'volume_ratio':volume}}

class Ablations(unittest.TestCase):
    def test_one_changed_gate_per_variant(self):
        p=features()
        for variant in VARIANTS:
            self.assertEqual(classify(p,variant)[:2],('SIGNAL','UP'))
        p=features(short=.002,bg=-.006)
        self.assertEqual(classify(p,'S-A')[:2],('SIGNAL','UP'))
        self.assertEqual(classify(p,'S-B')[0],'ABSTAIN')
        self.assertEqual(classify(p,'S-C')[0],'ABSTAIN')
        p=features(volume=.5)
        self.assertEqual(classify(p,'S-A')[0],'WATCH')
        self.assertEqual(classify(p,'S-B')[0],'SIGNAL')
        self.assertEqual(classify(p,'S-C')[0],'WATCH')
        p=features(short=.0004,bg=.0012)
        self.assertEqual(classify(p,'S-A')[0],'ABSTAIN')
        self.assertEqual(classify(p,'S-B')[0],'ABSTAIN')
        self.assertEqual(classify(p,'S-C')[:2],('SIGNAL','UP'))
    def test_activation_has_no_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=run(tmp,1_800_000_000_000)
            self.assertEqual(r['new_predictions'],0)
            self.assertEqual(sum(v['opportunities'] for v in r['variants'].values()),0)
    def test_all_branches_same_time_but_independent_verification(self):
        # Source verification indexes by prediction_time, so it must be called
        # separately for each variant; a single combined call loses results.
        import inspect
        source=inspect.getsource(run)
        self.assertIn('verify(subset,prev,candles,now)',source)

if __name__=='__main__':unittest.main()
