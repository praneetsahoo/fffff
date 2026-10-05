"""Read-side queries: record search and dashboard aggregations.

Every number shown in the UI is computed here with SQL over the stored records —
nothing is hard-coded. Queries use portable SQLAlchemy Core (work on MySQL and SQLite);
monthly grouping uses the stored `order_month` column, so no dialect-specific date functions.
"""
from __future__ import annotations

import math

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models import Order, Upload, UploadStatus
from app.schemas import RecordFilters

SORTABLE = {
    "order_date": Order.order_date, "order_id": Order.order_id, "revenue": Order.revenue,
    "quantity": Order.quantity, "unit_price": Order.unit_price, "city": Order.city,
    "region": Order.region, "category": Order.category, "status": Order.status,
    "delivery_days": Order.delivery_days, "product": Order.product,
}
COMPLETE_STATUSES = ("Delivered", "Returned")


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def filter_conditions(f: RecordFilters) -> list:
    conds = []
    if f.q:
        term = f"%{_escape_like(f.q.strip().lower())}%"
        conds.append(or_(func.lower(Order.order_id).like(term, escape="\\"),
                         func.lower(Order.product).like(term, escape="\\"),
                         func.lower(Order.city).like(term, escape="\\")))
    for col, values in ((Order.region, f.region), (Order.category, f.category),
                        (Order.status, f.status), (Order.city, f.city)):
        if values:
            conds.append(col.in_(values))
    if f.date_from:
        conds.append(Order.order_date >= f.date_from)
    if f.date_to:
        conds.append(Order.order_date <= f.date_to)
    if f.sla_breached is not None:
        conds.append(Order.sla_breached.is_(f.sla_breached))
    if f.upload_id:
        conds.append(Order.upload_id == f.upload_id)
    return conds


def _where(stmt: Select, f: RecordFilters) -> Select:
    conds = filter_conditions(f)
    return stmt.where(and_(*conds)) if conds else stmt


# ---------- records ----------

def search_records(session: Session, f: RecordFilters, sort: str, order: str,
                   page: int, page_size: int) -> tuple[list[Order], int]:
    total = session.scalar(_where(select(func.count()).select_from(Order), f)) or 0
    col = SORTABLE[sort]
    primary = col.desc() if order == "desc" else col.asc()
    stmt = (_where(select(Order), f)
            .order_by(primary, Order.order_id.asc())  # stable order for pagination
            .offset((page - 1) * page_size).limit(page_size))
    return list(session.scalars(stmt)), total


def export_records(session: Session, f: RecordFilters, limit: int) -> list[Order]:
    stmt = _where(select(Order), f).order_by(Order.order_date, Order.order_id).limit(limit)
    return list(session.scalars(stmt))


def pages(total: int, page_size: int) -> int:
    return max(1, math.ceil(total / page_size))


# ---------- dashboard ----------

def _pct(part, whole) -> float:
    # MySQL returns SUM()/COUNT-based expressions as Decimal; normalise to float.
    return round(100.0 * float(part or 0) / float(whole), 2) if whole else 0.0


def kpis(session: Session, f: RecordFilters) -> dict:
    complete = Order.status.in_(COMPLETE_STATUSES)
    row = session.execute(_where(select(
        func.count(),
        func.coalesce(func.sum(Order.revenue), 0),
        func.coalesce(func.sum(Order.quantity), 0),
        func.sum(case((Order.status == "Delivered", 1), else_=0)),
        func.sum(case((Order.status.in_(("Cancelled", "Returned")), 1), else_=0)),
        func.avg(case((complete, Order.delivery_days), else_=None)),
        func.sum(case((Order.sla_breached.is_(True), 1), else_=0)),
        func.count(Order.sla_breached),
    ).select_from(Order), f)).one()
    orders, revenue, units, delivered, cancel_ret, avg_dd, breached, with_dd = row
    return {
        "orders": orders,
        "revenue": round(float(revenue), 2),
        "units": int(units),
        "avg_order_value": round(float(revenue) / orders, 2) if orders else 0.0,
        "delivered_pct": _pct(delivered, orders),
        "cancel_return_pct": _pct(cancel_ret, orders),
        "avg_delivery_days": round(float(avg_dd), 2) if avg_dd is not None else None,
        "sla_breach_pct": _pct(breached, with_dd) if with_dd else None,
    }


