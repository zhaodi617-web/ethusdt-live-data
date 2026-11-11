"""Observer Ladder V3 shadow research. No orders, alerts, or V1/V2 mutation.

Run after the existing OKX data collection. Uses only confirmed candles and
snapshots, and records forward-only paper trigger conditions.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

SYMBOL = 'ETH-USDT-SWAP'
BASE = Path('.')

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def rows(path):
    p = Path(path)
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()] if p.exists() else []

def append_jsonl(path, item):
    with Path(path).open('a', encoding='utf-8') as f:
        f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + '\n')

def candle_end(bar, minutes):
    return int(bar['time']) + minutes * 60000

def confirmed(data, name):
    return sorted((x for x in data[name] if x.get('confirmed') is True), key=lambda x: int(x['time']))

def archive_candles(data, base=BASE):
    """Append-only archive. Never overwrite historical bars or invent missing ones."""
    counts = {}
    for tf, label in [('candles_15m', '15m'), ('candles_1h', '1h')]:
        path = base / f'v3_archive_{label}.jsonl'
        stored = rows(path)
        seen = {int(x['time']): x for x in stored}
        additions = 0
        for bar in confirmed(data, tf):
            key = int(bar['time'])
            if key in seen:
                # Treat corrected historical data as an explicit anomaly; never silently change it.
                if any(str(bar.get(k)) != str(seen[key].get(k)) for k in ('open','high','low','close','volume_usdt')):
                    raise ValueError(f'Historical candle changed: {label} at {key}')
                continue
            append_jsonl(path, bar)
            additions += 1
        counts[label] = additions
    return counts

def assess(candles, ticker, now_ms, max_lag_ms=10*60000):
    if ticker.get('exchange') != 'OKX' or ticker.get('symbol') != SYMBOL:
        raise ValueError('Unsupported or mismatched market')
    market_ms = int(ticker['market_time'])
    if not 0 <= now_ms - market_ms <= max_lag_ms:
        raise ValueError('Ticker is stale or future-dated')
    m15 = confirmed(candles, 'candles_15m')
    h1 = confirmed(candles, 'candles_1h')
    if len(m15) < 10 or len(h1) < 6:
        raise ValueError('Insufficient confirmed bars')
    last = m15[-1]
    if not 0 <= now_ms - candle_end(last, 15) <= 20*60000:
        raise ValueError('Confirmed 15m bar is stale or future-dated')
    # Independent historical background: exclude all 1h bars overlapping the
    # latest 15m candle, and measure three FULL prior one-hour intervals.
    prior_hour = [b for b in h1 if candle_end(b, 60) <= int(last['time'])]
    if len(prior_hour) < 4:
        raise ValueError('Insufficient non-overlapping hourly background')
    h_background = float(prior_hour[-1]['close']) / float(prior_hour[-4]['close']) - 1
    m15_return = float(last['close']) / float(m15[-2]['close']) - 1
    previous = m15[-9:-1]
    volume_base = sum(float(b['volume_usdt']) for b in previous) / 8
    volume_ratio = float(last['volume_usdt'])/volume_base if volume_base>0 else None
    prior_high = max(float(b['high']) for b in previous)
    prior_low = min(float(b['low']) for b in previous)
    breakout_up = float(last['close'])>prior_high
    near_breakout = float(last['close'])>=prior_high*0.9975
    # These are PRE-REGISTERED HYPOTHESES, not validated winning thresholds.
    upward_structure = breakout_up or near_breakout
    momentum = m15_return > 0.0005 and h_background > 0.001
    vol = volume_ratio is not None and volume_ratio>=1.2
    setup = 'PAPER_LONG_WATCH' if upward_structure and momentum and vol else 'NO_QUALIFIED_LONG_WATCH'
    return {
        'model':'observer_v3_shadow_research_v0',
        'observed_at_utc':datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat(),
        'observation_id':str(int(last['time'])),
        'reference_closed_15m_end_utc':datetime.fromtimestamp(candle_end(last,15)/1000,timezone.utc).isoformat(),
        'exchange':'OKX','symbol':SYMBOL,'market_timestamp_ms':market_ms,
        'ticker_snapshot':float(ticker['price']),
        'short_momentum_15m':round(m15_return,7),
        'nonoverlap_h1_background_3h':round(h_background,7),
        'volume_ratio':round(volume_ratio,4) if volume_ratio is not None else None,
        'structure_high_before_signal':prior_high,
        'structure_low_before_signal':prior_low,
        'status':setup,
        'conditions':{'momentum':momentum,'volume':vol,'structure':upward_structure,'confirmed_breakout':breakout_up},
        'paper_trigger_rule':'After signal generated, a future observed quote must cross above prior structure high; historical candle close is NOT assumed to be an executable entry',
        'paper_trigger_threshold':round(prior_high,2),
        'fill_price':None,'trade_opened':False,'net_pnl':None,
        'validation_status':'NOT_FILLED_NO_FUTURE_PATH_YET',
        'assumptions':['thresholds unvalidated','no entry until future observation','no fee/slippage data yet','same-exchange comparison only'],
        'simulation_only':True,'live_order_allowed':False,
    }

def run(base=BASE, now_ms=None):
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp()*1000)
    candles=read_json(base/'okx_candles.json')
    ticker=read_json(base/'okx_latest.json')
    if candles.get('exchange')!='OKX' or candles.get('symbol')!=SYMBOL:
        raise ValueError('Candle source mismatch')
    obs=assess(candles,ticker,now_ms)
    # Only archive after validating freshness and market identity.
    obs['archive_new_candles']=archive_candles(candles,base)
    hist_path=base/'v3_shadow_history.jsonl'
    old=rows(hist_path)
    if not any(x['observation_id']==obs['observation_id'] for x in old):
        append_jsonl(hist_path,obs)
    (base/'v3_shadow_latest.json').write_text(json.dumps(obs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return obs

if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
