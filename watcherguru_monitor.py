import json
import re
import urllib.request
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

URL = "https://t.me/s/WatcherGuru"
STATE = Path("watcherguru_seen.json")
OUTPUT = Path("watcherguru_news.json")

KEYWORDS = (
    "nvidia", "jensen huang", "nvda",
    "bitcoin", "ethereum", "btc", "eth",
    "federal reserve", "interest rate",
    "inflation", "sec", "etf",
    "nasdaq", "stock market",
    "liquidation", "tariff",
    "binance", "tether", "stablecoin",
    "hack", "hacked"
)


class TelegramParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.posts = []
        self.current = None
        self.capture = False
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")

        if "tgme_widget_message_wrap" in cls:
            self.current = {"id": "", "text": ""}
            self.depth = 1
            return

        if self.current is None:
            return

        if tag == "div":
            self.depth += 1

        if "tgme_widget_message_text" in cls:
            self.capture = True

        if a.get("data-post", "").startswith("WatcherGuru/"):
            self.current["id"] = a["data-post"]

        if tag == "br" and self.capture:
            self.current["text"] += "\n"

    def handle_endtag(self, tag):
        if self.current is None:
            return

        if tag == "div":
            self.depth -= 1
            if self.depth <= 0:
                if self.current["id"]:
                    self.posts.append(self.current)
                self.current = None
                self.capture = False

    def handle_data(self, data):
        if self.capture and self.current is not None:
            self.current["text"] += data


def main():
    req = urllib.request.Request(
        URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        html = response.read().decode("utf-8", "replace")

    parser = TelegramParser()
    parser.feed(html)

    posts = parser.posts

    if not posts:
        raise RuntimeError(
            "No Telegram posts found; page may have changed"
        )

    previous = set()
    if STATE.exists():
        previous = set(json.loads(STATE.read_text()))

    current_ids = [p["id"] for p in posts]

    # First run creates a baseline without replaying old news.
    first_run = not STATE.exists()

    matches = []
    for post in posts:
        if first_run or post["id"] in previous:
            continue

        content = unescape(post["text"]).strip()
        normalized = re.sub(r"\s+", " ", content).lower()

        if any(word in normalized for word in KEYWORDS):
            matches.append({
                "id": post["id"],
                "text": content[:1200],
                "url": "https://t.me/" + post["id"],
                "source": "WatcherGuru",
                "verification": "UNVERIFIED",
                "market_direction": "UNKNOWN"
            })

    output = {
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": URL,
        "posts_found": len(posts),
        "new_relevant_news": matches,
        "status": "OK"
    }

    OUTPUT.write_text(
        json.dumps(output, ensure_ascii=False, indent=2)
    )

    STATE.write_text(
        json.dumps(
            list(dict.fromkeys(
                list(previous) + current_ids
            ))[-1000:],
            ensure_ascii=False,
            indent=2
        )
    )

    print(
        "WatcherGuru checked:",
        len(posts),
        "posts;",
        len(matches),
        "new relevant items"
    )


if __name__ == "__main__":
    main()
