"""Upload and download a test object in the private MinIO bucket.

Run from /backend:  python -m scripts.minio_smoke_test
"""

import uuid

import boto3
import httpx
from botocore.client import Config

from app.core.config import get_settings


def main() -> None:
    s = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    key = f"smoke-test/{uuid.uuid4()}.txt"
    payload = b"fitcoach smoke test"

    client.put_object(Bucket=s.s3_bucket, Key=key, Body=payload)
    body = client.get_object(Bucket=s.s3_bucket, Key=key)["Body"].read()
    assert body == payload, "downloaded bytes differ from uploaded bytes"
    print("upload/download OK")

    # Bucket must be private: an unsigned request has to be rejected.
    anon = httpx.get(f"{s.s3_endpoint_url}/{s.s3_bucket}/{key}")
    assert anon.status_code in (401, 403), f"object is publicly readable ({anon.status_code})"
    print(f"anonymous access denied ({anon.status_code}) -> bucket is private")

    # Signed short-lived link works.
    url = client.generate_presigned_url(
        "get_object", Params={"Bucket": s.s3_bucket, "Key": key}, ExpiresIn=60
    )
    assert httpx.get(url).content == payload
    print("presigned URL OK")

    client.delete_object(Bucket=s.s3_bucket, Key=key)
    print("cleanup OK")


if __name__ == "__main__":
    main()
