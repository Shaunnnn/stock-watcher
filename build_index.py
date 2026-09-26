"""
Combines news_cache.json (from news_fetch.py) and filings_cache.json
(from filings_fetch.py) into one embeddings_cache.json, so the RAG step
only pays for embedding calls once per refresh, not on every question.
Either cache file is optional — you can run with just news, just
filings, or both.

Run this after news_fetch.py and/or filings_fetch.py, and again any time
you refresh either:

    python3 news_fetch.py
    python3 filings_fetch.py
    python3 build_index.py
"""
import json
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

NEWS_CACHE_FILE = "news_cache.json"
FILINGS_CACHE_FILE = "filings_cache.json"
EMBEDDINGS_FILE = "embeddings_cache.json"
EMBED_MODEL = "text-embedding-3-small"


def load_cache(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def doc_text(doc):
    """What actually gets embedded: title + summary give the model more
    to match against than the headline/section label alone. Works the
    same way for a news article and a filing chunk — both have a title
    and a summary field."""
    return f"{doc['title']}. {doc['summary']}".strip()


def embed_docs(docs):
    """Embeds a list of docs (news articles and/or filing chunks, either
    shape works — see doc_text) in place and returns them. Pulled out as
    its own function so the web app can embed just a handful of newly
    added docs incrementally, instead of re-embedding the whole cache
    every time someone adds one ticker."""
    if not docs:
        return docs
    texts = [doc_text(d) for d in docs]
    response = client.embeddings.create(model=EMBED_MODEL, input=texts)
    for doc, item in zip(docs, response.data):
        doc["embedding"] = item.embedding
    return docs


def main():
    news = load_cache(NEWS_CACHE_FILE)
    filings = load_cache(FILINGS_CACHE_FILE)
    docs = news + filings

    if not docs:
        print("Nothing to embed — run news_fetch.py and/or filings_fetch.py first.")
        return

    print(f"Embedding {len(docs)} documents ({len(news)} news, {len(filings)} filing "
          f"chunks) with {EMBED_MODEL}...")
    embed_docs(docs)

    with open(EMBEDDINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(docs, f)

    print(f"Done. Wrote {EMBEDDINGS_FILE} ({len(docs)} documents).")
    print("Estimated cost: well under a cent for this many short documents.")


if __name__ == "__main__":
    main()
