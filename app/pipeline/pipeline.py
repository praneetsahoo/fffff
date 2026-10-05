"""The processing pipeline:  parse → validate → clean → de-duplicate → derive → score.

Pure function: bytes in, PipelineResult out. No database or AWS calls here, which keeps it
fast to test and easy to move later (e.g. to Lambda or Glue). Every input row ends up in
exactly one bucket — valid, invalid or duplicate — so nothing is silently dropped:

    total_rows = valid_rows + invalid_rows + duplicate_rows
"""
from __future__ import annotations

import io
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from app.pipeline.cleaners import (TYPE_ERROR_TEXT, clean_date, clean_decimal, clean_integer,
                                   clean_string, split_missing)
from app.pipeline.reader import ROW_COL, parse_csv
from app.profile import DatasetProfile

log = logging.getLogger(__name__)

DERIVED_COLUMNS = ["revenue", "order_month", "sla_breached"]


@dataclass
class PipelineResult:
    clean: pd.DataFrame                    # typed, de-duplicated rows + derived metrics + _row
    rejected: list[dict]                   # {row_number, raw, reasons, kind}
    issues: Counter                        # (column, issue_type) -> count
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    rows_needing_fixes: int                # valid rows the pipeline had to repair
    extra_columns: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """% of input rows that made it into the clean dataset."""
        return round(100 * self.valid_rows / self.total_rows, 2) if self.total_rows else 0.0

    @property
    def quality_score(self) -> float:
        """% of input rows that were already perfect at source (no fix, no rejection)."""
        if not self.total_rows:
            return 0.0
        perfect = self.valid_rows - self.rows_needing_fixes
        return round(100 * perfect / self.total_rows, 2)

    def issues_as_rows(self) -> list[dict]:
        return [{"column": c, "issue_type": t, "count": n}
                for (c, t), n in sorted(self.issues.items())]


