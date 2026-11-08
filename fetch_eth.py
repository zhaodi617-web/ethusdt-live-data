
import json
import urllib.error

import urllib.request
from datetime import datetime, timezone

url = "https://fapi.binance.com/fapi/v1/ticker/price?symbol=ETHUSDT"

request = urllib.request.Request(
    url,
    headers={"User-Agent": "ETHUSDT-Data-Collector/1.0"}
)

try:
    with urllib.request.urlopen(request, timeout=15) as response:
        ticker = json.load(response)
except urllib.error.HTTPError as error:
    if error.code == 451:
        raise SystemExit(
            "Binance API access restricted (HTTP 451). "
            "No new market data saved."
        )
    raise

data = {
    "symbol": ticker["symbol"],
    "price": ticker["price"],
    "time": int(datetime.now(timezone.utc).timestamp() * 1000)
}

with open("latest.json", "w", encoding="utf-8") as file:
    json.dump(data, file, ensure_ascii=False, indent=2)

print("ETHUSDT data updated:", data)
