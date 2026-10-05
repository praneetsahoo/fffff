"""Upload ingestion and processing — the business workflow.

    accept_upload:  quick checks → SHA-256 → duplicate-file check → raw file to S3 → job QUEUED
    process_upload: raw from S3 → pipeline → clean/rejected CSVs to S3 → ONE DB transaction
    retry_upload:   FAILED → cleared → QUEUED again (raw file is still in S3)
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import new_session
from app.errors import (AppError, DuplicateUploadError, InvalidFileError, NotFoundError,
                        StorageError)
from app.models import Upload, UploadStatus
from app.pipeline import clean_csv_bytes, rejected_csv_bytes, run_pipeline
from app.pipeline.pipeline import DERIVED_COLUMNS
from app.profile import load_profile
from app.repositories import uploads as repo
from app.storage import get_storage, processed_key, raw_key, rejected_key

log = logging.getLogger(__name__)


def safe_filename(name: str) -> str:
    """Keep only the base name and safe characters (used inside the S3 key)."""
    base = re.split(r"[\\/]", name or "")[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:120]
    return base or "upload.csv"


def accept_upload(session: Session, filename: str, data: bytes) -> Upload:
    settings = get_settings()
    name = safe_filename(filename)

    # Cheap checks first so obviously bad files never reach storage.
    if not name.lower().endswith(".csv"):
        raise InvalidFileError(f"Unsupported file type '{name}'. Please upload a .csv file.",
                               code="unsupported_format", status_code=415)
    if not data or not data.strip():
        raise InvalidFileError("The file is empty.", code="empty_file")
    if len(data) > settings.max_upload_bytes:
        raise InvalidFileError(f"The file is larger than the {settings.max_upload_mb} MB limit.",
                               code="file_too_large", status_code=413)

    file_hash = hashlib.sha256(data).hexdigest()
    existing = repo.get_upload_by_hash(session, file_hash)
    if existing is not None:
        raise DuplicateUploadError(
            f"This exact file was already uploaded as '{existing.original_filename}'.",
            details={"upload_id": existing.id, "status": existing.status})

    upload_id = str(uuid.uuid4())
    key = raw_key(upload_id, name)
    get_storage().put(key, data)  # raw copy is stored BEFORE any processing

    upload = Upload(id=upload_id, original_filename=name, file_hash=file_hash,
                    file_size_bytes=len(data), status=UploadStatus.QUEUED, stage="queued",
                    raw_key=key)
    session.add(upload)
    try:
        session.commit()
    except IntegrityError as exc:  # same file submitted twice at the same moment
        session.rollback()
        raise DuplicateUploadError("This exact file is already being processed.") from exc
    log.info("Accepted upload %s (%s, %d bytes) raw=%s", upload_id, name, len(data), key)
    return upload


def process_upload(upload_id: str) -> None:
    """Run the full pipeline for one upload. Never raises: failures are recorded on the upload."""
    session = new_session()
    try:
        _process(session, upload_id)
    except (InvalidFileError, StorageError) as exc:
        session.rollback()
        log.warning("Upload %s failed: %s", upload_id, exc.message)
        repo.mark_failed(session, upload_id, exc.message)
    except IntegrityError:
        session.rollback()
        log.exception("Upload %s hit a uniqueness conflict", upload_id)
        repo.mark_failed(session, upload_id,
                         "Some records conflicted with data saved at the same time. "
                         "Please retry this upload.")
    except SQLAlchemyError:
        session.rollback()
        log.exception("Upload %s database error", upload_id)
        _mark_failed_safely(session, upload_id,
                            "The database was unavailable while saving. No partial data was "
                            "saved; please retry.")
    except Exception:  # last resort: record it, never leave a job stuck in PROCESSING
        session.rollback()
        log.exception("Upload %s crashed during processing", upload_id)
        _mark_failed_safely(session, upload_id,
                            "Processing failed unexpectedly. The original file is kept; "
                            "please retry.")
    finally:
        session.close()


def _mark_failed_safely(session: Session, upload_id: str, message: str) -> None:
    try:
        repo.mark_failed(session, upload_id, message)
    except SQLAlchemyError:
        log.exception("Could not record failure for upload %s (database down?)", upload_id)


def _process(session: Session, upload_id: str) -> None:
    upload = repo.get_upload(session, upload_id)
    if upload is None:
        log.error("Upload %s not found; skipping", upload_id)
        return
    if upload.status == UploadStatus.COMPLETED:
        log.info("Upload %s already completed; skipping", upload_id)
        return

    profile = load_profile(get_settings().dataset_profile)
    storage = get_storage()

    log.info("Processing started for upload %s", upload_id)
    repo.set_stage(session, upload, UploadStatus.PROCESSING, "reading")
    data = storage.get(upload.raw_key)

    repo.set_stage(session, upload, UploadStatus.PROCESSING, "validating")
    result = run_pipeline(
        data, upload.original_filename, profile,
        existing_keys_lookup=lambda keys: repo.existing_order_keys(session, keys),
    )

    repo.set_stage(session, upload, UploadStatus.PROCESSING, "storing outputs")
    p_key, r_key = processed_key(upload_id), rejected_key(upload_id)
    storage.put(p_key, clean_csv_bytes(result, profile))
    storage.put(r_key, rejected_csv_bytes(result, profile))

    repo.set_stage(session, upload, UploadStatus.PROCESSING, "loading database")
    order_columns = profile.column_names + DERIVED_COLUMNS
    repo.save_results(session, upload, result, order_columns, p_key, r_key)
    session.commit()  # single commit: orders + rejects + issues + status, or nothing
    log.info("Processing completed for upload %s: %d valid / %d total",
             upload_id, result.valid_rows, result.total_rows)


def retry_upload(session: Session, upload_id: str) -> Upload:
    upload = repo.get_upload(session, upload_id)
    if upload is None:
        raise NotFoundError("Upload not found.")
    if upload.status != UploadStatus.FAILED:
        raise AppError(f"Only failed uploads can be retried (this one is {upload.status}).",
                       code="not_retryable", status_code=409)
    repo.reset_for_retry(session, upload)
    log.info("Upload %s reset for retry", upload_id)
    return upload
