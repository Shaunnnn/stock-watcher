"""
Filings side of the combined bot: pulls the most recent 10-K for each
watchlist ticker from SEC EDGAR (free, no API key — SEC just requires a
descriptive User-Agent identifying who's making requests, so you must set
one, see Setup below), extracts the Risk Factors and MD&A sections (the
two most Q&A-relevant parts of a 10-K), chunks them, and saves everything
to filings_cache.json.

Setup (SEC requires this — see https://www.sec.gov/os/webmaster-faq#developers):
    export EDGAR_USER_AGENT="Your Name your.email@example.com"

Run:
    python3 filings_fetch.py
"""
import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup

WATCHLIST_FILE = "watchlist.json"
FILINGS_CACHE_FILE = "filings_cache.json"

# Fallback CIKs (SEC's per-company ID) in case the live ticker->CIK lookup
# can't be reached. These are the well-known, public CIKs for the 5
# watchlist companies — double check against https://www.sec.gov/cgi-bin/browse-edgar
# if a fetch ever 404s, company filers occasionally change structure.
FALLBACK_CIKS = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "TSLA": "0001318605",
    "NVDA": "0001045810",
    "AMZN": "0001018724",
}

MAX_CHUNKS_PER_FILING = 8
CHUNK_WORDS = 220
CHUNK_OVERLAP = 40

SECTION_PATTERNS = {
    "Item 1A - Risk Factors": (r"item\s*1a[\.\:]?\s*risk factors", r"item\s*1b[\.\:]?"),
    "Item 7 - MD&A": (
        r"item\s*7[\.\:]?\s*management.?s discussion",
        r"item\s*7a[\.\:]?",
    ),
}


class EdgarConfigError(RuntimeError):
    """Raised when EDGAR_USER_AGENT isn't set. A plain RuntimeError subclass
    (not SystemExit) so callers other than the CLI — like the Flask app —
    can catch it and turn it into an error response instead of the whole
    process exiting."""


def edgar_headers():
    user_agent = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not user_agent:
        raise EdgarConfigError(
            "Set EDGAR_USER_AGENT first, e.g.:\n"
            '  export EDGAR_USER_AGENT="Your Name your.email@example.com"\n'
            "SEC requires a descriptive User-Agent for automated access — "
            "see https://www.sec.gov/os/webmaster-faq#developers"
        )
    return {"User-Agent": user_agent}


def load_watchlist():
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_cik_map(headers):
    """Ticker -> zero-padded CIK, from SEC's official mapping file. Falls
    back to the hardcoded map if the request fails for any reason."""
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json", headers=headers, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            row["ticker"].upper(): str(row["cik_str"]).zfill(10)
            for row in data.values()
        }
    except Exception as e:
        print(f"  Could not fetch live CIK map ({e}), using fallback list.")
        return FALLBACK_CIKS


def get_latest_10k(cik, headers):
    """Returns {accessionNumber, primaryDocument, filingDate} for the most
    recent 10-K on file for this CIK, or None if none found."""
    resp = requests.get(
        f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers, timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    recent = data["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form == "10-K":
            return {
                "accessionNumber": recent["accessionNumber"][i],
                "primaryDocument": recent["primaryDocument"][i],
                "filingDate": recent["filingDate"][i],
            }
    return None


def build_filing_url(cik, accession_number, primary_document):
    accession_nodash = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession_nodash}/{primary_document}"
    )


def html_to_text(raw_html):
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def find_best_span(text_lower, start_pattern, end_pattern, min_len=800):
    """10-Ks usually mention 'Item 1A' both in the table of contents and
    at the real section start, so the naive first match is often wrong.
    Heuristic: try every start match, pair it with the next end match
    after it, and keep whichever pair spans the most text — the table of
    contents entries are right next to each other with nothing in
    between, the real section has the full body of text."""
    starts = [m.start() for m in re.finditer(start_pattern, text_lower)]
    best = None
    for s in starts:
        end_match = re.search(end_pattern, text_lower[s + 1:])
        e = s + 1 + end_match.start() if end_match else len(text_lower)
        length = e - s
        if length >= min_len and (best is None or length > best[2]):
            best = (s, e, length)
    return best


