"""Automatic business insights ("intelligence beyond basic file upload").

Simple, explainable rules over SQL aggregates — every statement can be traced to a number
on the dashboard. Rules compare the latest complete month against recent history:

  1. Revenue trend          latest month vs previous month
  2. Category movers        latest month vs the category's own 3-month average
  3. Delivery SLA risk      region breach rate, last 2 months vs the 3 months before
  4. Cancellations/returns  categories well above the overall rate
  5. Data quality           latest upload's success rate and most common issue
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order, QualityIssue, Upload, UploadStatus
from app.repositories import analytics as q
from app.schemas import RecordFilters

ISSUE_LABEL = {"missing": "missing values", "invalid_type": "wrong data types",
               "invalid_value": "invalid values", "duplicate": "duplicates",
               "standardized": "inconsistent formatting", "filled": "blank optional fields",
               "malformed": "malformed lines"}


def _pct_change(new: float, old: float) -> float | None:
    return None if not old else round(100.0 * (new - old) / old, 1)


def _complete_months(months: list[str], max_date: date | None) -> list[str]:
    """Drop the final month if the data stops well before its end (partial month)."""
    if not months or max_date is None:
        return months
    y, m = map(int, months[-1].split("-"))
    last_day = calendar.monthrange(y, m)[1]
    if (max_date.year, max_date.month) == (y, m) and max_date.day < last_day - 3:
        return months[:-1]
    return months


def _fmt_month(m: str) -> str:
    y, mo = m.split("-")
    return f"{calendar.month_abbr[int(mo)]} {y}"


def _money(v: float) -> str:
    if v >= 1e7:
        return f"₹{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"₹{v / 1e5:.1f} L"
    return f"₹{v:,.0f}"


def generate(session: Session) -> list[dict]:
    insights: list[dict] = []
    allf = RecordFilters()
    rng = q.date_range(session)
    if not rng["max"]:
        return [{"kind": "info", "title": "No data yet",
                 "detail": "Upload a CSV dataset to see insights.", "metric": None}]
    max_date = date.fromisoformat(rng["max"])

    monthly = q.monthly(session, allf)
    months = _complete_months([r["month"] for r in monthly], max_date)
    rev = {r["month"]: r["revenue"] for r in monthly}

    # 1. revenue trend
    if len(months) >= 2:
        cur, prev = months[-1], months[-2]
        chg = _pct_change(rev[cur], rev[prev])
        if chg is not None:
            insights.append({
                "kind": "positive" if chg >= 0 else ("warning" if chg <= -10 else "info"),
                "title": f"Revenue {'up' if chg >= 0 else 'down'} {abs(chg):.1f}% in {_fmt_month(cur)}",
                "detail": f"{_money(rev[cur])} in {_fmt_month(cur)} vs {_money(rev[prev])} "
                          f"in {_fmt_month(prev)}.",
                "metric": f"{chg:+.1f}%"})

    # 2. category movers vs own 3-month average
    if len(months) >= 4:
        cur, hist = months[-1], months[-4:-1]
        by_cat: dict[str, dict[str, float]] = defaultdict(dict)
        for r in q.category_month(session):
            by_cat[r["category"]][r["month"]] = r["revenue"]
        moves = []
        for cat, series in by_cat.items():
            base = sum(series.get(m, 0) for m in hist) / 3
            chg = _pct_change(series.get(cur, 0), base)
            if chg is not None:
                moves.append((chg, cat, series.get(cur, 0), base))
        if moves:
            up = max(moves)
            if up[0] >= 15:
                insights.append({
                    "kind": "positive", "title": f"{up[1]} demand surging",
                    "detail": f"{up[1]} revenue in {_fmt_month(cur)} was {_money(up[2])}, "
                              f"{up[0]:.0f}% above its 3-month average of {_money(up[3])}.",
                    "metric": f"+{up[0]:.0f}%"})
            down = min(moves)
            if down[0] <= -15:
                insights.append({
                    "kind": "warning", "title": f"{down[1]} revenue falling",
                    "detail": f"{down[1]} revenue in {_fmt_month(cur)} was {abs(down[0]):.0f}% "
                              f"below its 3-month average.",
                    "metric": f"{down[0]:.0f}%"})

    # 3. delivery SLA risk by region
    if len(months) >= 5:
        recent, before = set(months[-2:]), set(months[-5:-2])
        agg: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # rm, rb, bm, bb
        for r in q.sla_by_region_month(session):
            a = agg[r["region"]]
            if r["month"] in recent:
                a[0] += r["measured"]; a[1] += r["breached"]
            elif r["month"] in before:
                a[2] += r["measured"]; a[3] += r["breached"]
        worst = None
        for region, (rm, rb, bm, bb) in agg.items():
            if rm < 20 or bm < 20:
                continue
            now, then = 100 * rb / rm, 100 * bb / bm
            if worst is None or now - then > worst[0]:
                worst = (now - then, region, now, then)
        if worst and worst[0] >= 5:
            insights.append({
                "kind": "warning", "title": f"Delivery delays rising in {worst[1]}",
                "detail": f"{worst[2]:.0f}% of {worst[1]} deliveries in the last two months took "
                          f"longer than the SLA, up from {worst[3]:.0f}% in the three months before.",
                "metric": f"{worst[2]:.0f}% late"})

    # 4. cancellations / returns by category
    overall = q.kpis(session, allf)["cancel_return_pct"]
    worst_cat = None
    for cat in q.distinct_values(session, Order.category):
        pct = q.kpis(session, RecordFilters(category=[cat]))["cancel_return_pct"]
        if worst_cat is None or pct > worst_cat[1]:
            worst_cat = (cat, pct)
    if worst_cat and worst_cat[1] >= overall + 2:
        insights.append({
            "kind": "warning", "title": f"High cancellations/returns in {worst_cat[0]}",
            "detail": f"{worst_cat[1]:.1f}% of {worst_cat[0]} orders were cancelled or returned, "
                      f"vs {overall:.1f}% across all categories.",
            "metric": f"{worst_cat[1]:.1f}%"})

    # 5. data quality of the latest upload
    latest = session.scalar(select(Upload).where(Upload.status == UploadStatus.COMPLETED)
                            .order_by(Upload.completed_at.desc()).limit(1))
    if latest is not None:
        top = session.scalar(
            select(QualityIssue)
            .where(QualityIssue.upload_id == latest.id,
                   QualityIssue.issue_type.notin_(("standardized", "filled")))
            .order_by(QualityIssue.issue_count.desc()).limit(1))
        detail = (f"{latest.valid_rows:,} of {latest.total_rows:,} rows in "
                  f"'{latest.original_filename}' were loaded.")
        if top is not None:
            detail += (f" Most common problem: {ISSUE_LABEL.get(top.issue_type, top.issue_type)} "
                       f"in '{top.column_name}' ({top.issue_count:,} rows).")
        insights.append({"kind": "info", "title": "Latest upload data quality",
                         "detail": detail, "metric": f"{latest.success_rate:.1f}%"})

    order = {"warning": 0, "positive": 1, "info": 2}
    return sorted(insights, key=lambda i: order[i["kind"]])
