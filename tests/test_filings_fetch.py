import os

import pytest

import filings_fetch


def test_chunk_text_splits_with_overlap():
    words_text = " ".join(f"word{i}" for i in range(500))
    chunks = filings_fetch.chunk_text(words_text, max_words=220, overlap=40)
    assert len(chunks) >= 2
    assert chunks[0].split()[0] == "word0"
    overlap_from_c0 = chunks[0].split()[-40:]
    start_of_c1 = chunks[1].split()[:40]
    assert overlap_from_c0 == start_of_c1


def test_chunk_text_empty_input():
    assert filings_fetch.chunk_text("") == []


def test_chunk_text_shorter_than_one_chunk():
    text = "just a few words here"
    chunks = filings_fetch.chunk_text(text, max_words=220, overlap=40)
    assert chunks == [text]


FAKE_10K_TEXT = (
    "TABLE OF CONTENTS "
    "Item 1A. Risk Factors Item 1B. Unresolved Staff Comments "
    "Item 7. Management's Discussion and Analysis Item 7A. Quantitative Disclosures "
    + ("filler " * 50) +
    "Item 1A. Risk Factors "
    + ("Our business faces significant competition and regulatory risk. " * 60) +
    "Item 1B. Unresolved Staff Comments None. "
    + ("filler " * 50) +
    "Item 7. Management's Discussion and Analysis "
    + ("Revenue grew year over year driven by strong demand. " * 60) +
    "Item 7A. Quantitative and Qualitative Disclosures About Market Risk "
    "See above."
)


def test_extract_sections_skips_table_of_contents():
    sections = filings_fetch.extract_sections(FAKE_10K_TEXT)
    assert "Item 1A - Risk Factors" in sections
    assert "Item 7 - MD&A" in sections
    assert "significant competition" in sections["Item 1A - Risk Factors"]
    assert "Revenue grew" in sections["Item 7 - MD&A"]
    # the real section, not the short table-of-contents blurb
    assert len(sections["Item 1A - Risk Factors"]) > 500


def test_extract_sections_falls_back_when_nothing_found():
    sections = filings_fetch.extract_sections("Just some random filing text with no item markers.")
    assert "Filing excerpt" in sections


def test_build_filing_chunks_respects_limit_and_tags_fields():
    sections = filings_fetch.extract_sections(FAKE_10K_TEXT)
    chunks = filings_fetch.build_filing_chunks(
        "NVDA", sections, "2026-02-20", "https://example.com/nvda-10k", limit=3
    )
    assert len(chunks) == 3
    assert all(c["source_type"] == "filing" for c in chunks)
    assert all(c["ticker"] == "NVDA" for c in chunks)
    assert chunks[0]["id"] == "NVDA-10K-0"


def test_edgar_headers_requires_env_var(monkeypatch):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    with pytest.raises(filings_fetch.EdgarConfigError):
        filings_fetch.edgar_headers()


def test_edgar_headers_returns_header_when_set(monkeypatch):
    monkeypatch.setenv("EDGAR_USER_AGENT", "Test User test@example.com")
    headers = filings_fetch.edgar_headers()
    assert headers["User-Agent"] == "Test User test@example.com"


def test_describe_sections_warns_on_fallback(capsys):
    filings_fetch.describe_sections("NVDA", {"Filing excerpt": "some opening text " * 100})
    out = capsys.readouterr().out
    assert "could not confidently locate" in out


def test_describe_sections_warns_on_suspiciously_short_section(capsys):
    filings_fetch.describe_sections("NVDA", {"Item 1A - Risk Factors": "too short"})
    out = capsys.readouterr().out
    assert "shorter than expected" in out


def test_describe_sections_no_warning_for_healthy_section(capsys):
    healthy_text = "Our risk factors are extensive. " * 100
    filings_fetch.describe_sections("NVDA", {"Item 1A - Risk Factors": healthy_text})
    out = capsys.readouterr().out
    assert "shorter than expected" not in out
    assert "could not confidently locate" not in out


class FakeResp:
    def __init__(self, json_data=None, text_data=None):
        self._json = json_data
        self.text = text_data or ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


FAKE_SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["8-K", "10-K", "10-Q"],
            "accessionNumber": ["0001-1", "0001-2", "0001-3"],
            "primaryDocument": ["a.htm", "goog10k.htm", "c.htm"],
            "filingDate": ["2026-08-01", "2026-02-15", "2026-05-01"],
        }
    }
}

FAKE_10K_HTML = (
    "<html><body>TABLE OF CONTENTS Item 1A. Risk Factors Item 1B. "
    "<p>" + ("Our risk is significant. " * 80) + "</p>"
    "Item 1A. Risk Factors <p>" + ("Our real risk factors are extensive and detailed. " * 60) + "</p>"
    "Item 1B. Unresolved Staff Comments None."
    "</body></html>"
)


def test_get_latest_10k_picks_the_10k_row_not_8k_or_10q(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert "data.sec.gov/submissions" in url
        return FakeResp(json_data=FAKE_SUBMISSIONS)

    monkeypatch.setattr(filings_fetch.requests, "get", fake_get)
    latest = filings_fetch.get_latest_10k("0001652044", {"User-Agent": "test"})
    assert latest["primaryDocument"] == "goog10k.htm"
    assert latest["filingDate"] == "2026-02-15"


def test_fetch_filing_chunks_for_ticker_end_to_end(monkeypatch):
    call_log = []

    def fake_get(url, headers=None, timeout=None):
        call_log.append(url)
        if "data.sec.gov/submissions" in url:
            return FakeResp(json_data=FAKE_SUBMISSIONS)
        if "goog10k.htm" in url:
            return FakeResp(text_data=FAKE_10K_HTML)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(filings_fetch.requests, "get", fake_get)
    monkeypatch.setattr(filings_fetch.time, "sleep", lambda *_args: None)

    headers = {"User-Agent": "test"}
    chunks = filings_fetch.fetch_filing_chunks_for_ticker("GOOGL", {"GOOGL": "0001652044"}, headers)
    assert len(chunks) >= 1
    assert all(c["ticker"] == "GOOGL" for c in chunks)
    assert all(c["source_type"] == "filing" for c in chunks)
    assert "goog10k.htm" in "".join(call_log)


def test_fetch_filing_chunks_for_ticker_unknown_ticker_returns_empty():
    chunks = filings_fetch.fetch_filing_chunks_for_ticker("NOPE", {}, {"User-Agent": "test"})
    assert chunks == []