def extract_sections(full_text):
    """Returns {section_label: text} for whichever of Risk Factors / MD&A
    could be confidently located. Falls back to the document's opening
    text if neither section is found (better than nothing, worse than a
    clean section match)."""
    text_lower = full_text.lower()
    sections = {}
    for label, (start_pat, end_pat) in SECTION_PATTERNS.items():
        span = find_best_span(text_lower, start_pat, end_pat)
        if span:
            s, e, _ = span
            sections[label] = full_text[s:e].strip()

    if not sections:
        sections["Filing excerpt"] = full_text[:20000]

    return sections


def chunk_text(text, max_words=CHUNK_WORDS, overlap=CHUNK_OVERLAP):
    """Pure function: splits text into overlapping word-count chunks. Easy
    to unit test without any network or HTML involved."""
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(max_words - overlap, 1)
    for start in range(0, len(words), step):
        chunk_words = words[start:start + max_words]
        if not chunk_words:
            break
        chunks.append(" ".join(chunk_words))
        if start + max_words >= len(words):
            break
    return chunks


def build_filing_chunks(ticker, sections, filing_date, url, limit=MAX_CHUNKS_PER_FILING):
    chunks = []
    for label, text in sections.items():
        for piece in chunk_text(text):
            chunks.append({
                "id": f"{ticker}-10K-{len(chunks)}",
                "ticker": ticker,
                "source_type": "filing",
                "section": label,
                "title": f"{ticker} 10-K ({filing_date}) - {label}",
                "summary": piece,
                "published": filing_date,
                "link": url,
            })
            if len(chunks) >= limit:
                return chunks
    return chunks


PREVIEW_CHARS = 220
SUSPICIOUSLY_SHORT_SECTION = 1200  # below this, print a heads-up even on a "successful" match


def describe_sections(ticker, sections):
    """Prints a quick eyeball-check for each extracted section: whether it
    hit the real section or the generic fallback, its length, and a short
    text preview — since this sandbox can never verify the regex heuristic
    against real SEC filings, this is what makes that verification fast
    and visual instead of blind trust the first time you run this live."""
    if "Filing excerpt" in sections:
        print(f"  [!] {ticker}: could not confidently locate Item 1A or Item 7 — "
              f"falling back to the filing's opening text instead. Worth opening "
              f"the actual filing and checking this ticker's structure.")

    for label, text in sections.items():
        flag = ""
        if label != "Filing excerpt" and len(text) < SUSPICIOUSLY_SHORT_SECTION:
            flag = "  [!] shorter than expected — double check this one"
        preview = text[:PREVIEW_CHARS].replace("\n", " ").strip()
        print(f"    - {label} ({len(text)} chars){flag}\n      \"{preview}...\"")


def fetch_filing_chunks_for_ticker(ticker, cik_map, headers):
    """The single-ticker version of the pipeline main() runs in a loop —
    pulled out so the web app can call it for just one newly-added ticker
    without re-fetching everyone else's filings. Returns [] (rather than
    raising) if this ticker has no CIK or no 10-K on file, since that's a
    normal, expected outcome, not a failure."""
    cik = cik_map.get(ticker) or FALLBACK_CIKS.get(ticker)
    if not cik:
        print(f"  No CIK found for {ticker}, skipping.")
        return []

    time.sleep(0.2)  # be polite to SEC's servers
    latest = get_latest_10k(cik, headers)
    if not latest:
        print(f"  No 10-K found for {ticker}.")
        return []

    url = build_filing_url(cik, latest["accessionNumber"], latest["primaryDocument"])
    time.sleep(0.2)
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    full_text = html_to_text(resp.text)
    sections = extract_sections(full_text)
    chunks = build_filing_chunks(ticker, sections, latest["filingDate"], url)
    print(f"{ticker}: {len(chunks)} chunks from {', '.join(sections.keys())} "
          f"(filed {latest['filingDate']})")
    describe_sections(ticker, sections)
    return chunks


def main():
    try:
        headers = edgar_headers()
    except EdgarConfigError as e:
        raise SystemExit(str(e))

    watchlist = load_watchlist()

    print("Fetching ticker -> CIK map...")
    cik_map = get_cik_map(headers)

    all_chunks = []
    for entry in watchlist:
        ticker = entry["ticker"]
        try:
            all_chunks.extend(fetch_filing_chunks_for_ticker(ticker, cik_map, headers))
        except Exception as e:
            print(f"  Could not fetch 10-K for {ticker}: {e}")

    with open(FILINGS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2)

    print(f"\nSaved {len(all_chunks)} filing chunks to {FILINGS_CACHE_FILE}")


if __name__ == "__main__":
    main()
