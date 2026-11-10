import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
import observer_v1_plus as m


def pred(hour=13,minute=0,score=2,signal='EARLY_UP'):
    dt = datetime(2026,11,13,hour,minute,tzinfo=timezone.utc)
    return {'prediction_time':int(dt.timestamp()*1000), 'target_time':int(dt.timestamp()*1000)+3600000,
            'score':score,'signal':signal,'reference_price':2100}

EVT={'name':'Producer Price Index for October 2026','utc':'2026-11-13T13:30:00+00:00',
     'source':'https://www.bls.gov/schedule/2026/11_sched_list.htm','source_status':'fixed_official_schedule_backup'}

class V1PlusTest(unittest.TestCase):
    def test_crossing_downgrades_without_changing_v1(self):
        at = datetime(2026,11,13,13,5,tzinfo=timezone.utc)
        r=m.evaluate(pred(),[EVT],at)
        self.assertEqual(r['event_risk'],'PREDICTION_CROSSES_EVENT')
        self.assertEqual(r['v1_score_original'],2)
        self.assertEqual(r['v1_signal_original'],'EARLY_UP')
        self.assertEqual(r['confidence_label'],'EVENT_RISK_DOWNGRADED')
    def test_event_not_in_horizon(self):
        at = datetime(2026,11,13,11,40,tzinfo=timezone.utc)
        r=m.evaluate(pred(hour=11),[EVT],at)
        self.assertEqual(r['event_risk'],'UPCOMING_WITHIN_120_MIN')
    def test_after_event_does_not_presume_release_results(self):
        at = datetime(2026,11,13,13,40,tzinfo=timezone.utc)
        r=m.evaluate(pred(),[EVT],at)
        self.assertEqual(r['event_risk'],'NONE')
        self.assertFalse(r['event_result_known'])
    def test_stale_prediction_rejected(self):
        at = datetime(2026,11,13,14,30,tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            m.evaluate(pred(hour=12),[EVT],at)
    def test_record_deduplicated(self):
        with TemporaryDirectory() as d:
            with patch.object(m,'HISTORY',Path(d)/'log.jsonl'):
                r=m.evaluate(pred(),[EVT],datetime(2026,11,13,13,5,tzinfo=timezone.utc))
                m.append_once(r);m.append_once(r)
                self.assertEqual(len(m.HISTORY.read_text().splitlines()),1)

if __name__=='__main__':
    unittest.main()
