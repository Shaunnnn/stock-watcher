"""
Web front end for Stock Watcher. This is a thin Flask layer over the
exact same logic in price_watcher.py, rag_chat.py, news_fetch.py, and
filings_fetch.py — it doesn't reimplement any of the price fetching,
retrieval, or generation, it just calls those functions and renders the
results as JSON for the page's JS to draw.

Building the news/filing index (news_fetch.py -> filings_fetch.py ->
build_index.py) is still a CLI step, deliberately: those calls cost real
API requests and take a few seconds each, so they shouldn't silently fire
on every page load. Live PRICE refresh is cheap and instant, so that one
does happen from the page (the Refresh button, or an initial load).

A few things this file also does, deliberately, because this app reads
and writes shared JSON files from multiple requests:
  - Every read-modify-write on the data files is wrapped in a file lock
    (data_lock()), so two requests landing at nearly the same time can't
    silently clobber each other's changes.
  - /api/ask and /api/watchlist/add are rate-limited (in-memory, no extra
    dependency) since both cost real API calls — this is a guard against
    a stuck browser tab or an accidental double-click running up a bill,
    not a defense against a hostile user.
  - debug mode is OFF by default; set FLASK_DEBUG=1 for local development
    only. Never run with debug on anywhere reachable by anyone but you.
  - an optional password gate (APP_PASSWORD) is off by default and only
    matters if you ever deploy this somewhere other than your own
    machine — see the README before doing that.

Run:
    export OPENAI_API_KEY="your-key-here"
    python3 app.py
Then open http://127.0.0.1:5000
"""
import os
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from filelock import FileLock, Timeout
from flask import Flask, Response, jsonify, render_template, request

import build_index
import filings_fetch
import news_fetch
import price_watcher
import rag_chat
import storage

app = Flask(__name__)

# Locally this is a real cross-process lock file. In Lambda the deployment
# package itself is read-only, so the lock file has to live in /tmp — and
# note it only protects against concurrent requests landing in the SAME
# warm execution environment, not across Lambda's separate concurrent
# instances. Fine for a low-traffic personal dashboard; see README.
DATA_LOCK_FILE = "/tmp/data.lock" if storage.IS_LAMBDA else "data.lock"
DATA_LOCK_TIMEOUT_SECONDS = 20


def data_lock():
    return FileLock(DATA_LOCK_FILE, timeout=DATA_LOCK_TIMEOUT_SECONDS)


# ---- rate limiting: simple in-memory sliding window, per route ----
# Not meant to withstand a hostile multi-machine attacker — this is a
# single-user local tool. It's meant to stop a stuck tab, an accidental
# double-click, or a runaway loop from quietly burning through API credit.
_rate_limit_calls = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def rate_limit(max_calls, period_seconds):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = fn.__name__
            now = time.time()
            with _rate_limit_lock:
                calls = _rate_limit_calls[key]
                while calls and now - calls[0] > period_seconds:
                    calls.popleft()
                if len(calls) >= max_calls:
                    retry_after = period_seconds - (now - calls[0])
                    return jsonify({
                        "error": f"Slow down — {max_calls} requests per {period_seconds}s max "
                                 f"here (this guards against runaway API costs). "
                                 f"Try again in about {retry_after:.0f}s."
                    }), 429
                calls.append(now)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---- optional password gate, off unless APP_PASSWORD is set ----
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()


@app.before_request
def check_auth():
    if not APP_PASSWORD:
        return  # gate is off by default — fine for local-only use
    auth = request.authorization
    if not auth or auth.password != APP_PASSWORD:
        return Response(
            "Authentication required.", 401,
            {"WWW-Authenticate": 'Basic realm="Stock Watcher"'},
        )


def embeddings_ready():
    return storage.exists(rag_chat.EMBEDDINGS_FILE)


def load_json_list(path):
    return storage.load_json(path, default=[])


def save_json_list(path, data):
    storage.save_json(path, data)


