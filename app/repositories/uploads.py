"""Data access for uploads and their processed results.

Only this module (and the analytics queries) talk SQL; services call these functions.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import Order, QualityIssue, RejectedRow, Upload, UploadStatus
from app.pipeline import PipelineResult

log = logging.getLogger(__name__)

CHUNK = 1000  # rows per multi-row INSERT / IN (...) query


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_upload(session: Session, upload_id: str) -> Upload | None:
    return session.get(Upload, upload_id)


def get_upload_by_hash(session: Session, file_hash: str) -> Upload | None:
    return session.scalar(select(Upload).where(Upload.file_hash == file_hash))


def list_uploads(session: Session, limit: int = 50) -> list[Upload]:
    return list(session.scalars(select(Upload).order_by(Upload.created_at.desc()).limit(limit)))


def pending_upload_ids(session: Session) -> list[str]:
    """Uploads interrupted by a restart (still QUEUED/PROCESSING)."""
    stmt = (select(Upload.id)
            .where(Upload.status.in_([UploadStatus.QUEUED, UploadStatus.PROCESSING]))
            .order_by(Upload.created_at))
    return list(session.scalars(stmt))


def existing_order_keys(session: Session, keys: Iterable[str]) -> set[str]:
    """Which of these order ids are already stored? Queried in chunks to keep IN() small."""
    keys = list(keys)
    found: set[str] = set()
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i:i + CHUNK]
        found.update(session.scalars(select(Order.order_id).where(Order.order_id.in_(chunk))))
    return found


def set_stage(session: Session, upload: Upload, status: str, stage: str) -> None:
    upload.status = status
    upload.stage = stage
    if status == UploadStatus.PROCESSING and upload.started_at is None:
        upload.started_at = _now()
    session.commit()


def mark_failed(session: Session, upload_id: str, message: str) -> None:
    """Record failure in its own transaction (the load transaction has rolled back)."""
    upload = session.get(Upload, upload_id)
    if upload is None:
        return
    upload.status = UploadStatus.FAILED
    upload.stage = "failed"
    upload.error_message = message[:2000]
    upload.completed_at = _now()
    session.commit()


def reset_for_retry(session: Session, upload: Upload) -> None:
    """Clear any results of a previous attempt so a retry starts clean."""
    for model in (QualityIssue, RejectedRow, Order):
        session.execute(delete(model).where(model.upload_id == upload.id))
    upload.status = UploadStatus.QUEUED
    upload.stage = "queued"
    upload.error_message = None
    upload.started_at = upload.completed_at = None
    upload.total_rows = upload.valid_rows = upload.invalid_rows = upload.duplicate_rows = None
    upload.success_rate = upload.quality_score = None
    session.commit()


def _records(df: pd.DataFrame, columns: list[str]) -> list[dict]:
    """pandas -> plain Python values (NA/NaN -> None) safe for the DB driver."""
    sub = df[columns].astype(object)
    sub = sub.where(pd.notna(sub), None)
    return sub.to_dict("records")


def save_results(session: Session, upload: Upload, result: PipelineResult,
                 order_columns: list[str], processed_key: str, rejected_key: str) -> None:
    """Persist everything for one upload. The caller owns the transaction (commit/rollback),
    so either all rows land or none do."""
    rows = _records(result.clean, order_columns)
    for r in rows:
        r["upload_id"] = upload.id
    for i in range(0, len(rows), CHUNK):
        session.execute(insert(Order), rows[i:i + CHUNK])

    rejected = [{"upload_id": upload.id, "row_number": r["row_number"], "raw_data": r["raw"],
                 "reasons": "; ".join(r["reasons"])} for r in result.rejected]
    for i in range(0, len(rejected), CHUNK):
        session.execute(insert(RejectedRow), rejected[i:i + CHUNK])

    issues = [{"upload_id": upload.id, "column_name": c, "issue_type": t, "issue_count": n}
              for (c, t), n in result.issues.items()]
    if issues:
        session.execute(insert(QualityIssue), issues)

    upload.total_rows = result.total_rows
    upload.valid_rows = result.valid_rows
    upload.invalid_rows = result.invalid_rows
    upload.duplicate_rows = result.duplicate_rows
    upload.success_rate = result.success_rate
    upload.quality_score = result.quality_score
    upload.processed_key = processed_key
    upload.rejected_key = rejected_key
    upload.status = UploadStatus.COMPLETED
    upload.stage = "completed"
    upload.completed_at = _now()
    log.info("Saved upload %s: %d orders, %d rejected, %d issue types",
             upload.id, len(rows), len(rejected), len(issues))
