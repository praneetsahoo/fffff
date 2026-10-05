"""API response/request models (the contract between backend and frontend)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


# ---------- uploads ----------

class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    file_size_bytes: int
    status: str
    stage: str | None
    total_rows: int | None
    valid_rows: int | None
    invalid_rows: int | None
    duplicate_rows: int | None
    success_rate: float | None
    quality_score: float | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    raw_key: str
    processed_key: str | None
    rejected_key: str | None


class QualityIssueOut(BaseModel):
    column: str
    issue_type: str
    count: int


class QualityReport(BaseModel):
    upload: UploadOut
    issues: list[QualityIssueOut]
    issues_by_type: dict[str, int]
    rejected_by_kind: dict[str, int]


class RejectedRowOut(BaseModel):
    row_number: int
    kind: str
    reasons: list[str]
    raw_data: dict


# ---------- records ----------

class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    order_date: date
    region: str
    state: str | None
    city: str
    category: str
    product: str
    quantity: int
    unit_price: float
    status: str
    delivery_days: int | None
    revenue: float
    order_month: str
    sla_breached: bool | None
    upload_id: str


SortField = Literal["order_date", "order_id", "revenue", "quantity", "unit_price", "city",
                    "region", "category", "status", "delivery_days", "product"]


class RecordFilters(BaseModel):
    q: str | None = Field(None, max_length=100, description="Search order id, product or city")
    region: list[str] = []
    category: list[str] = []
    status: list[str] = []
    city: list[str] = []
    date_from: date | None = None
    date_to: date | None = None
    sla_breached: bool | None = None
    upload_id: str | None = None


# ---------- dashboard ----------

class Kpis(BaseModel):
    orders: int
    revenue: float
    units: int
    avg_order_value: float
    delivered_pct: float
    cancel_return_pct: float
    avg_delivery_days: float | None
    sla_breach_pct: float | None


class DataQualityKpis(BaseModel):
    uploads_completed: int
    uploads_failed: int
    records_received: int
    records_valid: int
    records_invalid: int
    records_duplicate: int
    success_rate: float | None


class SeriesPoint(BaseModel):
    label: str
    value: float
    count: int | None = None


class Dashboard(BaseModel):
    kpis: Kpis
    data_quality: DataQualityKpis
    monthly: list[dict]           # [{month, revenue, orders}]
    by_category: list[SeriesPoint]
    by_region: list[SeriesPoint]
    by_status: list[SeriesPoint]
    top_cities: list[SeriesPoint]
    sla_by_region: list[dict]     # [{region, delivered, breached, breach_pct}]
    date_range: dict


class Insight(BaseModel):
    kind: Literal["positive", "warning", "info"]
    title: str
    detail: str
    metric: str | None = None


class Meta(BaseModel):
    profile: str
    title: str
    description: str
    required_columns: list[str]
    columns: list[str]
    max_upload_mb: int
    sla_days: int
    filter_options: dict[str, list[str]]
    date_range: dict
