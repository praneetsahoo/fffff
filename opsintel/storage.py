"""File storage: Amazon S3 on AWS (boto3), a local folder during development.

Folder layout keeps raw and processed data clearly separate:
    raw/<upload>/<file name>          the original file, never changed
    processed/<upload>/clean.csv      cleaned rows that were loaded into RDS
    rejected/<upload>/rejected.csv    refused rows with the reason for each
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from opsintel import config

log = logging.getLogger(__name__)


@lru_cache
def _s3():
    import boto3   # credentials come from the EC2 instance role, never from code
    return boto3.client("s3", region_name=config.REGION)


def using_s3() -> bool:
    return bool(config.S3_BUCKET)


def save(key: str, data: bytes) -> None:
    if using_s3():
        _s3().put_object(Bucket=config.S3_BUCKET, Key=key, Body=data, ServerSideEncryption="AES256")
        log.info("Saved s3://%s/%s (%d bytes)", config.S3_BUCKET, key, len(data))
    else:
        path = Path(config.LOCAL_STORAGE_DIR) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        log.info("Saved %s (%d bytes)", path, len(data))


def load(key: str) -> bytes:
    if using_s3():
        return _s3().get_object(Bucket=config.S3_BUCKET, Key=key)["Body"].read()
    return (Path(config.LOCAL_STORAGE_DIR) / key).read_bytes()


def is_available() -> bool:
    """Health check shown in the sidebar."""
    try:
        if using_s3():
            _s3().head_bucket(Bucket=config.S3_BUCKET)
        else:
            Path(config.LOCAL_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
        return True
    except Exception:  # any failure simply means "unavailable" for the health indicator
        log.exception("Storage health check failed")
        return False
