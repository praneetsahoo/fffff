"""Processed records: search, filter, sort, paginate, export."""
from __future__ import annotations

import csv
import io
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.repositories import analytics as q
from app.routers.deps import record_filters
from app.schemas import OrderOut, Page, RecordFilters, SortField

router = APIRouter(prefix="/api/records", tags=["records"])
DbSession = Annotated[Session, Depends(get_session)]
Filters = Annotated[RecordFilters, Depends(record_filters)]
EXPORT_LIMIT = 50_000
EXPORT_COLUMNS = list(OrderOut.model_fields)


@router.get("", response_model=Page[OrderOut], summary="Search and filter processed records")
def list_records(session: DbSession, filters: Filters,
                 sort: SortField = "order_date",
                 order: Literal["asc", "desc"] = "desc",
                 page: Annotated[int, Query(ge=1, le=100_000)] = 1,
                 page_size: Annotated[int, Query(ge=1, le=100)] = 25) -> Page[OrderOut]:
    items, total = q.search_records(session, filters, sort, order, page, page_size)
    return Page[OrderOut](items=[OrderOut.model_validate(o) for o in items], total=total,
                          page=page, page_size=page_size, pages=q.pages(total, page_size))


@router.get("/export", summary="Download the filtered records as CSV",
            response_class=Response)
def export(session: DbSession, filters: Filters) -> Response:
    rows = q.export_records(session, filters, EXPORT_LIMIT)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for o in rows:
        w.writerow([getattr(o, c) for c in EXPORT_COLUMNS])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="opsintel_records.csv"',
                             "X-Row-Count": str(len(rows))})
