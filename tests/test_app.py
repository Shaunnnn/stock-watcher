import json
import time
from types import SimpleNamespace

import pytest
from filelock import FileLock

import app as app_module
import build_index
import filings_fetch
import news_fetch
import price_watcher
import rag_chat


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolates every file this app touches to a temp directory (so tests
    never read or write the real project's watchlist.json etc.), seeds a
    small watchlist, resets the in-memory rate limiter between tests, and
    returns a Flask test client."""
    watchlist = [
        {"ticker": "AAPL", "name": "Apple Inc.", "aliases": ["apple"]},
        {"ticker": "TSLA", "name": "Tesla, Inc.", "aliases": ["tesla"]},
    ]
    (tmp_path / "watchlist.json").write_text(json.dumps(watchlist))

    for mod, attr in (
        (price_watcher, "WATCHLIST_FILE"),
        (news_fetch, "WATCHLIST_FILE"),
        (filings_fetch, "WATCHLIST_FILE"),
        (rag_chat, "WATCHLIST_FILE"),
    ):
        monkeypatch.setattr(mod, attr, str(tmp_path / "watchlist.json"))

    monkeypatch.setattr(price_watcher, "PRICE_LOG_FILE", str(tmp_path / "price_log.json"))
    monkeypatch.setattr(rag_chat, "PRICE_LOG_FILE", str(tmp_path / "price_log.json"))
    monkeypatch.setattr(news_fetch, "NEWS_CACHE_FILE", str(tmp_path / "news_cache.json"))
    monkeypatch.setattr(filings_fetch, "FILINGS_CACHE_FILE", str(tmp_path / "filings_cache.json"))
    monkeypatch.setattr(build_index, "NEWS_CACHE_FILE", str(tmp_path / "news_cache.json"))
    monkeypatch.setattr(build_index, "FILINGS_CACHE_FILE", str(tmp_path / "filings_cache.json"))
    monkeypatch.setattr(build_index, "EMBEDDINGS_FILE", str(tmp_path / "embeddings_cache.json"))
    monkeypatch.setattr(rag_chat, "EMBEDDINGS_FILE", str(tmp_path / "embeddings_cache.json"))
    monkeypatch.setattr(app_module, "DATA_LOCK_FILE", str(tmp_path / "data.lock"))
    monkeypatch.setattr(app_module, "APP_PASSWORD", "")  # gate off unless a test turns it on

    app_module._rate_limit_calls.clear()

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_index_renders_watchlist(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"AAPL" in resp.data
    assert b"TSLA" in resp.data


def test_status_reports_index_not_ready_when_no_embeddings(client):
    resp = client.get("/api/status")
    data = resp.get_json()
    assert data["index_ready"] is False
    assert data["news_articles"] == 0


def test_prices_empty_when_no_log(client):
    resp = client.get("/api/prices")
    assert resp.get_json() == {"prices": []}


def test_price_history_for_unknown_ticker_is_empty(client):
    resp = client.get("/api/price_history/ZZZZ")
    assert resp.get_json() == {"ticker": "ZZZZ", "history": []}


def test_ask_without_index_returns_helpful_400(client):
    resp = client.post("/api/ask", json={"question": "why did AAPL move?"})
    assert resp.status_code == 400
    assert "index" in resp.get_json()["error"].lower()


def test_ask_requires_a_question(client, monkeypatch, tmp_path):
    # make the index "ready" by dropping a minimal embeddings file in place
    (tmp_path / "embeddings_cache.json").write_text("[]")
    resp = client.post("/api/ask", json={"question": "   "})
    assert resp.status_code == 400
    assert "ask something" in resp.get_json()["error"].lower()


def test_add_to_watchlist_rejects_invalid_ticker(client):
    resp = client.post("/api/watchlist/add", json={"ticker": "!!!", "name": "Nope"})
    assert resp.status_code == 400
    assert "valid ticker" in resp.get_json()["error"]


def test_add_to_watchlist_rejects_duplicate(client):
    resp = client.post("/api/watchlist/add", json={"ticker": "AAPL", "name": "Apple Inc."})
    assert resp.status_code == 400
    assert "already on the watchlist" in resp.get_json()["error"]


def test_add_to_watchlist_full_flow(client, monkeypatch, tmp_path):
    monkeypatch.setattr(price_watcher, "fetch_snapshot", lambda ticker: {
        "ticker": ticker, "price": 123.45, "prev_close": 120.0,
        "pct_change": 2.88, "timestamp": "2026-09-25T15:00:00+00:00",
    })
    monkeypatch.setattr(news_fetch, "fetch_news_for_ticker", lambda ticker, name: [{
        "id": f"{ticker}-news-0", "ticker": ticker, "source_type": "news",
        "title": f"{name} news", "summary": "something happened",
        "link": "https://example.com", "published": "2026-09-25",
    }])
    monkeypatch.setattr(filings_fetch, "get_cik_map", lambda headers: {})
    monkeypatch.setattr(filings_fetch, "fetch_filing_chunks_for_ticker", lambda ticker, cik_map, headers: [{
        "id": f"{ticker}-10K-0", "ticker": ticker, "source_type": "filing",
        "title": f"{ticker} 10-K", "summary": "risk text", "published": "2026-01-01",
    }])
    monkeypatch.setattr(build_index, "embed_docs", lambda docs: [d.update(embedding=[0.5, 0.5]) or d for d in docs])

    resp = client.post("/api/watchlist/add", json={"ticker": "googl", "name": "Alphabet Inc."})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["entry"]["ticker"] == "GOOGL"
    assert data["entry"]["aliases"] == ["alphabet"]
    assert data["news_count"] == 1
    assert data["filing_chunks"] == 1
    assert data["warnings"] == []

    watchlist = json.loads((tmp_path / "watchlist.json").read_text())
    assert any(e["ticker"] == "GOOGL" for e in watchlist)

    embeddings = json.loads((tmp_path / "embeddings_cache.json").read_text())
    assert len(embeddings) == 2  # 1 news + 1 filing chunk
    assert all("embedding" in d for d in embeddings)


def test_add_to_watchlist_bad_ticker_never_touches_watchlist_file(client, monkeypatch, tmp_path):
    def raise_not_found(ticker):
        raise ValueError("no data found for this ticker")

    monkeypatch.setattr(price_watcher, "fetch_snapshot", raise_not_found)
    resp = client.post("/api/watchlist/add", json={"ticker": "NOPE1234", "name": ""})
    assert resp.status_code == 400

    watchlist = json.loads((tmp_path / "watchlist.json").read_text())
    assert not any(e["ticker"] == "NOPE1234" for e in watchlist)


def test_remove_from_watchlist_purges_every_cache(client, tmp_path):
    (tmp_path / "price_log.json").write_text(json.dumps([
        {"ticker": "AAPL", "price": 1}, {"ticker": "TSLA", "price": 2},
    ]))
    (tmp_path / "news_cache.json").write_text(json.dumps([
        {"ticker": "AAPL"}, {"ticker": "TSLA"},
    ]))
    (tmp_path / "filings_cache.json").write_text(json.dumps([{"ticker": "TSLA"}]))
    (tmp_path / "embeddings_cache.json").write_text(json.dumps([
        {"ticker": "AAPL"}, {"ticker": "TSLA"},
    ]))

    resp = client.post("/api/watchlist/remove", json={"ticker": "TSLA"})
    assert resp.status_code == 200
    assert resp.get_json() == {"removed": "TSLA"}

    watchlist = json.loads((tmp_path / "watchlist.json").read_text())
    assert not any(e["ticker"] == "TSLA" for e in watchlist)
    assert any(e["ticker"] == "AAPL" for e in watchlist)  # untouched

    for fname in ("price_log.json", "news_cache.json", "filings_cache.json", "embeddings_cache.json"):
        data = json.loads((tmp_path / fname).read_text())
        assert not any(d.get("ticker") == "TSLA" for d in data)


def test_remove_unknown_ticker_errors(client):
    resp = client.post("/api/watchlist/remove", json={"ticker": "ZZZZ"})
    assert resp.status_code == 400
    assert "isn't on the watchlist" in resp.get_json()["error"]


def test_add_to_watchlist_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(price_watcher, "fetch_snapshot", lambda ticker: (_ for _ in ()).throw(
        ValueError("invalid ticker")
    ))
    # every one of these fails validation fast (still counts against the limiter)
    responses = [
        client.post("/api/watchlist/add", json={"ticker": f"BAD{i}", "name": ""})
        for i in range(11)
    ]
    statuses = [r.status_code for r in responses]
    assert 429 in statuses  # the 11th call trips the 10-per-minute limit
    assert statuses.index(429) == 10


def test_data_lock_busy_returns_503(client, monkeypatch, tmp_path):
    # hold the lock ourselves first, like a concurrent request would
    lock = FileLock(str(tmp_path / "data.lock"))
    lock.acquire()
    try:
        monkeypatch.setattr(app_module, "DATA_LOCK_TIMEOUT_SECONDS", 0.3)
        resp = client.post("/api/watchlist/remove", json={"ticker": "AAPL"})
        assert resp.status_code == 503
        assert "busy" in resp.get_json()["error"].lower()
    finally:
        lock.release()


def test_password_gate_when_enabled(client, monkeypatch):
    monkeypatch.setattr(app_module, "APP_PASSWORD", "secret123")
    resp = client.get("/")
    assert resp.status_code == 401

    resp_ok = client.get("/", headers={
        "Authorization": "Basic " + __import__("base64").b64encode(b"user:secret123").decode()
    })
    assert resp_ok.status_code == 200


def test_password_gate_off_by_default(client):
    resp = client.get("/")
    assert resp.status_code == 200
