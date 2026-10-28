"""Private object storage (S3 / MinIO). Objects are never public; share via short-lived signed URLs."""

from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings

_MISSING_CODES = {"NoSuchKey", "404", "NotFound"}


def user_prefix(user_id: object) -> str:
    """All of a user's objects live under this prefix, so deletion can sweep it."""
    return f"users/{user_id}/"


class Storage:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self.bucket = bucket

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    def get_bytes(self, key: str) -> bytes | None:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_CODES:
                return None
            raise

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_CODES:
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under prefix. Returns how many were removed."""
        removed = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if not objects:
                continue
            result = self._client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": objects, "Quiet": True}
            )
            if result.get("Errors"):
                raise RuntimeError(f"failed to delete {len(result['Errors'])} objects")
            removed += len(objects)
        return removed

    def presign_get(self, key: str, expires_s: int = 300) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_s,
        )


def make_storage(bucket: str | None = None) -> Storage:
    s = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return Storage(client, bucket or s.s3_bucket)


@lru_cache
def get_storage() -> Storage:
    """FastAPI dependency (override in tests)."""
    return make_storage()
