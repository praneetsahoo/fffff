"""Dashboard aggregates, automatic insights and filter metadata."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import Order
from app.profile import load_profile
from app.repositories import analytics as q
from app.routers.deps import record_filters
from app.schemas import Dashboard, Insight, Meta, RecordFilters
from app.services import insights

router = APIRouter(prefix="/api", tags=["dashboard"])
DbSession = Annotated[Session, Depends(get_session)]
Filters = Annotated[RecordFilters, Depends(record_filters)]


@router.get("/dashboard", response_model=Dashboard,
            summary="KPIs and chart data, computed live from stored records")
def dashboard(session: DbSession, filters: Filters) -> Dashboard:
    return Dashboard(
        kpis=q.kpis(session, filters),
        data_quality=q.data_quality_kpis(session, filters.upload_id),
        monthly=q.monthly(session, filters),
        by_category=q.revenue_by(session, filters, Order.category),
        by_region=q.revenue_by(session, filters, Order.region),
        by_status=q.count_by(session, filters, Order.status),
        top_cities=q.revenue_by(session, filters, Order.city, limit=10),
        sla_by_region=q.sla_by_region(session, filters),
        date_range=q.date_range(session, filters),
    )


@router.get("/insights", response_model=list[Insight],
            summary="Automatically generated business and data-quality insights")
def get_insights(session: DbSession) -> list[dict]:
    return insights.generate(session)


@router.get("/meta", response_model=Meta, summary="Dataset contract and filter options")
def meta(session: DbSession) -> Meta:
    settings = get_settings()
    p = load_profile(settings.dataset_profile)
    return Meta(
        profile=p.name, title=p.title, description=p.description,
        required_columns=p.required_columns, columns=p.column_names,
        max_upload_mb=settings.max_upload_mb, sla_days=p.sla_days,
        filter_options={
            "region": q.distinct_values(session, Order.region),
            "category": q.distinct_values(session, Order.category),
            "status": q.distinct_values(session, Order.status),
            "city": q.distinct_values(session, Order.city),
        },
        date_range=q.date_range(session),
    )
