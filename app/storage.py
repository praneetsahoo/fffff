"""Object storage abstraction: S3 in production, local folder in development.

Key layout (same in both backends) keeps raw and processed data clearly separated:
    raw/{upload_id}/{filename}          original file, never modified
    processed/{upload_id}/clean.csv     cleaned, typed output
    rejected/{upload_id}/rejected.csv   rejected rows with reasons
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings
from app.errors import StorageError

log = logging.getLogger(__name__)


def raw_key(upload_id: str, filename: str) -> str:
    return f"raw/{upload_id}/{filename}"


def processed_key(upload_id: str) -> str:
    return f"processed/{upload_id}/clean.csv"


def rejected_key(upload_id: str) -> str:
    return f"rejected/{upload_id}/rejected.csv"


class Storage(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str = "text/csv") -> None: ...
    def get(self, key: str) -> bytes: ...
    def health(self) -> bool: ...


class LocalStorage:
    name = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:  # block path traversal via crafted keys
            raise StorageError("Invalid storage key.")
        return path

    def put(self, key: str, data: bytes, content_type: str = "text/csv") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        log.info("Stored %d bytes at local:%s", len(data), key)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"Object not found: {key}", status_code=404, code="not_found")
        return path.read_bytes()

    def health(self) -> bool:
        return self.root.is_dir()


class S3Storage:
    name = "s3"

    def __init__(self, bucket: str, region: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        # Credentials come from the EC2 instance role — never from code or env files.
        self.client = boto3.client(
            "s3", region_name=region,
            config=Config(retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=5, read_timeout=30),
        )

    def put(self, key: str, data: bytes, content_type: str = "text/csv") -> None:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                   ContentType=content_type, ServerSideEncryption="AES256")
        except (BotoCoreError, ClientError) as exc:
            log.error("S3 put failed for %s: %s", key, exc.__class__.__name__)
            raise StorageError("Could not save the file to cloud storage.") from exc
        log.info("Stored %d bytes at s3://%s/%s", len(data), self.bucket, key)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise StorageError(f"Object not found: {key}", status_code=404,
                                   code="not_found") from exc
            raise StorageError("Could not read from cloud storage.") from exc
        except BotoCoreError as exc:
            raise StorageError("Could not read from cloud storage.") from exc

    def health(self) -> bool:
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except (BotoCoreError, ClientError):
            return False


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        if not s.s3_bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 requires S3_BUCKET.")
        return S3Storage(s.s3_bucket, s.aws_region)
    return LocalStorage(s.local_storage_dir)
