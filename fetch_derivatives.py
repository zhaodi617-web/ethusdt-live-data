import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

BASE = "https://www.okx.com"
SYMBOL = "ETH-USDT-SWAP"

def fetch(path, params):
    query = urllib.parse.urlencode(params)
    url = BASE + path + "?" + query

    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(
        request, timeout=20
    ) as response:
        result = json.load(response)

    if result.get("code") != "0":
        raise RuntimeError(
            "OKX API error: " + str(result)
        )

    if not result.get("data"):
        raise RuntimeError("Empty market data")

    return result["data"][0]

oi = fetch("/api/v5/public/open-interest", {
    "instType": "SWAP",
    "instId": SYMBOL
})

funding = fetch("/api/v5/public/funding-rate", {
    "instId": SYMBOL
})

data = {
    "exchange": "OKX",
    "symbol": SYMBOL,
    "collected_at": datetime.now(
        timezone.utc
    ).isoformat(),
    "open_interest": {
        "contracts": oi.get("oi"),
        "base_currency": oi.get("oiCcy"),
        "usd_value": oi.get("oiUsd"),
        "timestamp": oi.get("ts")
    },
    "funding": {
        "rate": funding.get("fundingRate"),
        "next_rate": funding.get("nextFundingRate"),
        "funding_time": funding.get("fundingTime"),
        "next_funding_time": funding.get(
            "nextFundingTime"
        ),
        "timestamp": funding.get("ts")
    }
}

with open(
    "okx_derivatives.json", "w", encoding="utf-8"
) as file:
    json.dump(data, file, indent=2)

print("Derivatives data saved")
