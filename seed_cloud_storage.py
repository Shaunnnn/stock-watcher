"""
One-time (or "run again after refreshing local data") script: copies the
current local watchlist.json / price_log.json / news_cache.json /
filings_cache.json / embeddings_cache.json up into DynamoDB and S3, so
the live Lambda deployment starts with real data instead of empty caches.

This does NOT run inside Lambda — it's a local admin tool. It talks to
DynamoDB/S3 directly with boto3 using your local AWS credentials (same as
the AWS CLI), so run `aws configure` first if you haven't.

Usage:
    export DYNAMODB_TABLE=stock-watcher-data   # must match terraform output
    export S3_BUCKET=<your-bucket-name>        # must match terraform output
    export AWS_REGION=us-east-1                # must match terraform.tfvars
    python3 seed_cloud_storage.py
"""
import json
import os

import boto3

DYNAMO_FILES = ["watchlist.json", "price_log.json", "news_cache.json", "filings_cache.json"]
S3_FILES = ["embeddings_cache.json"]


def main():
    region = os.environ.get("AWS_REGION", "us-east-1")
    table_name = os.environ["DYNAMODB_TABLE"]
    bucket = os.environ["S3_BUCKET"]

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    s3 = boto3.client("s3", region_name=region)

    for filename in DYNAMO_FILES:
        if not os.path.exists(filename):
            print(f"  skip {filename} (no local file yet)")
            continue
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        table.put_item(Item={"file": filename, "data": json.dumps(data)})
        print(f"  {filename} -> DynamoDB table {table_name} ({len(json.dumps(data))} bytes)")

    for filename in S3_FILES:
        if not os.path.exists(filename):
            print(f"  skip {filename} (no local file yet)")
            continue
        with open(filename, "rb") as f:
            body = f.read()
        s3.put_object(Bucket=bucket, Key=filename, Body=body)
        print(f"  {filename} -> s3://{bucket}/{filename} ({len(body)} bytes)")

    print("\nDone. The Lambda deployment will now read this data on first request.")


if __name__ == "__main__":
    main()
