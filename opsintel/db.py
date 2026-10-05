"""Database access with SQLAlchemy.

The SQL itself lives in sql/schema.sql and sql/queries.sql, so it can be read and explained
on its own. This module only connects, runs those queries with bound parameters, and returns
pandas DataFrames.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine

from opsintel import config

log = logging.getLogger(__name__)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


@lru_cache
def engine() -> Engine:
    # pool_pre_ping: RDS closes idle connections; check each one before use.
    return create_engine(config.database_url(), pool_pre_ping=True, pool_recycle=1800)


def reset_engine() -> None:
    """Used by tests to point at a different database."""
    if engine.cache_info().currsize:
        engine().dispose()
    engine.cache_clear()
    config.database_url.cache_clear()


def create_schema() -> None:
    """Run sql/schema.sql (CREATE TABLE IF NOT EXISTS — safe to run every start)."""
    statements = [s.strip() for s in (SQL_DIR / "schema.sql").read_text().split(";") if s.strip()]
    with engine().begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    log.info("Database schema ready")


def is_available() -> bool:
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # any failure simply means "unavailable" for the health indicator
        log.exception("Database health check failed")
        return False


@lru_cache
def _queries() -> dict[str, str]:
    """Split sql/queries.sql into {name: sql} using the '-- name: x' markers."""
    parts = re.split(r"^-- name: (\w+)\s*$", (SQL_DIR / "queries.sql").read_text(), flags=re.M)
    return {name: body.strip().rstrip(";") for name, body in zip(parts[1::2], parts[2::2])}


# ---------- filters (dashboard + records) ----------

def build_filters(regions=(), categories=(), statuses=(), date_from: date | None = None,
                  date_to: date | None = None, search: str = "", late_only: bool = False,
                  upload_id: int | None = None) -> tuple[str, dict]:
    """Return a fixed SQL fragment plus its parameters.

    Only these hard-coded fragments are ever added to the SQL text; the user's values travel
    separately as bound parameters, which is what prevents SQL injection.
    """
    sql, params = [], {}
    if regions:
        sql.append("AND o.region IN :regions"); params["regions"] = list(regions)
    if categories:
        sql.append("AND o.category IN :categories"); params["categories"] = list(categories)
    if statuses:
        sql.append("AND o.status IN :statuses"); params["statuses"] = list(statuses)
    if date_from:
        sql.append("AND o.order_date >= :date_from"); params["date_from"] = date_from
    if date_to:
        sql.append("AND o.order_date <= :date_to"); params["date_to"] = date_to
    if search:
        sql.append("AND (o.order_id LIKE :search OR o.product LIKE :search OR o.city LIKE :search)")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["search"] = f"%{escaped}%"
    if late_only:
        sql.append("AND o.is_late = 1")
    if upload_id:
        sql.append("AND o.upload_id = :upload_id"); params["upload_id"] = upload_id
    return " ".join(sql), params


SORT_COLUMNS = {   # whitelist: user picks a label, never types SQL
    "Newest first": "o.order_date DESC, o.order_id",
    "Oldest first": "o.order_date ASC, o.order_id",
    "Highest revenue": "o.revenue DESC, o.order_id",
    "Lowest revenue": "o.revenue ASC, o.order_id",
    "Order ID": "o.order_id",
}


def query(name: str, params: dict | None = None, filters: str = "",
          order_by: str = "o.order_date DESC, o.order_id") -> pd.DataFrame:
    """Run a named query from sql/queries.sql and return the rows as a DataFrame."""
    sql = _queries()[name].replace("{filters}", filters).replace("{order_by}", order_by)
    params = dict(params or {})
    stmt = text(sql)
    lists = [k for k, v in params.items() if isinstance(v, (list, tuple, set))]
    if lists:  # IN :name needs an "expanding" parameter so SQLAlchemy sends each value separately
        stmt = stmt.bindparams(*[bindparam(k, expanding=True) for k in lists])
        params.update({k: list(params[k]) for k in lists})
    with engine().connect() as conn:
        return pd.read_sql(stmt, conn, params=params)


def distinct_values(column: str) -> list[str]:
    allowed = {"region", "category", "status", "city"}
    if column not in allowed:
        raise ValueError(column)
    with engine().connect() as conn:
        return [r[0] for r in conn.execute(text(f"SELECT DISTINCT {column} FROM orders ORDER BY {column}"))]
