import json
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.okx.com/api/v5/market/candles"
SYMBOL = "ETH-USDT-SWAP"

def fetch_candles(bar):
    url = f"{BASE}?instId={SYMBOL}&bar={bar}&limit=100"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(req, timeout=20) as r:
        result = json.load(r)

    if result.get("code") != "0":
        raise RuntimeError(f"OKX error: {result}")

    candles = []
    for row in result["data"]:
        if len(row) < 9:
            raise RuntimeError("Unexpected candle format")
        candles.append({
            "time": int(row[0]),
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume_contracts": row[5],
            "volume_eth": row[6],
            "volume_usdt": row[7],
            "confirmed": row[8] == "1"
        })

    return sorted(candles, key=lambda x: x["time"])

data = {
    "exchange": "OKX",
    "symbol": SYMBOL,
    "collected_at": datetime.now(
        timezone.utc
    ).isoformat(),
    "candles_15m": fetch_candles("15m"),
    "candles_1h": fetch_candles("1H")
}

with open("okx_candles.json", "w") as f:
    json.dump(data, f, indent=2)

print("OKX candle data saved")