def derive_alias(name, ticker):
    """Best-effort colloquial alias from a company name, e.g.
    'Alphabet Inc.' -> 'alphabet', so a question like "how's google doing"
    still needs the user to have typed a real alias — this just saves
    them having to think of one for the common '<Name> Inc./Corp.' shape."""
    if not name or name.strip().upper() == ticker:
        return []
    cleaned = name.lower()
    for suffix in (" inc.", " inc", " corporation", " corp.", " corp", ", inc.", ", inc"):
        cleaned = cleaned.split(suffix)[0]
    cleaned = cleaned.split(",")[0].strip()
    return [cleaned] if cleaned else []


@app.route("/")
def index():
    watchlist = price_watcher.load_watchlist()
    return render_template("index.html", watchlist=watchlist)


@app.route("/api/status")
def status():
    news_count = len(load_json_list(news_fetch.NEWS_CACHE_FILE))
    filings_count = len(load_json_list(filings_fetch.FILINGS_CACHE_FILE))
    return jsonify({
        "index_ready": embeddings_ready(),
        "news_articles": news_count,
        "filing_chunks": filings_count,
    })


@app.route("/api/prices")
def cached_prices():
    """Fast path: whatever's already logged, no network call. Used to
    paint the dashboard instantly on load before the live refresh lands."""
    latest = rag_chat.load_latest_prices()
    return jsonify({"prices": list(latest.values())})


@app.route("/api/price_history/<ticker>")
def price_history(ticker):
    """Recent price history for one ticker, for the dashboard sparkline.
    Read-only, no lock needed — a torn read here is at worst a stale
    chart for one refresh, never corrupted data on disk."""
    ticker = ticker.strip().upper()
    log = price_watcher.load_price_log()
    history = [s for s in log if s.get("ticker") == ticker]
    history.sort(key=lambda s: s.get("timestamp", ""))
    return jsonify({"ticker": ticker, "history": history[-60:]})


@app.route("/api/refresh_prices", methods=["POST"])
def refresh_prices():
    """Live path: actually hits yfinance for every ticker, same as
    running price_watcher.py from the command line."""
    try:
        with data_lock():
            watchlist = price_watcher.load_watchlist()
            log = price_watcher.load_price_log()
            results, errors = [], []

            for entry in watchlist:
                ticker = entry["ticker"]
                try:
                    snapshot = price_watcher.fetch_snapshot(ticker)
                    log.append(snapshot)
                    results.append(snapshot)
                except Exception as e:
                    errors.append({"ticker": ticker, "error": str(e)})

            price_watcher.save_price_log(log)
    except Timeout:
        return jsonify({"error": "Data is busy (another request is writing). Try again shortly."}), 503

    return jsonify({"prices": results, "errors": errors})


@app.route("/api/ask", methods=["POST"])
@rate_limit(max_calls=20, period_seconds=60)
def ask():
    if not embeddings_ready():
        return jsonify({
            "error": "No index yet — run news_fetch.py / filings_fetch.py / "
                     "build_index.py on the command line first, then reload this page."
        }), 400

    question = (request.get_json(silent=True) or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "Ask something first."}), 400

    watchlist = rag_chat.load_watchlist()
    docs, embeddings = rag_chat.load_index()
    latest_prices = rag_chat.load_latest_prices()

    retrieved = rag_chat.retrieve(question, docs, embeddings)
    mentioned = rag_chat.detect_tickers_in_query(question, watchlist)
    price_context = {t: latest_prices[t] for t in mentioned if t in latest_prices} or latest_prices

    answer = rag_chat.generate_answer(question, retrieved, price_context)

    sources = [
        {
            "id": doc["id"],
            "ticker": doc["ticker"],
            "source_type": doc.get("source_type", "news"),
            "title": doc["title"],
            "published": doc.get("published", ""),
            "similarity": round(score, 3),
        }
        for doc, score in retrieved
    ]

    return jsonify({"answer": answer, "sources": sources})


