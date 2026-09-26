from types import SimpleNamespace

import build_index


def test_doc_text_works_for_news_shape():
    doc = {"title": "Apple headline", "summary": "Apple news body"}
    assert build_index.doc_text(doc) == "Apple headline. Apple news body"


def test_doc_text_works_for_filing_shape():
    doc = {"title": "AAPL 10-K excerpt", "summary": "Apple risk factor text"}
    assert build_index.doc_text(doc) == "AAPL 10-K excerpt. Apple risk factor text"


def test_load_cache_missing_file_returns_empty_list(tmp_path):
    assert build_index.load_cache(str(tmp_path / "nope.json")) == []


def test_load_cache_reads_existing_file(tmp_path):
    import json
    f = tmp_path / "data.json"
    f.write_text(json.dumps([{"a": 1}]))
    assert build_index.load_cache(str(f)) == [{"a": 1}]


def test_embed_docs_attaches_embeddings(monkeypatch):
    def fake_create(model, input):
        return SimpleNamespace(data=[SimpleNamespace(embedding=[float(len(t)), 0.0]) for t in input])

    monkeypatch.setattr(build_index.client.embeddings, "create", fake_create)

    docs = [{"title": "A", "summary": "short"}, {"title": "BB", "summary": "longer text here"}]
    result = build_index.embed_docs(docs)

    assert all("embedding" in d for d in result)
    assert result is docs  # mutates in place and returns the same list


def test_embed_docs_empty_input_skips_api_call(monkeypatch):
    calls = []
    monkeypatch.setattr(build_index.client.embeddings, "create",
                         lambda **kw: calls.append(kw) or SimpleNamespace(data=[]))
    assert build_index.embed_docs([]) == []
    assert calls == []
