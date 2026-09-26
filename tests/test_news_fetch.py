import feedparser

import news_fetch


def test_clean_summary_strips_tags_and_unescapes_entities():
    cleaned = news_fetch.clean_summary("<a href='x'>Nvidia shares rise</a>&nbsp;Reuters")
    assert cleaned == "Nvidia shares rise Reuters"


def test_clean_summary_handles_empty_input():
    assert news_fetch.clean_summary("") == ""
    assert news_fetch.clean_summary(None) == ""


def test_clean_summary_collapses_whitespace():
    assert news_fetch.clean_summary("a   b\n\nc") == "a b c"


def test_build_feed_url_encodes_ticker_and_name():
    url = news_fetch.build_feed_url("NVDA", "NVIDIA Corporation")
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "NVDA" in url
    assert "NVIDIA" in url


FAKE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>Nvidia shares rise on strong AI chip demand</title>
  <link>https://example.com/nvda-1</link>
  <pubDate>Mon, 22 Sep 2026 10:00:00 GMT</pubDate>
  <description><![CDATA[<a href="https://example.com/nvda-1">Nvidia shares rise on strong AI chip demand</a>&nbsp;<font color="#6f6f6f">Reuters</font>]]></description>
</item>
<item>
  <title>Analysts raise Nvidia price targets after earnings beat</title>
  <link>https://example.com/nvda-2</link>
  <pubDate>Tue, 23 Sep 2026 09:00:00 GMT</pubDate>
  <description><![CDATA[<a href="https://example.com/nvda-2">Analysts raise Nvidia price targets after earnings beat</a>]]></description>
</item>
</channel></rss>"""


def test_parse_entries_shape_and_count():
    parsed = feedparser.parse(FAKE_RSS)
    articles = news_fetch.parse_entries(parsed, "NVDA", limit=6)
    assert len(articles) == 2
    assert articles[0]["id"] == "NVDA-news-0"
    assert articles[1]["id"] == "NVDA-news-1"
    assert all(a["ticker"] == "NVDA" for a in articles)
    assert all(a["source_type"] == "news" for a in articles)
    assert "Nvidia shares rise" in articles[0]["title"]
    assert "<a" not in articles[0]["summary"]


def test_parse_entries_respects_limit():
    parsed = feedparser.parse(FAKE_RSS)
    articles = news_fetch.parse_entries(parsed, "NVDA", limit=1)
    assert len(articles) == 1


def test_fetch_news_for_ticker_uses_feedparser(monkeypatch):
    calls = {}
    real_parse = feedparser.parse  # captured before patching, so the fake below doesn't recurse into itself

    def fake_parse(url):
        calls["url"] = url
        return real_parse(FAKE_RSS)

    monkeypatch.setattr(news_fetch.feedparser, "parse", fake_parse)
    articles = news_fetch.fetch_news_for_ticker("NVDA", "NVIDIA Corporation")
    assert len(articles) == 2
    assert "NVDA" in calls["url"]
