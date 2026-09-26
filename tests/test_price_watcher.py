import price_watcher


def test_compute_pct_change_up():
    assert price_watcher.compute_pct_change(103, 100) == 3.0


def test_compute_pct_change_down():
    assert price_watcher.compute_pct_change(97, 100) == -3.0


def test_compute_pct_change_guards_div_by_zero():
    assert price_watcher.compute_pct_change(100, 0) == 0.0
    assert price_watcher.compute_pct_change(100, None) == 0.0


def test_format_alert_up():
    snap = {"ticker": "NVDA", "price": 103.0, "prev_close": 100.0, "pct_change": 3.0}
    text = price_watcher.format_alert(snap)
    assert "NVDA" in text and "up" in text and "3.0" in text


def test_format_alert_down():
    snap = {"ticker": "TSLA", "price": 97.0, "prev_close": 100.0, "pct_change": -3.0}
    text = price_watcher.format_alert(snap)
    assert "down" in text and "3.0" in text


def test_prune_log_keeps_most_recent_per_ticker():
    log = [
        {"ticker": "AAPL", "timestamp": f"2026-01-{i:02d}"} for i in range(1, 11)
    ]
    trimmed = price_watcher.prune_log(log, max_per_ticker=3)
    assert len(trimmed) == 3
    # keeps the LAST 3 (most recent), not the first 3
    assert [s["timestamp"] for s in trimmed] == ["2026-01-08", "2026-01-09", "2026-01-10"]


def test_prune_log_handles_multiple_tickers_independently():
    log = (
        [{"ticker": "AAPL", "timestamp": f"a{i}"} for i in range(5)]
        + [{"ticker": "TSLA", "timestamp": f"t{i}"} for i in range(2)]
    )
    trimmed = price_watcher.prune_log(log, max_per_ticker=3)
    tickers = [s["ticker"] for s in trimmed]
    assert tickers.count("AAPL") == 3
    assert tickers.count("TSLA") == 2  # under the cap, untouched


def test_prune_log_empty_input():
    assert price_watcher.prune_log([]) == []


def test_load_save_price_log_roundtrip(tmp_path, monkeypatch):
    log_file = tmp_path / "price_log.json"
    monkeypatch.setattr(price_watcher, "PRICE_LOG_FILE", str(log_file))

    assert price_watcher.load_price_log() == []  # missing file -> []

    snapshot = {"ticker": "AAPL", "price": 200.0, "prev_close": 198.0,
                "pct_change": 1.01, "timestamp": "2026-09-25T00:00:00+00:00"}
    price_watcher.save_price_log([snapshot])

    reloaded = price_watcher.load_price_log()
    assert reloaded == [snapshot]


def test_save_price_log_prunes_on_write(tmp_path, monkeypatch):
    log_file = tmp_path / "price_log.json"
    monkeypatch.setattr(price_watcher, "PRICE_LOG_FILE", str(log_file))

    big_log = [
        {"ticker": "AAPL", "timestamp": f"2026-01-{i:03d}"}
        for i in range(price_watcher.MAX_HISTORY_PER_TICKER + 50)
    ]
    price_watcher.save_price_log(big_log)

    reloaded = price_watcher.load_price_log()
    assert len(reloaded) == price_watcher.MAX_HISTORY_PER_TICKER


def test_fetch_snapshot_uses_yfinance_fast_info(monkeypatch):
    class FakeTicker:
        def __init__(self, ticker):
            self.fast_info = {"last_price": 150.0, "previous_close": 145.0}

    monkeypatch.setattr(price_watcher.yf, "Ticker", FakeTicker)
    snapshot = price_watcher.fetch_snapshot("AAPL")
    assert snapshot["ticker"] == "AAPL"
    assert snapshot["price"] == 150.0
    assert snapshot["prev_close"] == 145.0
    assert snapshot["pct_change"] == price_watcher.compute_pct_change(150.0, 145.0)
    assert "timestamp" in snapshot
