import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import v3_shadow as v

class Tests(unittest.TestCase):
    def fixtures(self):
        # 30 minutes since the latest closed 1h bar, and 2 minutes after the latest 15m close.
        base=1_800_000_000_000
        m=[]
        for i in range(24):
            m.append({'time':base+i*900_000,'open':str(100+i*.1),'high':str(100+i*.1+.2),
             'low':str(100+i*.1-.2),'close':str(100+i*.1+.1),'volume_usdt':'200', 'confirmed':True})
        h=[]
        for i in range(8):
            h.append({'time':base+(i-3)*3_600_000,'close':str(99+i*.4),'confirmed':True})
        t=m[-1]['time']+900_000+120_000
        return {'exchange':'OKX','symbol':v.SYMBOL,'candles_15m':m,'candles_1h':h},{'exchange':'OKX','symbol':v.SYMBOL,'price':'103','market_time':t-1000},t
    def test_nonoverlap_is_real(self):
        candles,ticker,now=self.fixtures()
        d=v.assess(candles,ticker,now)
        self.assertIn(d['status'],('PAPER_LONG_WATCH','NO_QUALIFIED_LONG_WATCH'))
        self.assertIsNone(d['fill_price'])
        self.assertFalse(d['trade_opened'])
    def test_stale_rejected(self):
        c,t,n=self.fixtures()
        with self.assertRaises(ValueError):v.assess(c,t,n+15*60000)
    def test_candle_archive_dedup(self):
        c,_,_=self.fixtures()
        with TemporaryDirectory() as td:
            p=Path(td)
            self.assertEqual(v.archive_candles(c,p),{'15m':24,'1h':8})
            self.assertEqual(v.archive_candles(c,p),{'15m':0,'1h':0})
            c['candles_15m'][1]['close']='9000'
            with self.assertRaises(ValueError):v.archive_candles(c,p)
    def test_no_history_leakage(self):
        c,t,n=self.fixtures()
        d=v.assess(c,t,n)
        self.assertIsNone(d['net_pnl'])
        self.assertEqual(d['validation_status'],'NOT_FILLED_NO_FUTURE_PATH_YET')
        self.assertEqual(d['paper_trigger_threshold'],round(d['structure_high_before_signal'],2))

if __name__=='__main__': unittest.main()
