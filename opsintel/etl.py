"""ETL: Extract -> Validate -> Clean -> Transform, using pandas and NumPy.

    extract()     read CSV / Excel / JSON into a table of text values
    check_columns() make sure the required columns exist
    clean()       fix what can be fixed safely, record every problem that can't
    deduplicate() remove repeated records (in the file, or already in the database)
    transform()   add derived columns (revenue, month, late delivery)

Every input row ends up in exactly one bucket:  loaded  |  rejected  |  duplicate
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from opsintel.config import SLA_DAYS

# ---------------------------------------------------------------- the data rules

REQUIRED = ["order_id", "order_date", "region", "city", "category", "product",
            "quantity", "unit_price", "status"]
OPTIONAL = ["state", "delivery_days"]
COLUMNS = REQUIRED + OPTIONAL

ALLOWED = {
    "region": ["North", "South", "East", "West", "Central"],
    "category": ["Electronics", "Grocery", "Apparel", "Home & Kitchen", "Health & Beauty"],
    "status": ["Delivered", "Shipped", "Pending", "Cancelled", "Returned"],
}
SYNONYMS = {   # different spellings of the same thing -> one standard value
    "region": {"n": "North", "s": "South", "e": "East", "w": "West", "northern": "North",
               "southern": "South", "eastern": "East", "western": "West"},
    "city": {"bombay": "Mumbai", "bangalore": "Bengaluru", "calcutta": "Kolkata", "madras": "Chennai",
             "new delhi": "Delhi", "delhi ncr": "Delhi", "gurgaon": "Gurugram"},
    "category": {"groceries": "Grocery", "clothing": "Apparel", "home and kitchen": "Home & Kitchen",
                 "health and beauty": "Health & Beauty"},
    "status": {"completed": "Delivered", "canceled": "Cancelled", "in transit": "Shipped",
               "dispatched": "Shipped", "processing": "Pending"},
}
NUMBERS = {"quantity": (1, 1000, True), "unit_price": (0.01, 1_000_000, False),
           "delivery_days": (0, 60, True)}           # column: (min, max, must be whole number)
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"]
MIN_DATE = pd.Timestamp("2020-01-01")
ORDER_ID = re.compile(r"^ORD-\d{6}$")
BLANKS = {"", "na", "n/a", "null", "none", "nan", "-", "?"}


class DataError(Exception):
    """The whole file can't be used (wrong format, empty, missing columns)."""


@dataclass
class EtlResult:
    clean: pd.DataFrame        # rows ready for the database
    problems: pd.DataFrame     # one row per problem: line_number, issue_column, issue_type, message, raw_data
    total_rows: int
    loaded_rows: int
    rejected_rows: int
    duplicate_rows: int
    fixed_values: int


# ---------------------------------------------------------------- 1. extract

def extract(file_name: str, data: bytes) -> pd.DataFrame:
    """Read the uploaded file into a DataFrame where every value is text (validated later)."""
    name = file_name.lower()
    if not data or not data.strip():
        raise DataError("The file is empty.")
    try:
        if name.endswith(".csv"):
            if b"\x00" in data[:4096]:
                raise DataError("This does not look like a CSV text file.")
            df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False,
                             sep=None, engine="python", encoding_errors="replace")
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(data), dtype=str).fillna("")
        elif name.endswith(".json"):
            df = pd.DataFrame(json.loads(data)).astype(str).replace({"None": "", "nan": ""})
        else:
            raise DataError("Unsupported file type. Upload a .csv, .xlsx or .json file.")
    except DataError:
        raise
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise DataError(f"The file could not be read: {exc}") from exc
    if df.empty:
        raise DataError("The file has a header but no data rows.")
    df.columns = [re.sub(r"[\s\-]+", "_", str(c).strip().lower()) for c in df.columns]
    df.insert(0, "line_number", range(2, len(df) + 2))   # line 1 is the header
    return df


# ---------------------------------------------------------------- 2. validate columns

