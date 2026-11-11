import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from v3_alert_verify import run


def iso(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.path=Path(self.tmp.name)
        self.sent=[]
        self.T=1800000000000
        obs={'status':'PAPER_LONG_WATCH','exchange':'OKX','symbol':'ETH-USDT-SWAP',
             'simulation_only':True,'observation_id':'abc','observed_at_utc':iso(self.T),
             'reference_closed_15m_end_utc':iso(self.T-30_000),
             'ticker_snapshot':100.0,'paper_trigger_threshold':101.0,
             'structure_low_before_signal':99.0,'volume_ratio':1.4}
        (self.path/'v3_shadow_latest.json').write_text(json.dumps(obs))
        (self.path/'v3_shadow_history.jsonl').write_text(json.dumps(obs)+'\n')
    def tearDown(self): self.tmp.cleanup()
    def tick(self, ms, price):
        (self.path/'okx_latest.json').write_text(json.dumps({'exchange':'OKX','symbol':'ETH-USDT-SWAP',
          'market_time':ms,'price':str(price)}))
        return run(self.path,now_ms=ms+500,sender=self.sent.append)
    def test_alert_once_and_trigger_later(self):
        a=self.tick(self.T+60000,100.3)
        self.assertEqual(a['new_alerts'],['abc'])
        self.assertEqual(len(self.sent),1)
        self.tick(self.T+120000,100.5)
        self.assertEqual(len(self.sent),1)
        self.tick(self.T+180000,101.2)
        self.assertEqual(len(self.sent),2)
        state=json.loads((self.path/'v3_alert_state.json').read_text())
        self.assertEqual(state['candidates']['abc']['status'],'TRIGGER_OBSERVED')
        self.assertIsNone(state['candidates']['abc']['net_pnl'])
        self.tick(self.T+240000,101.4)
        self.assertEqual(len(self.sent),2)
    def test_reject_old_candidate(self):
        a=self.tick(self.T+21*60000,100.2)
        self.assertEqual(a['new_alerts'],[])
    def test_missing_secrets_fails_without_false_dedup(self):
        (self.path/'okx_latest.json').write_text(json.dumps({'exchange':'OKX','symbol':'ETH-USDT-SWAP',
           'market_time':self.T+60000,'price':'100.2'}))
        def fail(_): raise RuntimeError('Telegram down')
        with self.assertRaises(RuntimeError):run(self.path,now_ms=self.T+60001,sender=fail)
        self.assertFalse((self.path/'v3_alert_state.json').exists())
    def test_stale_quote_refused(self):
        (self.path/'okx_latest.json').write_text(json.dumps({'exchange':'OKX','symbol':'ETH-USDT-SWAP',
           'market_time':self.T,'price':'100.2'}))
        with self.assertRaises(ValueError):run(self.path,now_ms=self.T+11*60000,sender=self.sent.append)
    def test_candidate_already_above_threshold_does_not_imply_fill(self):
        obs=json.loads((self.path/'v3_shadow_latest.json').read_text());obs['ticker_snapshot']=102.0
        (self.path/'v3_shadow_latest.json').write_text(json.dumps(obs))
        (self.path/'v3_shadow_history.jsonl').write_text(json.dumps(obs)+'\n')
        self.tick(self.T+60000,102.5)
        self.assertEqual(len(self.sent),1)
        self.tick(self.T+120000,102.6)
        self.assertEqual(len(self.sent),1)
        self.tick(self.T+180000,100.4)
        self.tick(self.T+240000,101.1)
        self.assertEqual(len(self.sent),2)
if __name__=='__main__':unittest.main()