def monthly(session: Session, f: RecordFilters) -> list[dict]:
    stmt = _where(select(Order.order_month, func.sum(Order.revenue), func.count())
                  .select_from(Order), f).group_by(Order.order_month).order_by(Order.order_month)
    return [{"month": m, "revenue": round(float(r), 2), "orders": n}
            for m, r, n in session.execute(stmt)]


def revenue_by(session: Session, f: RecordFilters, column, limit: int | None = None) -> list[dict]:
    rev = func.sum(Order.revenue)
    stmt = (_where(select(column, rev, func.count()).select_from(Order), f)
            .group_by(column).order_by(rev.desc()))
    if limit:
        stmt = stmt.limit(limit)
    return [{"label": k, "value": round(float(v), 2), "count": n}
            for k, v, n in session.execute(stmt)]


def count_by(session: Session, f: RecordFilters, column) -> list[dict]:
    n = func.count()
    stmt = (_where(select(column, n).select_from(Order), f).group_by(column).order_by(n.desc()))
    return [{"label": k, "value": float(c), "count": c} for k, c in session.execute(stmt)]


def sla_by_region(session: Session, f: RecordFilters) -> list[dict]:
    breached = func.sum(case((Order.sla_breached.is_(True), 1), else_=0))
    measured = func.count(Order.sla_breached)
    stmt = (_where(select(Order.region, measured, breached, func.avg(Order.delivery_days))
                   .select_from(Order), f)
            .group_by(Order.region).order_by(Order.region))
    return [{"region": r, "delivered": int(m or 0), "breached": int(b or 0),
             "breach_pct": _pct(b, m), "avg_delivery_days": round(float(a), 2) if a else None}
            for r, m, b, a in session.execute(stmt)]


def sla_by_region_month(session: Session) -> list[dict]:
    breached = func.sum(case((Order.sla_breached.is_(True), 1), else_=0))
    measured = func.count(Order.sla_breached)
    stmt = (select(Order.region, Order.order_month, measured, breached)
            .group_by(Order.region, Order.order_month)
            .order_by(Order.region, Order.order_month))
    return [{"region": r, "month": m, "measured": int(n or 0), "breached": int(b or 0)}
            for r, m, n, b in session.execute(stmt)]


def category_month(session: Session) -> list[dict]:
    stmt = (select(Order.category, Order.order_month, func.sum(Order.revenue), func.count())
            .group_by(Order.category, Order.order_month)
            .order_by(Order.category, Order.order_month))
    return [{"category": c, "month": m, "revenue": float(r), "orders": n}
            for c, m, r, n in session.execute(stmt)]


def date_range(session: Session, f: RecordFilters | None = None) -> dict:
    stmt = select(func.min(Order.order_date), func.max(Order.order_date)).select_from(Order)
    if f is not None:
        stmt = _where(stmt, f)
    lo, hi = session.execute(stmt).one()
    return {"min": lo.isoformat() if lo else None, "max": hi.isoformat() if hi else None}


def distinct_values(session: Session, column) -> list[str]:
    return [v for v in session.scalars(select(column).distinct().order_by(column)) if v]


def data_quality_kpis(session: Session, upload_id: str | None = None) -> dict:
    done = Upload.status == UploadStatus.COMPLETED
    stmt = select(
        func.sum(case((done, 1), else_=0)),
        func.sum(case((Upload.status == UploadStatus.FAILED, 1), else_=0)),
        func.coalesce(func.sum(case((done, Upload.total_rows), else_=0)), 0),
        func.coalesce(func.sum(case((done, Upload.valid_rows), else_=0)), 0),
        func.coalesce(func.sum(case((done, Upload.invalid_rows), else_=0)), 0),
        func.coalesce(func.sum(case((done, Upload.duplicate_rows), else_=0)), 0),
    ).select_from(Upload)
    if upload_id:
        stmt = stmt.where(Upload.id == upload_id)
    completed, failed, total, valid, invalid, dup = session.execute(stmt).one()
    total = int(total or 0)
    return {
        "uploads_completed": int(completed or 0), "uploads_failed": int(failed or 0),
        "records_received": total, "records_valid": int(valid or 0),
        "records_invalid": int(invalid or 0), "records_duplicate": int(dup or 0),
        "success_rate": _pct(valid, total) if total else None,
    }
