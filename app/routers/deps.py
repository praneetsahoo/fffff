"""Shared request dependencies."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Query

from app.errors import AppError
from app.schemas import RecordFilters


def record_filters(
    q: Annotated[str | None, Query(max_length=100, description="Search order id, product, city")] = None,
    region: Annotated[list[str], Query()] = [],
    category: Annotated[list[str], Query()] = [],
    status: Annotated[list[str], Query()] = [],
    city: Annotated[list[str], Query()] = [],
    date_from: date | None = None,
    date_to: date | None = None,
    sla_breached: bool | None = None,
    upload_id: Annotated[str | None, Query(max_length=36)] = None,
) -> RecordFilters:
    if date_from and date_to and date_from > date_to:
        raise AppError("'date_from' must be on or before 'date_to'.", code="invalid_filter",
                       status_code=422)
    clean = lambda vals: [v.strip() for v in vals if v and v.strip()][:50]  # noqa: E731
    return RecordFilters(q=q.strip() if q and q.strip() else None, region=clean(region),
                         category=clean(category), status=clean(status), city=clean(city),
                         date_from=date_from, date_to=date_to, sla_breached=sla_breached,
                         upload_id=upload_id)
