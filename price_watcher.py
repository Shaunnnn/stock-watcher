"""
Price side of the stock watcher.

Fetches the latest price for every ticker in watchlist.json, appends it to
price_log.json (so you build up a history over time), and prints an alert
for any ticker that moved more than ALERT_THRESHOLD_PCT since the previous
close.

Run this whenever you want a fresh snapshot, or wire it to a cron job /
scheduled task to run every N minutes:

    python3 price_watcher.py
"""
import json
import os
from datetime import datetime, timezone

import yfinance as yf

WATCHLIST_FILE = "watchlist.json"
PRICE_LOG_FILE = "price_log.json"
ALERT_THRESHOLD_PCT = 3.0  # flag moves bigger than this
MAX_HISTORY_PER_TICKER = 500  # price_log.json would otherwise grow forever


def load_watchlist():
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_price_log():
    if not os.path.exists(PRICE_LOG_FILE):
        return []
    with open(PRICE_LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def prune_log(log, max_per_ticker=MAX_HISTORY_PER_TICKER):
    """Pure function: keeps only the most recent max_per_ticker snapshots
    for each ticker, oldest-first order preserved within each ticker's
    remaining entries. Without this, price_log.json grows without bound
    since save_price_log only ever appends."""
    by_ticker = {}
    for snapshot in log:
        by_ticker.setdefault(snapshot["ticker"], []).append(snapshot)

    trimmed = []
    for ticker_snapshots in by_ticker.values():
        trimmed.extend(ticker_snapshots[-max_per_ticker:])

    # keep chronological order in the file rather than grouped-by-ticker
    trimmed.sort(key=lambda s: s.get("timestamp", ""))
    return trimmed


def save_price_log(log):
    log = prune_log(log)
    with open(PRICE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def compute_pct_change(price, prev_close):
    """Pure function so this math can be unit tested without hitting the
    network. Returns the percent change from prev_close to price."""
    if prev_close in (None, 0):
        return 0.0
    return round((price - prev_close) / prev_close * 100, 2)


def fetch_snapshot(ticker):
    """Pull the latest price + previous close for one ticker via yfinance."""
    t = yf.Ticker(ticker)
    info = t.fast_info  # lightweight, avoids the full slow .info call
    price = float(info["last_price"])
    prev_close = float(info["previous_close"])
    return {
        "ticker": ticker,
        "price": round(price, 2),
        "prev_close": round(prev_close, 2),
        "pct_change": compute_pct_change(price, prev_close),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def format_alert(snapshot):
    direction = "up" if snapshot["pct_change"] >= 0 else "down"
    return (
        f"ALERT: {snapshot['ticker']} is {direction} "
        f"{abs(snapshot['pct_change'])}% "
        f"(${snapshot['prev_close']} -> ${snapshot['price']})"
    )


def main():
    watchlist = load_watchlist()
    log = load_price_log()
    alerts = []

    for entry in watchlist:
        ticker = entry["ticker"]
        try:
            snapshot = fetch_snapshot(ticker)
        except Exception as e:
            print(f"  Could not fetch {ticker}: {e}")
            continue

        log.append(snapshot)
        print(f"{ticker}: ${snapshot['price']} ({snapshot['pct_change']:+.2f}%)")

        if abs(snapshot["pct_change"]) >= ALERT_THRESHOLD_PCT:
            alerts.append(snapshot)

    save_price_log(log)

    if alerts:
        print("\n" + "\n".join(format_alert(a) for a in alerts))
    else:
        print(f"\nNo moves over {ALERT_THRESHOLD_PCT}% today.")


if __name__ == "__main__":
    main()
