"""Relational schema.

uploads         one row per uploaded file: lineage (S3 keys), status, quality counts
orders          clean, typed, de-duplicated business records (+ derived metrics)
rejected_rows   every row that failed validation, with ALL reasons (nothing silently dropped)
quality_issues  per-column issue counts for the data-quality report
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UploadStatus:
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    original_filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), unique=True)  # blocks re-loading same file
    file_size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=UploadStatus.QUEUED, index=True)
    stage: Mapped[str | None] = mapped_column(String(32))  # human-readable progress

    raw_key: Mapped[str] = mapped_column(String(512))
    processed_key: Mapped[str | None] = mapped_column(String(512))
    rejected_key: Mapped[str | None] = mapped_column(String(512))

    total_rows: Mapped[int | None] = mapped_column(Integer)
    valid_rows: Mapped[int | None] = mapped_column(Integer)
    invalid_rows: Mapped[int | None] = mapped_column(Integer)
    duplicate_rows: Mapped[int | None] = mapped_column(Integer)
    success_rate: Mapped[float | None] = mapped_column(Numeric(5, 2, asdecimal=False))
    quality_score: Mapped[float | None] = mapped_column(Numeric(5, 2, asdecimal=False))
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    orders: Mapped[list[Order]] = relationship(back_populates="upload")


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"), index=True)

    order_date: Mapped[date] = mapped_column(Date)
    region: Mapped[str] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(64))
    product: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False))
    status: Mapped[str] = mapped_column(String(16))
    delivery_days: Mapped[int | None] = mapped_column(Integer)

    # Derived metrics, computed by the pipeline
    revenue: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False))
    order_month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    sla_breached: Mapped[bool | None] = mapped_column(Boolean)

    upload: Mapped[Upload] = relationship(back_populates="orders")

    __table_args__ = (
        Index("ix_orders_order_date", "order_date"),
        Index("ix_orders_region_category", "region", "category"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_month", "order_month"),
    )


class RejectedRow(Base):
    __tablename__ = "rejected_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)  # 1-based line in the CSV (header = 1)
    kind: Mapped[str] = mapped_column(String(16), default="invalid")  # invalid|duplicate|malformed
    raw_data: Mapped[dict] = mapped_column(JSON)
    reasons: Mapped[str] = mapped_column(Text)  # "; "-joined list of every failed rule

    __table_args__ = (Index("ix_rejected_upload_kind_row", "upload_id", "kind", "row_number"),)


class QualityIssue(Base):
    __tablename__ = "quality_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id"), index=True)
    column_name: Mapped[str] = mapped_column(String(64))
    issue_type: Mapped[str] = mapped_column(String(32))
    # missing | invalid_type | invalid_value | duplicate | standardized | filled
    issue_count: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("upload_id", "column_name", "issue_type",
                                       name="uq_quality_issue"),)
