"""Phase 4: storage + database integration (runs on SQLite and MySQL)."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import new_session
from app.errors import DuplicateUploadError, InvalidFileError, StorageError
from app.models import Order, QualityIssue, RejectedRow, Upload, UploadStatus
from app.repositories import uploads as repo
from app.services import ingest, jobs
from app.storage import get_storage

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"
HEADER = "order_id,order_date,region,state,city,category,product,quantity,unit_price,status,delivery_days"
GOOD = "ORD-000001,2026-05-01,North,Delhi,Delhi,Grocery,Atta 10kg,2,499.00,Delivered,3"


def csv(*lines: str) -> bytes:
    return ("\n".join([HEADER, *lines]) + "\n").encode()


def upload_and_process(name: str, data: bytes) -> Upload:
    with new_session() as s:
        up = ingest.accept_upload(s, name, data)
        upload_id = up.id
    jobs.enqueue(upload_id)  # sync mode in tests
    with new_session() as s:
        return repo.get_upload(s, upload_id)


def count(model, **where) -> int:
    with new_session() as s:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return s.scalar(stmt)


def test_demo_file_end_to_end(db_env):
    up = upload_and_process("operations_orders.csv",
                            (SAMPLES / "operations_orders.csv").read_bytes())
    assert up.status == UploadStatus.COMPLETED, up.error_message
    assert up.total_rows == 5150
    assert up.total_rows == up.valid_rows + up.invalid_rows + up.duplicate_rows
    assert count(Order, upload_id=up.id) == up.valid_rows
    assert count(RejectedRow, upload_id=up.id) == up.invalid_rows + up.duplicate_rows
    assert count(QualityIssue, upload_id=up.id) > 5
    assert 90 < up.success_rate < 97

    storage = get_storage()  # raw kept + processed + rejected written
    assert storage.get(up.raw_key).startswith(b"order_id")
    assert storage.get(up.processed_key).splitlines()[0].endswith(b"sla_breached")
    assert b"reasons" in storage.get(up.rejected_key).splitlines()[0]

    with new_session() as s:  # typed values round-trip through the DB
        o = s.scalar(select(Order).where(Order.upload_id == up.id).limit(1))
        assert o.order_date.year == 2026 and isinstance(o.quantity, int)
        assert o.revenue == pytest.approx(o.quantity * o.unit_price, abs=0.01)


def test_second_batch_adds_records(db_env):
    a = upload_and_process("a.csv", (SAMPLES / "operations_orders.csv").read_bytes())
    b = upload_and_process("b.csv", (SAMPLES / "operations_orders_batch2.csv").read_bytes())
    assert b.status == UploadStatus.COMPLETED
    assert count(Order) == a.valid_rows + b.valid_rows


def test_same_file_twice_is_blocked(db_env):
    data = csv(GOOD)
    upload_and_process("a.csv", data)
    with new_session() as s, pytest.raises(DuplicateUploadError) as exc:
        ingest.accept_upload(s, "renamed.csv", data)
    assert exc.value.details["upload_id"]


def test_records_already_loaded_are_duplicates(db_env):
    upload_and_process("a.csv", csv(GOOD))
    b = upload_and_process("b.csv", csv(GOOD, GOOD.replace("ORD-000001", "ORD-000002")))
    assert (b.valid_rows, b.duplicate_rows) == (1, 1)
    with new_session() as s:
        rej = s.scalar(select(RejectedRow).where(RejectedRow.upload_id == b.id))
        assert "previous upload" in rej.reasons
    assert count(Order) == 2


@pytest.mark.parametrize("name,data,code", [
    ("notes.txt", b"hello", "unsupported_format"),
    ("e.csv", b"", "empty_file"),
    ("big.csv", b"x" * (11 * 1024 * 1024), "file_too_large"),
])
def test_rejected_before_storage(db_env, name, data, code):
    with new_session() as s, pytest.raises(InvalidFileError) as exc:
        ingest.accept_upload(s, name, data)
    assert exc.value.code == code
    assert count(Upload) == 0


def test_unusable_file_fails_with_message_and_keeps_raw(db_env):
    up = upload_and_process("m.csv", b"order_id,region\nORD-000001,North\n")
    assert up.status == UploadStatus.FAILED
    assert "Missing required column" in up.error_message
    assert get_storage().get(up.raw_key)  # original kept for traceability


def test_db_failure_mid_load_rolls_back_then_retry_succeeds(db_env, monkeypatch):
    real_save = repo.save_results

    def save_then_crash(session, *args, **kwargs):
        real_save(session, *args, **kwargs)  # rows inserted in the open transaction...
        raise OperationalError("INSERT", {}, Exception("connection lost"))  # ...then DB dies

    monkeypatch.setattr(repo, "save_results", save_then_crash)
    up = upload_and_process("a.csv", csv(GOOD, GOOD.replace("ORD-000001", "ORD-000002")))
    assert up.status == UploadStatus.FAILED
    assert "No partial data" in up.error_message
    assert count(Order) == 0 and count(RejectedRow) == 0  # nothing half-written

    monkeypatch.setattr(repo, "save_results", real_save)
    with new_session() as s:
        ingest.retry_upload(s, up.id)
    jobs.enqueue(up.id)
    with new_session() as s:
        again = repo.get_upload(s, up.id)
    assert again.status == UploadStatus.COMPLETED and count(Order) == 2


def test_storage_down_at_upload_creates_nothing(db_env, monkeypatch):
    def boom(*a, **k):
        raise StorageError("Could not save the file to cloud storage.")

    monkeypatch.setattr(type(get_storage()), "put", boom)
    with new_session() as s, pytest.raises(StorageError):
        ingest.accept_upload(s, "a.csv", csv(GOOD))
    assert count(Upload) == 0


def test_retry_only_allowed_for_failed(db_env):
    up = upload_and_process("a.csv", csv(GOOD))
    with new_session() as s, pytest.raises(Exception) as exc:
        ingest.retry_upload(s, up.id)
    assert getattr(exc.value, "code", "") == "not_retryable"


def test_interrupted_uploads_resume_on_startup(db_env):
    with new_session() as s:
        up = ingest.accept_upload(s, "a.csv", csv(GOOD))  # queued, never processed
        upload_id = up.id
    assert jobs.resume_pending() == 1
    with new_session() as s:
        assert repo.get_upload(s, upload_id).status == UploadStatus.COMPLETED


def test_filename_is_sanitised(db_env):
    with new_session() as s:
        up = ingest.accept_upload(s, "../../etc/pa$$wd.csv", csv(GOOD))
    assert up.original_filename == "pa__wd.csv"
    assert up.raw_key == f"raw/{up.id}/pa__wd.csv"