def check_columns(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise DataError(f"Missing required column(s): {', '.join(missing)}.")
    for col in OPTIONAL:
        if col not in df.columns:
            df[col] = ""
    return df[["line_number"] + COLUMNS].copy()


# ---------------------------------------------------------------- 3. clean

def standard_spelling(values: pd.Series, column: str) -> pd.Series:
    """Unify spellings: 'MUMBAI', 'mumbai', 'Bombay' -> 'Mumbai'. Correct values stay as they are."""
    key = values.str.lower()
    canonical = {}
    if column in ALLOWED:
        canonical.update({v.lower(): v for v in ALLOWED[column]})
    canonical.update(SYNONYMS.get(column, {}))
    # otherwise: the most common properly-capitalised spelling in the file wins
    mixed = values[(values != values.str.lower()) & (values != values.str.upper())]
    for spelling in mixed.value_counts().index:
        canonical.setdefault(spelling.lower(), spelling)
    return key.map(canonical).fillna(values.str.title())


def clean(df: pd.DataFrame, today: date | None = None) -> tuple[pd.DataFrame, list[dict], int]:
    today = pd.Timestamp(today or date.today())
    raw = df.copy()
    problems: list[dict] = []
    fixed = 0

    def problem(mask: pd.Series, column: str, kind: str, message) -> None:
        for idx in df.index[mask]:
            text_ = message(raw.at[idx, column]) if callable(message) else message
            problems.append({"idx": idx, "issue_column": column, "issue_type": kind, "message": text_})

    for col in COLUMNS:
        text_values = df[col].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
        blank = text_values.str.lower().isin(BLANKS)
        if col in REQUIRED:
            problem(blank, col, "missing", f"{col} is missing")
        values = text_values.mask(blank)
        present = values.notna()

        if col in NUMBERS:
            lo, hi, whole = NUMBERS[col]
            numbers = pd.to_numeric(values.str.replace(r"[₹,]|^rs\.?\s*", "", regex=True, case=False),
                                    errors="coerce")
            bad_type = present & (numbers.isna() | (whole & (numbers % 1 != 0)))
            problem(bad_type, col, "wrong_type", lambda v, c=col: f"{c}: '{v}' is not a valid number")
            out_of_range = numbers.notna() & ~bad_type & ((numbers < lo) | (numbers > hi))
            problem(out_of_range, col, "invalid_value", lambda v, c=col, a=lo, b=hi: f"{c}: {v} is outside {a}–{b}")
            df[col] = numbers.where(~bad_type)

        elif col == "order_date":
            parsed = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
            for i, fmt in enumerate(DATE_FORMATS):
                todo = present & parsed.isna()
                attempt = pd.to_datetime(values[todo], format=fmt, errors="coerce")
                parsed[attempt.index] = attempt
                if i > 0:
                    fixed += int(attempt.notna().sum())       # converted to the standard format
            problem(present & parsed.isna(), col, "wrong_type", lambda v: f"order_date: '{v}' is not a valid date")
            problem(parsed.notna() & ((parsed < MIN_DATE) | (parsed > today)), col, "invalid_value",
                    lambda v: f"order_date: {v} is before 2020 or in the future")
            df[col] = parsed

        else:  # text columns
            cleaned = values.copy()
            if col == "order_id":
                cleaned = values.str.upper()
                problem(present & ~cleaned.fillna("").str.match(ORDER_ID), col, "invalid_value",
                        lambda v: f"order_id: '{v}' should look like ORD-123456")
            else:
                cleaned[present] = standard_spelling(values[present], col)
                if col in ALLOWED:
                    problem(present & ~cleaned.isin(ALLOWED[col]), col, "invalid_value",
                            lambda v, c=col: f"{c}: '{v}' is not one of {', '.join(ALLOWED[c])}")
            fixed += int((present & (cleaned != df[col])).sum())   # spelling / spacing repaired
            df[col] = cleaned

    blank_state = df["state"].isna()
    df.loc[blank_state, "state"] = "Unknown"                        # optional: default value
    fixed += int(blank_state.sum())
    return df, problems, fixed


# ---------------------------------------------------------------- 4. deduplicate

def deduplicate(valid: pd.DataFrame, existing_ids: set[str]) -> tuple[pd.DataFrame, list[dict]]:
    problems = []
    first_line = valid.drop_duplicates("order_id").set_index("order_id")["line_number"]
    exact = valid.duplicated(subset=COLUMNS, keep="first")
    same_id = valid.duplicated(subset=["order_id"], keep="first") & ~exact
    already = valid["order_id"].isin(existing_ids) & ~exact & ~same_id
    for idx in valid.index[exact]:
        problems.append({"idx": idx, "issue_column": "order_id", "issue_type": "duplicate",
                         "message": f"exact copy of line {first_line[valid.at[idx, 'order_id']]}"})
    for idx in valid.index[same_id]:
        problems.append({"idx": idx, "issue_column": "order_id", "issue_type": "duplicate",
                         "message": f"order_id already used on line {first_line[valid.at[idx, 'order_id']]}"})
    for idx in valid.index[already]:
        problems.append({"idx": idx, "issue_column": "order_id", "issue_type": "duplicate",
                         "message": "order_id was already loaded by an earlier upload"})
    return valid[~(exact | same_id | already)], problems


# ---------------------------------------------------------------- 5. transform

def transform(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["quantity"] = out["quantity"].astype(int)
    out["revenue"] = np.round(out["quantity"] * out["unit_price"].astype(float), 2)
    out["order_month"] = out["order_date"].dt.strftime("%Y-%m")
    out["order_date"] = out["order_date"].dt.date
    days = out["delivery_days"].astype(float)
    out["is_late"] = np.where(days.isna(), np.nan, (days > SLA_DAYS).astype(float))
    return out


# ---------------------------------------------------------------- the whole pipeline

def run(file_name: str, data: bytes, existing_ids_lookup=lambda ids: set(),
        today: date | None = None) -> EtlResult:
    raw = check_columns(extract(file_name, data))
    original = raw.copy()
    cleaned, problems, fixed = clean(raw.copy(), today)

    bad_idx = {p["idx"] for p in problems}
    valid = cleaned.drop(index=list(bad_idx))
    ids = valid["order_id"].dropna().unique().tolist()
    loaded, dup_problems = deduplicate(valid, existing_ids_lookup(ids) if ids else set())
    problems += dup_problems

    problems_df = pd.DataFrame(problems, columns=["idx", "issue_column", "issue_type", "message"])
    problems_df["line_number"] = original.loc[problems_df["idx"], "line_number"].to_numpy()
    problems_df["raw_data"] = [
        json.dumps(original.loc[i, COLUMNS].to_dict(), ensure_ascii=False) for i in problems_df["idx"]]
    dup_idx = {p["idx"] for p in dup_problems}

    return EtlResult(
        clean=transform(loaded) if len(loaded) else loaded,
        problems=problems_df.drop(columns="idx").sort_values("line_number").reset_index(drop=True),
        total_rows=len(raw),
        loaded_rows=len(loaded),
        rejected_rows=len(bad_idx),
        duplicate_rows=len(dup_idx),
        fixed_values=fixed,
    )
