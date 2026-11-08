import json
import urllib.request
from datetime import datetime, timezone

url = "https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT-SWAP"

request = urllib.request.Request(
    url,
    headers={"User-Agent": "Mozilla/5.0"}
)

with urllib.request.urlopen(request, timeout=15) as response:
    result = json.load(response)

if result.get("code") != "0" or not result.get("data"):
    raise RuntimeError("OKX API returned invalid data")

ticker = result["data"][0]

if ticker.get("instId") != "ETH-USDT-SWAP":
    raise RuntimeError("Unexpected contract")

data = {
    "exchange": "OKX",
    "symbol": "ETH-USDT-SWAP",
    "price": ticker["last"],
    "market_time": int(ticker["ts"]),
    "collected_at": datetime.now(
        timezone.utc
    ).isoformat()
}

with open("okx_latest.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

print("OKX price updated:", data)
