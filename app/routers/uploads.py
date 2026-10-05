"""Upload endpoints: submit, track, retry, quality report, rejected rows, file downloads."""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.errors import InvalidFileError, NotFoundError
from app.models import QualityIssue, RejectedRow, Upload
from app.repositories import uploads as repo
from app.repositories.analytics import pages
from app.schemas import Page, QualityIssueOut, QualityReport, RejectedRowOut, UploadOut
from app.services import ingest, jobs
from app.storage import get_storage

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
log = logging.getLogger(__name__)
DbSession = Annotated[Session, Depends(get_session)]


def _get_or_404(session: Session, upload_id: str) -> Upload:
    upload = repo.get_upload(session, upload_id)
    if upload is None:
        raise NotFoundError("Upload not found.")
    return upload


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """Read at most limit+1 bytes so an oversized upload is rejected without buffering it all."""
    chunks, size = [], 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise InvalidFileError(
                f"The file is larger than the {get_settings().max_upload_mb} MB limit.",
                code="file_too_large", status_code=413)
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=UploadOut, status_code=202,
             summary="Upload a CSV dataset; processing starts in the background")
async def create_upload(session: DbSession, file: Annotated[UploadFile, File()]) -> Upload:
    data = await _read_limited(file, get_settings().max_upload_bytes)
    upload = ingest.accept_upload(session, file.filename or "upload.csv", data)
    jobs.enqueue(upload.id)
    session.refresh(upload)
    return upload


@router.get("", response_model=list[UploadOut], summary="Upload history (newest first)")
def list_uploads(session: DbSession,
                 limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[Upload]:
    return repo.list_uploads(session, limit)


@router.get("/{upload_id}", response_model=UploadOut, summary="Upload status and counts")
def get_upload(upload_id: str, session: DbSession) -> Upload:
    return _get_or_404(session, upload_id)


@router.post("/{upload_id}/retry", response_model=UploadOut, status_code=202,
             summary="Retry a failed upload from its stored raw file")
def retry(upload_id: str, session: DbSession) -> Upload:
    upload = ingest.retry_upload(session, upload_id)
    jobs.enqueue(upload.id)
    session.refresh(upload)
    return upload


@router.get("/{upload_id}/quality", response_model=QualityReport,
            summary="Data-quality report for one upload")
def quality(upload_id: str, session: DbSession) -> QualityReport:
    upload = _get_or_404(session, upload_id)
    issues = session.scalars(select(QualityIssue).where(QualityIssue.upload_id == upload_id)
                             .order_by(QualityIssue.issue_count.desc())).all()
    by_type: dict[str, int] = {}
    for i in issues:
        by_type[i.issue_type] = by_type.get(i.issue_type, 0) + i.issue_count
    return QualityReport(
        upload=UploadOut.model_validate(upload),
        issues=[QualityIssueOut(column=i.column_name, issue_type=i.issue_type, count=i.issue_count)
                for i in issues],
        issues_by_type=by_type,
        rejected_by_kind={"invalid": upload.invalid_rows or 0,
                          "duplicate": upload.duplicate_rows or 0},
    )


@router.get("/{upload_id}/rejected", response_model=Page[RejectedRowOut],
            summary="Rejected rows with every reason, paginated")
def rejected(upload_id: str, session: DbSession,
             page: Annotated[int, Query(ge=1)] = 1,
             page_size: Annotated[int, Query(ge=1, le=100)] = 25,
             kind: Literal["all", "invalid", "duplicate"] = "all") -> Page[RejectedRowOut]:
    _get_or_404(session, upload_id)
    base = select(RejectedRow).where(RejectedRow.upload_id == upload_id)
    if kind == "duplicate":
        base = base.where(RejectedRow.kind == "duplicate")
    elif kind == "invalid":  # invalid values + malformed lines
        base = base.where(RejectedRow.kind != "duplicate")
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(base.order_by(RejectedRow.row_number)
                           .offset((page - 1) * page_size).limit(page_size)).all()
    return Page[RejectedRowOut](
        items=[RejectedRowOut(row_number=r.row_number, kind=r.kind, reasons=r.reasons.split("; "),
                              raw_data=r.raw_data) for r in rows],
        total=total, page=page, page_size=page_size, pages=pages(total, page_size))


@router.get("/{upload_id}/files/{kind}", summary="Download the raw, processed or rejected CSV",
            response_class=Response)
def download(upload_id: str, kind: Literal["raw", "processed", "rejected"],
             session: DbSession) -> Response:
    upload = _get_or_404(session, upload_id)
    key = {"raw": upload.raw_key, "processed": upload.processed_key,
           "rejected": upload.rejected_key}[kind]
    if not key:
        raise NotFoundError(f"The {kind} file is not available for this upload yet.")
    data = get_storage().get(key)
    stem = upload.original_filename.rsplit(".", 1)[0]
    name = upload.original_filename if kind == "raw" else f"{stem}_{kind}.csv"
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
