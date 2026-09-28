"""
The combined part: retrieval + generation over BOTH recent news and 10-K
filing excerpts, plus whatever live price data you've logged, so you can
ask short-term questions and deeper fundamentals questions in the same
place:

    "why did NVDA move this week?"          (answered from news)
    "what risks does Tesla flag in its 10-K?" (answered from the filing)
    "what's going on with Amazon lately?"     (answered from news + filing)

Run this after build_index.py has produced embeddings_cache.json (and
ideally after price_watcher.py has logged at least one price snapshot):

    python3 rag_chat.py

Each turn:
  1. Detects which watchlist ticker(s), if any, the question mentions
  2. Embeds the question and retrieves the most relevant chunks —
     news articles and/or 10-K filing excerpts, whichever score higher
     (retrieval)
  3. Pulls the latest logged price snapshot for any mentioned ticker
  4. Feeds the retrieved context (tagged as news vs. filing) + price data
     + question to GPT, instructed to answer only from that context
     (generation)
  5. Prints the answer and which sources it used, labeled by type
"""
import os

import numpy as np
from openai import OpenAI

import storage

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

WATCHLIST_FILE = "watchlist.json"
EMBEDDINGS_FILE = "embeddings_cache.json"
PRICE_LOG_FILE = "price_log.json"
EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-3.5-turbo"
TOP_K = 5  # a bit higher now that results can come from two source types


def load_watchlist():
    return storage.load_json(WATCHLIST_FILE, default=[])


def load_index():
    docs = storage.load_json(EMBEDDINGS_FILE, default=[])
    embeddings = np.array([d["embedding"] for d in docs])
    return docs, embeddings


def load_latest_prices():
    """Collapse price_log.json (a running history) down to the most recent
    snapshot per ticker. Returns {} if no log exists yet — that's fine,
    the bot just won't have live price context."""
    log = storage.load_json(PRICE_LOG_FILE, default=[])
    latest = {}
    for snapshot in log:
        latest[snapshot["ticker"]] = snapshot  # later entries overwrite earlier
    return latest


def detect_tickers_in_query(query, watchlist):
    """Pure function, easy to unit test: which watchlist tickers/company
    aliases does this question mention? Matches on the ticker symbol
    (AAPL) or any of the watchlist's colloquial aliases (apple)."""
    query_lower = query.lower()
    matched = []
    for entry in watchlist:
        names_to_check = [entry["ticker"].lower()] + [a.lower() for a in entry.get("aliases", [])]
        if any(name in query_lower for name in names_to_check):
            matched.append(entry["ticker"])
    return list(dict.fromkeys(matched))  # dedupe, keep order


def embed_query(query):
    response = client.embeddings.create(model=EMBED_MODEL, input=[query])
    return np.array(response.data[0].embedding)


def cosine_similarity(a, b):
    a_norm = a / np.linalg.norm(a)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return b_norm @ a_norm


def retrieve(query, docs, embeddings, k=TOP_K):
    query_vec = embed_query(query)
    scores = cosine_similarity(query_vec, embeddings)
    top_indices = np.argsort(scores)[::-1][:k]
    return [(docs[i], float(scores[i])) for i in top_indices]


def format_source(doc):
    """News and filing chunks get labeled differently in the prompt so
    the model (and the person reading the printed sources) can tell a
    same-day headline apart from a sentence out of an official filing."""
    label = "10-K filing" if doc.get("source_type") == "filing" else "news"
    return f"[{doc['ticker']} {label}, {doc['published']}] {doc['title']}. {doc['summary']}"


def generate_answer(query, retrieved, price_context):
    doc_context = "\n\n".join(format_source(doc) for doc, _score in retrieved)
    price_lines = "\n".join(
        f"{t}: ${s['price']} ({s['pct_change']:+.2f}% vs previous close, as of {s['timestamp']})"
        for t, s in price_context.items()
    ) or "No live price data logged yet."

    prompt = f"""You are a stock-watching assistant with access to two kinds of
information: recent news headlines (good for "what happened lately") and
excerpts from official 10-K filings (good for deeper questions about
risks, strategy, or financial discussion). Answer the user's question
using ONLY the price, news, and filing information given below, and say
which kind of source your answer relies on. If it isn't enough to answer
confidently, say so rather than guessing or using outside knowledge. This
is informational only, not financial advice — do not recommend buying or
selling.

RECENT PRICE DATA:
{price_lines}

RETRIEVED CONTEXT (news and/or 10-K filing excerpts):
{doc_context}

QUESTION: {query}

ANSWER:"""

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.4,
    )
    return response.choices[0].message.content.strip()


def main():
    print("Loading watchlist, news index, and price log...")
    watchlist = load_watchlist()
    docs, embeddings = load_index()
    latest_prices = load_latest_prices()
    print(f"Loaded {len(docs)} news articles. Ask about your watchlist (or 'quit' to exit).\n")

    while True:
        query = input("You: ").strip()
        if not query or query.lower() in ("quit", "exit"):
            break

        retrieved = retrieve(query, docs, embeddings)
        mentioned = detect_tickers_in_query(query, watchlist)
        price_context = {
            t: latest_prices[t] for t in mentioned if t in latest_prices
        } or latest_prices  # fall back to showing everything logged

        print("\n  [retrieved sources]")
        for doc, score in retrieved:
            label = "filing" if doc.get("source_type") == "filing" else "news "
            print(f"   - [{label}] {doc['id']} (similarity: {score:.3f}): {doc['title'][:70]}")

        answer = generate_answer(query, retrieved, price_context)
        print(f"\nBot: {answer}\n")


if __name__ == "__main__":
    main()
