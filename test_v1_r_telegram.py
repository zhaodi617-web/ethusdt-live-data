import unittest
from v1_r_telegram import evaluate, HOUR_MS

T = 1794376800000 + 120000

def fixtures(v1='EARLY_UP', seq=('UP', 'UP', 'UP', 'DOWN')):
    a={'model':'observer_early_v1','exchange':'OKX','symbol':'ETH-USDT-SWAP','prediction_time':T,'signal':v1}
    b={'model':'observer_v1_r_shadow_v0','market':'OKX ETH-USDT-SWAP','prediction_time_ms':T+60000,
       'predictions':[{'horizon_minutes':m,'direction':x} for m,x in zip((15,30,45,60),seq)]}
    return a,b

class Checks(unittest.TestCase):
    def test_opposing_v1_v1r_60m(self):
        a,b=fixtures('EARLY_UP',('DOWN','DOWN','DOWN','DOWN'))
        self.assertEqual(evaluate(a,b,T+120000)[0],'V1_V1R_60M_OPPOSITE')
    def test_short_long_opposition(self):
        a,b=fixtures('EARLY_UP',('UP','UP','UNCERTAIN','DOWN'))
        self.assertEqual(evaluate(a,b,T+120000)[0],'V1_V1R_60M_OPPOSITE')
        a,b=fixtures('UNCERTAIN',('UP','UP','UNCERTAIN','DOWN'))
        self.assertEqual(evaluate(a,b,T+120000)[0],'V1R_SHORT_LONG_OPPOSITE')
    def test_same_direction_silent(self):
        a,b=fixtures('EARLY_UP',('UP','UP','UP','UP'))
        self.assertIsNone(evaluate(a,b,T+120000))
    def test_weak_divergence_silent(self):
        a,b=fixtures('UNCERTAIN',('UP','DOWN','DOWN','DOWN'))
        self.assertIsNone(evaluate(a,b,T+120000))
    def test_different_hours_silent(self):
        a,b=fixtures()
        b['prediction_time_ms']+=HOUR_MS
        self.assertIsNone(evaluate(a,b,T+HOUR_MS+120000))
    def test_old_prediction_silent(self):
        a,b=fixtures()
        self.assertIsNone(evaluate(a,b,T+91*60000+60000))
    def test_wrong_market_silent(self):
        a,b=fixtures()
        b['market']='BINANCE ETHUSDT'
        self.assertIsNone(evaluate(a,b,T+120000))
    def test_chinese_and_per_hour_id(self):
        a,b=fixtures('EARLY_DOWN',('UP','UP','UP','UP'))
        k,msg,h=evaluate(a,b,T+120000)
        self.assertIn('重要分歧',msg)
        self.assertIn('非买入或平仓指令',msg)
        self.assertEqual(h,str(T//HOUR_MS))

if __name__ == '__main__':
    unittest.main()
