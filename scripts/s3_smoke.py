"""Smoke test for the object store: buckets present, raw versioned, one object round trip.

Checks only. It creates no bucket and changes no setting: a missing bucket, or versioning
off on the raw bucket, is a failure, and scripts/setup_stack.py is what puts it right. The
one thing it writes is its own object, `_smoke/hello.txt` in the raw bucket.

Run from the repo root with the store up (`docker compose up -d` in infra/):
    uv run --env-file infra/.env python scripts/s3_smoke.py
"""

import os
from datetime import UTC, datetime

import boto3
from botocore.config import Config

# Bucket names and region match the AWS buckets. Iceberg metadata stores full
# s3://bucket/key paths, so a table moves between stores without a path rewrite only when
# the bucket name is the same on both sides.
RAW_BUCKET = "statcave-raw"  # files as received; versioned; not a table layer
TABLE_BUCKETS = ["statcave-bronze", "statcave-silver", "statcave-gold"]  # one per layer
REGION = "us-east-2"
KEY = "_smoke/hello.txt"

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    region_name=REGION,
    config=Config(s3={"addressing_style": "path"}),
)

existing = {b["Name"] for b in s3.list_buckets()["Buckets"]}
missing = [b for b in [RAW_BUCKET, *TABLE_BUCKETS] if b not in existing]
if missing:
    raise SystemExit(f"FAIL: missing bucket(s) {missing}; run scripts/setup_stack.py")
print(f"buckets present: {[RAW_BUCKET, *TABLE_BUCKETS]}")

for bucket in [RAW_BUCKET, *TABLE_BUCKETS]:
    status = s3.get_bucket_versioning(Bucket=bucket).get("Status", "off")
    print(f"  versioning on {bucket}: {status}")
if s3.get_bucket_versioning(Bucket=RAW_BUCKET).get("Status") != "Enabled":
    raise SystemExit(f"FAIL: versioning is off on {RAW_BUCKET}; run scripts/setup_stack.py")

body = f"hello from stat-cave at {datetime.now(UTC).isoformat()}\n"
put = s3.put_object(Bucket=RAW_BUCKET, Key=KEY, Body=body.encode())
assert put.get("VersionId"), "raw bucket did not return a version id"
head = s3.head_object(Bucket=RAW_BUCKET, Key=KEY)
print(f"put {KEY}: {head['ContentLength']} bytes, version {put['VersionId']}")

got = s3.get_object(Bucket=RAW_BUCKET, Key=KEY)["Body"].read().decode()
assert got == body, "read back differs from what was written"
print("read back matches")

versions = 0
for page in s3.get_paginator("list_object_versions").paginate(Bucket=RAW_BUCKET, Prefix=KEY):
    versions += len(page.get("Versions", []))
print(f"{KEY} has {versions} stored version(s)")

for page in s3.get_paginator("list_objects_v2").paginate(Bucket=RAW_BUCKET):
    for obj in page.get("Contents", []):
        print(f"  {obj['Key']}  {obj['Size']} B  {obj['LastModified']:%Y-%m-%d %H:%M:%S}")
