"""Plain-English insights built on the SQL results (the window functions do the heavy lifting).

Each insight compares the latest complete month with that region's or category's own average
over the 3 months before it (computed in SQL with AVG() OVER ... ROWS BETWEEN 3 PRECEDING).
"""
from __future__ import annotations

import pandas as pd

from opsintel import db


def _latest_month(df: pd.DataFrame) -> str | None:
    return df["order_month"].max() if len(df) else None


def generate() -> list[dict]:
    """Return [{kind: 'warning'|'good'|'info', title, detail}] — biggest signals only."""
    found: list[dict] = []

    months = db.query("monthly_trend")
    if len(months) >= 2:
        last = months.iloc[-1]
        if pd.notna(last["growth_pct"]):
            g = float(last["growth_pct"])
            found.append({"kind": "good" if g >= 0 else "warning",
                          "title": f"Revenue {'up' if g >= 0 else 'down'} {abs(g):.0f}% in {last['order_month']}",
                          "detail": f"Compared with the month before (running total ₹{float(last['running_revenue']) / 1e7:.2f} Cr)."})

    late = db.query("region_late_trend").dropna(subset=["baseline_late_pct"])
    month = _latest_month(late)
    if month:
        now = late[late["order_month"] == month].copy()
        now["jump"] = now["late_pct"].astype(float) - now["baseline_late_pct"].astype(float)
        worst = now.sort_values("jump", ascending=False).iloc[0] if len(now) else None
        if worst is not None and worst["jump"] >= 10:
            found.append({"kind": "warning", "title": f"Delivery delays rising in {worst['region']}",
                          "detail": f"{float(worst['late_pct']):.0f}% of deliveries were late in {month}, "
                                    f"against {float(worst['baseline_late_pct']):.0f}% over the previous 3 months."})

    cats = db.query("category_month_trend").dropna(subset=["baseline_revenue"])
    month = _latest_month(cats)
    if month:
        now = cats[cats["order_month"] == month].copy()
        now["change"] = 100 * (now["revenue"].astype(float) / now["baseline_revenue"].astype(float) - 1)
        up, down = now.sort_values("change").iloc[-1], now.sort_values("change").iloc[0]
        if up["change"] >= 20:
            found.append({"kind": "good", "title": f"{up['category']} demand surging",
                          "detail": f"{up['change']:.0f}% above its 3-month average in {month}."})
        if down["change"] <= -20:
            found.append({"kind": "warning", "title": f"{down['category']} revenue falling",
                          "detail": f"{abs(down['change']):.0f}% below its 3-month average in {month}."})

    order = {"warning": 0, "good": 1, "info": 2}
    return sorted(found, key=lambda i: order[i["kind"]])
