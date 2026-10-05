"""Phase 2 foundation tests: app boots, schema exists, storage works, errors are predictable."""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import inspect

from app.errors import StorageError
from app.profile import load_profile
from app.storage import LocalStorage, S3Storage, processed_key, raw_key, rejected_key


def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "storage": "ok"}
    assert r.headers["X-Request-ID"]


def test_schema_created(client):
    from app.db import get_engine

    tables = set(inspect(get_engine()).get_table_names())
    assert {"uploads", "orders", "rejected_rows", "quality_issues"} <= tables


def test_unknown_route_returns_json_error(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_profile_loads_and_is_consistent():
    p = load_profile("operations_orders")
    assert p.key == "order_id"
    assert "order_date" in p.required_columns
    assert "state" not in p.required_columns
    assert p.column("status").allowed and "Delivered" in p.column("status").allowed


def test_missing_profile_fails_fast():
    with pytest.raises(FileNotFoundError):
        load_profile("does_not_exist")


def test_storage_key_layout_separates_raw_and_processed():
    assert raw_key("u1", "f.csv") == "raw/u1/f.csv"
    assert processed_key("u1").startswith("processed/u1/")
    assert rejected_key("u1").startswith("rejected/u1/")


def test_local_storage_roundtrip_and_traversal_blocked(tmp_path):
    s = LocalStorage(str(tmp_path / "store"))
    s.put("raw/u1/a.csv", b"hello")
    assert s.get("raw/u1/a.csv") == b"hello"
    with pytest.raises(StorageError):
        s.put("../../etc/evil", b"x")
    with pytest.raises(StorageError):
        s.get("raw/u1/missing.csv")


@mock_aws
def test_s3_storage_roundtrip(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    region = "ap-southeast-2"
    boto3.client("s3", region_name=region).create_bucket(
        Bucket="test-bucket", CreateBucketConfiguration={"LocationConstraint": region})
    s = S3Storage("test-bucket", region)
    assert s.health()
    s.put("raw/u1/a.csv", b"data")
    assert s.get("raw/u1/a.csv") == b"data"
    with pytest.raises(StorageError) as exc:
        s.get("raw/u1/missing.csv")
    assert exc.value.status_code == 404


@mock_aws
def test_s3_health_false_when_bucket_missing(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    assert S3Storage("no-such-bucket", "ap-southeast-2").health() is False