def run_pipeline(data: bytes, filename: str, profile: DatasetProfile, *,
                 existing_keys: set[str] | frozenset[str] = frozenset(),
                 today: date | None = None) -> PipelineResult:
    today = today or date.today()
    parsed = parse_csv(data, filename, profile)
    df = parsed.df
    n = len(df)
    reasons: list[list[str]] = [[] for _ in range(n)]
    needs_fix = np.zeros(n, dtype=bool)
    issues: Counter = Counter()
    out = pd.DataFrame(index=df.index)

    def add(mask: pd.Series, messages) -> None:
        for i in np.flatnonzero(mask.to_numpy()):
            reasons[i].append(messages if isinstance(messages, str) else messages.iat[i])

    # ---- validate + clean every profile column ----
    for col in profile.columns:
        values, missing, ws_fixed = split_missing(df[col.name])

        if col.type == "string":
            res = clean_string(values, col)
        elif col.type == "integer":
            res = clean_integer(values, col)
        elif col.type == "decimal":
            res = clean_decimal(values, col)
        else:
            res = clean_date(values, col, today)

        cleaned = res.values
        if missing.any():
            if col.required:
                issues[(col.name, "missing")] += int(missing.sum())
                add(missing, f"{col.name}: required value is missing")
            elif col.fill is not None:
                issues[(col.name, "filled")] += int(missing.sum())
                cleaned = cleaned.astype(object).mask(missing, col.fill)
                needs_fix |= missing.to_numpy()
            else:
                # Optional value: only a quality issue when the profile says it is expected
                # here (e.g. delivery_days only exists once an order is Delivered/Returned).
                cond = col.expected_when
                if cond and cond["column"] in out.columns:
                    missing = missing & out[cond["column"]].isin(cond["values"])
                if missing.any():
                    issues[(col.name, "missing")] += int(missing.sum())
                    needs_fix |= missing.to_numpy()  # kept, but not "perfect"

        if res.bad_type.any():
            issues[(col.name, "invalid_type")] += int(res.bad_type.sum())
            text = TYPE_ERROR_TEXT[col.type]
            add(res.bad_type, values.map(lambda x, t=text, c=col.name: f"{c}: '{x}' {t}",
                                         na_action="ignore").fillna(""))
        if res.bad_value.any():
            issues[(col.name, "invalid_value")] += int(res.bad_value.sum())
            add(res.bad_value, res.value_msg)

        fixed = (res.changed | ws_fixed) & ~res.bad_type & ~res.bad_value
        if fixed.any():
            issues[(col.name, "standardized")] += int(fixed.sum())
            needs_fix |= fixed.to_numpy()

        out[col.name] = cleaned

    out[ROW_COL] = df[ROW_COL]
    invalid = np.array([bool(r) for r in reasons], dtype=bool)

    rejected: list[dict] = [
        {"row_number": int(df[ROW_COL].iat[i]),
         "raw": {c: df[c].iat[i] for c in profile.column_names},
         "reasons": reasons[i], "kind": "invalid"}
        for i in np.flatnonzero(invalid)
    ]

    # ---- de-duplicate the valid rows ----
    valid = out[~invalid]
    key = profile.key
    first_row = valid.drop_duplicates(subset=[key], keep="first").set_index(key)[ROW_COL]

    exact = valid.duplicated(subset=profile.column_names, keep="first")
    remaining = valid[~exact]
    conflict = remaining.duplicated(subset=[key], keep="first")
    already = remaining[key].isin(existing_keys) & ~conflict

    dup_reason: dict[int, str] = {}
    for i, k in valid.loc[exact, key].items():
        dup_reason[i] = f"exact duplicate of row {first_row[k]}"
    for i, k in remaining.loc[conflict, key].items():
        dup_reason[i] = f"{key} {k} already appears on row {first_row[k]} with different values"
    for i, k in remaining.loc[already, key].items():
        dup_reason[i] = f"{key} {k} was already loaded by a previous upload"

    for i, msg in sorted(dup_reason.items()):
        rejected.append({"row_number": int(df[ROW_COL].iat[i]),
                         "raw": {c: df[c].iat[i] for c in profile.column_names},
                         "reasons": [msg], "kind": "duplicate"})
    if dup_reason:
        issues[(key, "duplicate")] += len(dup_reason)

    clean = valid.drop(index=list(dup_reason)).copy()
    needs_fix_count = int(needs_fix[clean.index.to_numpy()].sum()) if len(clean) else 0

    # ---- malformed lines from the reader ----
    rejected.extend(parsed.malformed)
    if parsed.malformed:
        issues[("_row", "malformed")] += len(parsed.malformed)

    # ---- derived metrics ----
    clean = _derive(clean, profile)
    rejected.sort(key=lambda r: r["row_number"])

    result = PipelineResult(
        clean=clean.reset_index(drop=True),
        rejected=rejected,
        issues=issues,
        total_rows=n + len(parsed.malformed),
        valid_rows=len(clean),
        invalid_rows=int(invalid.sum()) + len(parsed.malformed),
        duplicate_rows=len(dup_reason),
        rows_needing_fixes=needs_fix_count,
        extra_columns=parsed.extra_columns,
    )
    log.info("Pipeline %s: total=%d valid=%d invalid=%d duplicates=%d success=%.2f%% quality=%.2f",
             filename, result.total_rows, result.valid_rows, result.invalid_rows,
             result.duplicate_rows, result.success_rate, result.quality_score)
    return result


def _derive(clean: pd.DataFrame, profile: DatasetProfile) -> pd.DataFrame:
    r = profile.roles
    qty = clean[r["quantity"]].astype("float64")
    price = clean[r["price"]].astype("float64")
    clean["revenue"] = (qty * price).round(2)
    clean["order_month"] = pd.to_datetime(clean[r["date"]]).dt.strftime("%Y-%m")
    dd = clean[r["delivery_days"]]
    clean["sla_breached"] = (dd > profile.sla_days).astype("boolean").where(dd.notna())
    return clean


# ---------- outputs written to S3 ----------

def clean_csv_bytes(result: PipelineResult, profile: DatasetProfile) -> bytes:
    cols = profile.column_names + DERIVED_COLUMNS
    buf = io.StringIO()
    result.clean[cols].to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def rejected_csv_bytes(result: PipelineResult, profile: DatasetProfile) -> bytes:
    rows = [{"row_number": r["row_number"], "kind": r["kind"],
             "reasons": "; ".join(r["reasons"]), **r["raw"]} for r in result.rejected]
    cols = ["row_number", "kind", "reasons"] + profile.column_names
    buf = io.StringIO()
    pd.DataFrame(rows, columns=cols).to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")
