"""Background processing queue.

A single worker thread processes uploads one at a time, in arrival order. One writer
means two overlapping uploads can never race on the same order ids. The upload request
returns immediately; the browser polls the upload's status.

Scale-out path: replace this in-process queue with SQS + workers (or S3 event → Lambda/Glue).
The processing function itself does not change.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from app.config import get_settings
from app.db import new_session
from app.repositories import uploads as repo
from app.services.ingest import process_upload

log = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")
    return _executor


def enqueue(upload_id: str) -> Future | None:
    if get_settings().job_mode == "sync":
        process_upload(upload_id)
        return None
    log.info("Queued upload %s for processing", upload_id)
    return _get_executor().submit(process_upload, upload_id)


def resume_pending() -> int:
    """On startup, re-queue uploads interrupted by a restart or deploy."""
    session = new_session()
    try:
        ids = repo.pending_upload_ids(session)
    finally:
        session.close()
    for upload_id in ids:
        enqueue(upload_id)
    if ids:
        log.info("Resumed %d interrupted upload(s)", len(ids))
    return len(ids)


def shutdown(wait: bool = True) -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None
