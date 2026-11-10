"""Smoke test for the object store: create the raw bucket, write one object, read it back.

Run from the repo root with the store up (`docker compose up -d` in infra/):
    uv run --env-file infra/.env python scripts/s3_smoke.py
"""

import os
from datetime import UTC, datetime

import boto3
from botocore.config import Config

BUCKET = "raw"
KEY = "_smoke/hello.txt"

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    region_name="us-east-1",
    config=Config(s3={"addressing_style": "path"}),
)

existing = {b["Name"] for b in s3.list_buckets()["Buckets"]}
if BUCKET in existing:
    print(f"bucket {BUCKET} exists")
else:
    s3.create_bucket(Bucket=BUCKET)
    print(f"created bucket {BUCKET}")

body = f"hello from stat-cave at {datetime.now(UTC).isoformat()}\n"
s3.put_object(Bucket=BUCKET, Key=KEY, Body=body.encode())
head = s3.head_object(Bucket=BUCKET, Key=KEY)
print(f"put {KEY}: {head['ContentLength']} bytes, etag {head['ETag']}")

got = s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read().decode()
assert got == body, "read back differs from what was written"
print("read back matches")

for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
    for obj in page.get("Contents", []):
        print(f"  {obj['Key']}  {obj['Size']} B  {obj['LastModified']:%Y-%m-%d %H:%M:%S}")
