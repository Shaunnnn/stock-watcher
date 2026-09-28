"""storage.py has two backends: local files (the path every other test
in this suite already exercises indirectly) and DynamoDB/S3 for Lambda.
These tests check both directly — the local round trip for real, and the
Lambda path with the boto3 table/client swapped for mocks, since hitting
real AWS isn't something a test suite should do."""
import json
from unittest.mock import MagicMock

import pytest

import storage


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Every local-mode test in this file runs against a scratch
    directory, never the project's real JSON files."""
    monkeypatch.chdir(tmp_path)


def test_is_lambda_false_by_default():
    assert storage.IS_LAMBDA is False


def test_local_save_then_load_round_trips():
    storage.save_json("watchlist.json", [{"ticker": "AAPL"}])
    assert storage.load_json("watchlist.json") == [{"ticker": "AAPL"}]


def test_local_load_missing_file_returns_default():
    assert storage.load_json("nope.json", default=[]) == []
    assert storage.load_json("nope.json") is None


def test_local_exists():
    # default=None (exists()'s own default) is what real callers use —
    # e.g. embeddings_ready() calls exists(EMBEDDINGS_FILE) with no
    # default, relying on "missing file" and "None" meaning the same
    # thing.
    assert storage.exists("watchlist.json") is False
    storage.save_json("watchlist.json", [])
    # an empty list is still "saved" — exists() only means "was it ever
    # written", not "is it non-empty"
    assert storage.exists("watchlist.json") is True


def test_local_save_overwrites_whole_file():
    storage.save_json("price_log.json", [{"a": 1}])
    storage.save_json("price_log.json", [{"b": 2}])
    assert storage.load_json("price_log.json") == [{"b": 2}]


def test_lambda_dynamo_file_round_trips(monkeypatch):
    monkeypatch.setattr(storage, "IS_LAMBDA", True)
    fake_table = MagicMock()
    monkeypatch.setattr(storage, "_table", fake_table)

    storage.save_json("watchlist.json", [{"ticker": "MSFT"}])
    fake_table.put_item.assert_called_once_with(
        Item={"file": "watchlist.json", "data": json.dumps([{"ticker": "MSFT"}])}
    )

    fake_table.get_item.return_value = {
        "Item": {"file": "watchlist.json", "data": json.dumps([{"ticker": "MSFT"}])}
    }
    assert storage.load_json("watchlist.json") == [{"ticker": "MSFT"}]


def test_lambda_dynamo_missing_item_returns_default(monkeypatch):
    monkeypatch.setattr(storage, "IS_LAMBDA", True)
    fake_table = MagicMock()
    fake_table.get_item.return_value = {}
    monkeypatch.setattr(storage, "_table", fake_table)

    assert storage.load_json("watchlist.json", default=[]) == []


def test_lambda_s3_file_round_trips(monkeypatch):
    monkeypatch.setattr(storage, "IS_LAMBDA", True)
    fake_s3 = MagicMock()
    monkeypatch.setattr(storage, "_s3", fake_s3)
    monkeypatch.setattr(storage, "_bucket", "test-bucket")

    storage.save_json("embeddings_cache.json", [{"id": 1}])
    fake_s3.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="embeddings_cache.json",
        Body=json.dumps([{"id": 1}]).encode("utf-8"),
    )


def test_lambda_s3_missing_key_returns_default(monkeypatch):
    monkeypatch.setattr(storage, "IS_LAMBDA", True)
    fake_s3 = MagicMock()

    class NoSuchKey(Exception):
        pass

    fake_s3.exceptions.NoSuchKey = NoSuchKey
    fake_s3.get_object.side_effect = NoSuchKey()
    monkeypatch.setattr(storage, "_s3", fake_s3)
    monkeypatch.setattr(storage, "_bucket", "test-bucket")

    assert storage.load_json("embeddings_cache.json", default=[]) == []
