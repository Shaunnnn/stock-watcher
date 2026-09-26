import json

import numpy as np

import rag_chat

WATCHLIST = [
    {"ticker": "AAPL", "name": "Apple Inc.", "aliases": ["apple"]},
    {"ticker": "MSFT", "name": "Microsoft Corporation", "aliases": ["microsoft"]},
    {"ticker": "TSLA", "name": "Tesla, Inc.", "aliases": ["tesla"]},
    {"ticker": "NVDA", "name": "NVIDIA Corporation", "aliases": ["nvidia"]},
    {"ticker": "AMZN", "name": "Amazon.com, Inc.", "aliases": ["amazon"]},
]


def test_detect_tickers_in_query_by_ticker_symbol():
    assert rag_chat.detect_tickers_in_query("why did NVDA move this week?", WATCHLIST) == ["NVDA"]


def test_detect_tickers_in_query_by_alias():
    assert rag_chat.detect_tickers_in_query("what's going on with Tesla?", WATCHLIST) == ["TSLA"]


def test_detect_tickers_in_query_multiple_matches_preserve_order():
    result = rag_chat.detect_tickers_in_query("any news on apple and microsoft?", WATCHLIST)
    assert result == ["AAPL", "MSFT"]


def test_detect_tickers_in_query_no_match():
    assert rag_chat.detect_tickers_in_query("how's the weather today", WATCHLIST) == []


def test_detect_tickers_in_query_dedupes():
    result = rag_chat.detect_tickers_in_query("NVDA NVDA nvidia nvidia", WATCHLIST)
    assert result == ["NVDA"]


def test_cosine_similarity_picks_nearest_vector():
    docs_embeddings = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ])
    query_vec = np.array([0.9, 0.1, 0.0])
    scores = rag_chat.cosine_similarity(query_vec, docs_embeddings)
    assert int(np.argmax(scores)) == 0


def test_retrieve_returns_top_k_sorted_by_score(monkeypatch):
    docs = [
        {"id": "a", "ticker": "NVDA"},
        {"id": "b", "ticker": "TSLA"},
        {"id": "c", "ticker": "AMZN"},
    ]
    embeddings = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
        [0.5, 0.5],
    ])
    monkeypatch.setattr(rag_chat, "embed_query", lambda q: np.array([0.9, 0.1]))
    results = rag_chat.retrieve("anything", docs, embeddings, k=2)
    assert len(results) == 2
    assert results[0][0]["id"] == "a"  # closest match first
    assert results[0][1] >= results[1][1]  # sorted descending by score


def test_format_source_labels_news():
    doc = {"ticker": "NVDA", "source_type": "news", "published": "2026-09-20",
           "title": "Nvidia rises", "summary": "on strong demand"}
    assert "news" in rag_chat.format_source(doc)
    assert "10-K filing" not in rag_chat.format_source(doc)


def test_format_source_labels_filing():
    doc = {"ticker": "NVDA", "source_type": "filing", "published": "2026-02-01",
           "title": "NVDA 10-K - Risk Factors", "summary": "competition risk text"}
    assert "10-K filing" in rag_chat.format_source(doc)


def test_load_latest_prices_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rag_chat, "PRICE_LOG_FILE", str(tmp_path / "nope.json"))
    assert rag_chat.load_latest_prices() == {}


def test_load_latest_prices_collapses_to_most_recent_per_ticker(tmp_path, monkeypatch):
    log_file = tmp_path / "price_log.json"
    log_file.write_text(json.dumps([
        {"ticker": "NVDA", "price": 100, "timestamp": "t1"},
        {"ticker": "NVDA", "price": 110, "timestamp": "t2"},
        {"ticker": "TSLA", "price": 200, "timestamp": "t1"},
    ]))
    monkeypatch.setattr(rag_chat, "PRICE_LOG_FILE", str(log_file))

    latest = rag_chat.load_latest_prices()
    assert latest["NVDA"]["price"] == 110  # the later entry wins
    assert latest["TSLA"]["price"] == 200