@app.route("/api/watchlist/add", methods=["POST"])
@rate_limit(max_calls=10, period_seconds=60)
def add_to_watchlist():
    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip().upper()
    name = (body.get("name") or "").strip()

    if not ticker:
        return jsonify({"error": "Enter a ticker symbol."}), 400
    if not ticker.replace(".", "").replace("-", "").isalnum():
        return jsonify({"error": f"'{ticker}' doesn't look like a valid ticker."}), 400

    try:
        with data_lock():
            watchlist = price_watcher.load_watchlist()
            if any(e["ticker"] == ticker for e in watchlist):
                return jsonify({"error": f"{ticker} is already on the watchlist."}), 400

            # validate against yfinance first — if this fails, nothing else
            # runs and the watchlist is never touched, so a typo'd ticker
            # costs nothing
            try:
                snapshot = price_watcher.fetch_snapshot(ticker)
            except Exception as e:
                return jsonify({
                    "error": f"Couldn't fetch a price for {ticker} — is that a valid ticker? ({e})"
                }), 400

            if not name:
                name = ticker
            entry = {"ticker": ticker, "name": name, "aliases": derive_alias(name, ticker)}
            watchlist.append(entry)
            save_json_list(price_watcher.WATCHLIST_FILE, watchlist)

            warnings = []

            log = price_watcher.load_price_log()
            log.append(snapshot)
            price_watcher.save_price_log(log)

            try:
                news_articles = news_fetch.fetch_news_for_ticker(ticker, name)
            except Exception as e:
                news_articles = []
                warnings.append(f"Couldn't fetch news for {ticker}: {e}")
            news_cache = load_json_list(news_fetch.NEWS_CACHE_FILE)
            news_cache.extend(news_articles)
            save_json_list(news_fetch.NEWS_CACHE_FILE, news_cache)

            filing_chunks = []
            try:
                headers = filings_fetch.edgar_headers()
                cik_map = filings_fetch.get_cik_map(headers)
                filing_chunks = filings_fetch.fetch_filing_chunks_for_ticker(ticker, cik_map, headers)
            except filings_fetch.EdgarConfigError as e:
                warnings.append(str(e))
            except Exception as e:
                warnings.append(f"Couldn't fetch a 10-K for {ticker}: {e}")
            filings_cache = load_json_list(filings_fetch.FILINGS_CACHE_FILE)
            filings_cache.extend(filing_chunks)
            save_json_list(filings_fetch.FILINGS_CACHE_FILE, filings_cache)

            # embed only the newly fetched docs, then merge into the existing
            # index — no need to re-embed everyone else's news/filings just
            # to add one ticker
            new_docs = news_articles + filing_chunks
            try:
                build_index.embed_docs(new_docs)
                embeddings = load_json_list(build_index.EMBEDDINGS_FILE)
                embeddings.extend(new_docs)
                save_json_list(build_index.EMBEDDINGS_FILE, embeddings)
            except Exception as e:
                warnings.append(f"Couldn't embed new content for {ticker}: {e}")
    except Timeout:
        return jsonify({"error": "Data is busy (another request is writing). Try again shortly."}), 503

    return jsonify({
        "entry": entry,
        "price": snapshot,
        "news_count": len(news_articles),
        "filing_chunks": len(filing_chunks),
        "warnings": warnings,
    })


@app.route("/api/watchlist/remove", methods=["POST"])
def remove_from_watchlist():
    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"error": "Enter a ticker symbol."}), 400

    try:
        with data_lock():
            watchlist = price_watcher.load_watchlist()
            new_watchlist = [e for e in watchlist if e["ticker"] != ticker]
            if len(new_watchlist) == len(watchlist):
                return jsonify({"error": f"{ticker} isn't on the watchlist."}), 400
            save_json_list(price_watcher.WATCHLIST_FILE, new_watchlist)

            # purge this ticker from every cache too — an explicit "remove"
            # in the UI should mean actually gone, not just hidden from the
            # dashboard
            for path in (
                price_watcher.PRICE_LOG_FILE,
                news_fetch.NEWS_CACHE_FILE,
                filings_fetch.FILINGS_CACHE_FILE,
                build_index.EMBEDDINGS_FILE,
            ):
                data = load_json_list(path)
                filtered = [d for d in data if d.get("ticker") != ticker]
                if len(filtered) != len(data):
                    save_json_list(path, filtered)
    except Timeout:
        return jsonify({"error": "Data is busy (another request is writing). Try again shortly."}), 503

    return jsonify({"removed": ticker})


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, port=5000)
