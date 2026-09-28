"""
Storage abstraction for the handful of JSON "database" files this app
reads and rewrites (watchlist.json, price_log.json, news_cache.json,
filings_cache.json, embeddings_cache.json).

Locally, these are just files on disk, same as always. Inside AWS Lambda
the filesystem is read-only outside of /tmp, and /tmp isn't shared across
concurrent invocations, so the same file-based approach can't work there
— this module swaps in DynamoDB (for the four files small enough to fit
a single item, under DynamoDB's 400KB limit) and S3 (for
embeddings_cache.json, which runs a few MB) when it detects it's running
in Lambda. Every other module just calls load_json()/save_json() and
doesn't need to know which backend is in play.

One-time setup before a Lambda deploy: run seed_cloud_storage.py once to
copy whatever's already in the local JSON files up into DynamoDB/S3, so
the live app starts with real data instead of empty caches.
"""
import json
import os

IS_LAMBDA = "AWS_LAMBDA_FUNCTION_NAME" in os.environ

# Small enough (under DynamoDB's 400KB per-item limit) to store as a
# single item's worth of JSON text.
_DYNAMO_FILES = {"watchlist.json", "price_log.json", "news_cache.json", "filings_cache.json"}
# Too big for a DynamoDB item (embeddings_cache.json runs a few MB) —
# these go to S3 instead.
_S3_FILES = {"embeddings_cache.json"}

_table = None
_s3 = None
_bucket = None

if IS_LAMBDA:
    import boto3

    # AWS_REGION is set automatically by the Lambda runtime; the explicit
    # fallback just makes this importable outside Lambda too (e.g. for a
    # smoke test) without needing a region configured some other way.
    _region = os.environ.get("AWS_REGION", "us-east-1")
    _dynamodb = boto3.resource("dynamodb", region_name=_region)
    _table = _dynamodb.Table(os.environ.get("DYNAMODB_TABLE", "stock-watcher-data"))
    _s3 = boto3.client("s3", region_name=_region)
    _bucket = os.environ.get("S3_BUCKET")


def load_json(filename, default=None):
    """Load and parse the JSON previously saved under `filename` (e.g.
    'watchlist.json'). Returns `default` if nothing's been saved yet."""
    if not IS_LAMBDA:
        if not os.path.exists(filename):
            return default
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    if filename in _S3_FILES:
        try:
            obj = _s3.get_object(Bucket=_bucket, Key=filename)
            return json.loads(obj["Body"].read())
        except _s3.exceptions.NoSuchKey:
            return default
    else:
        resp = _table.get_item(Key={"file": filename})
        item = resp.get("Item")
        return json.loads(item["data"]) if item else default


def save_json(filename, data):
    """Serialize `data` as JSON and save it under `filename`, replacing
    whatever was there before (same whole-file-replace semantics as the
    original file-based code)."""
    if not IS_LAMBDA:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return

    if filename in _S3_FILES:
        _s3.put_object(Bucket=_bucket, Key=filename, Body=json.dumps(data).encode("utf-8"))
    else:
        _table.put_item(Item={"file": filename, "data": json.dumps(data)})


def exists(filename, default=None):
    """True if `filename` has ever been saved. Used where the app only
    needs to know readiness (e.g. 'has the index been built yet?') rather
    than the content itself."""
    return load_json(filename, default=default) is not None
