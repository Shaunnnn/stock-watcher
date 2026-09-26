"""
News side of the stock watcher.

Pulls recent headlines for every ticker in watchlist.json from Google
News' public RSS search feed (no API key needed) and saves them to
news_cache.json. This is the raw material the RAG step (build_index.py)
will embed and search over.

Run this to refresh the news cache:

    python3 news_fetch.py
"""
import html
import json
import re
import feedparser

WATCHLIST_FILE = "watchlist.json"
NEWS_CACHE_FILE = "news_cache.json"
MAX_ARTICLES_PER_TICKER = 6

TAG_RE = re.compile(r"<[^>]+>")


def load_watchlist():
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def clean_summary(raw_html):
    """Google News RSS summaries are a blob of HTML (an <a> tag plus
    sometimes a source name). Strip tags down to plain text."""
    if not raw_html:
        return ""
    text = TAG_RE.sub("", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def build_feed_url(ticker, name):
    query = f"{ticker} {name} stock".replace(" ", "+")
    return f"https://news.google.com/rss/search?q={query}+when:3d&hl=en-US&gl=US&ceid=US:en"


def parse_entries(parsed_feed, ticker, limit=MAX_ARTICLES_PER_TICKER):
    """Pure-ish function: takes an already-parsed feedparser object (so it
    can be unit tested against a fixture without hitting the network) and
    turns it into our article dict shape."""
    articles = []
    for i, entry in enumerate(parsed_feed.entries[:limit]):
        articles.append({
            "id": f"{ticker}-news-{i}",
            "ticker": ticker,
            "source_type": "news",
            "title": entry.get("title", "").strip(),
            "summary": clean_summary(entry.get("summary", "")),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
        })
    return articles


def fetch_news_for_ticker(ticker, name):
    url = build_feed_url(ticker, name)
    parsed = feedparser.parse(url)
    return parse_entries(parsed, ticker)


def main():
    watchlist = load_watchlist()
    all_articles = []

    for entry in watchlist:
        ticker, name = entry["ticker"], entry["name"]
        try:
            articles = fetch_news_for_ticker(ticker, name)
        except Exception as e:
            print(f"  Could not fetch news for {ticker}: {e}")
            continue

        print(f"{ticker}: {len(articles)} articles")
        all_articles.extend(articles)

    with open(NEWS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_articles, f, indent=2)

    print(f"\nSaved {len(all_articles)} articles to {NEWS_CACHE_FILE}")


if __name__ == "__main__":
    main()
