"""Stage 2 — per-column validation and standardisation, driven by the dataset profile.

Each cleaner is vectorised (pandas) and returns a ColumnResult with:
  values     cleaned, correctly typed values
  bad_type   rows whose value could not be converted (e.g. quantity = "abc")
  bad_value  rows whose value converted but breaks a rule (range, allowed list, pattern, date limits)
  changed    rows whose value was fixed/standardised (case, synonym, date format, currency symbol)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.profile import ColumnSpec

NULL_TOKENS = {"na", "n/a", "null", "none", "nan", "-", "--", "?"}


@dataclass
class ColumnResult:
    values: pd.Series
    bad_type: pd.Series
    bad_value: pd.Series
    changed: pd.Series
    value_msg: pd.Series  # message per bad_value row ('' elsewhere)


def _false(index) -> pd.Series:
    return pd.Series(False, index=index)


def smart_title(text: str) -> str:
    """'  basmati RICE 5KG ' -> 'Basmati Rice 5kg' (consistent; leaves '&' and digits alone)."""
    words = text.split()
    return " ".join(w[:1].upper() + w[1:].lower() if w[:1].isalpha() else w.lower()
                    for w in words)


def split_missing(raw: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Trim whitespace and treat blanks / 'N/A' / 'null' as missing.

    Returns (values with NaN for missing, missing mask, whitespace-fixed mask)."""
    raw = raw.fillna("").astype(str)
    stripped = raw.str.strip()
    missing = stripped.eq("") | stripped.str.lower().isin(NULL_TOKENS)
    ws_fixed = (stripped != raw) & ~missing
    return stripped.mask(missing), missing, ws_fixed


def _is_proper_case(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and not text.islower() and not text.isupper()


def canonicalize(values: pd.Series, col: ColumnSpec) -> pd.Series:
    """Unify spellings that differ only by case/spacing.

    'MUMBAI', 'mumbai', 'Mumbai  ' -> one canonical form. The canonical form is the
    allowed value if the profile lists them, otherwise the most frequent properly-cased
    spelling in the file (falling back to title case). Correct values are never altered.
    """
    collapsed = values.str.replace(r"\s+", " ", regex=True)
    key = collapsed.str.lower()
    canon: dict[str, str] = {}
    if col.allowed:
        canon.update({a.lower(): a for a in col.allowed})
    counts = collapsed.dropna().value_counts()
    for form, n in sorted(counts.items(), key=lambda kv: (-_is_proper_case(kv[0]), -kv[1])):
        canon.setdefault(form.lower(), form if _is_proper_case(form) else smart_title(form))
    return key.map(canon, na_action="ignore")


def clean_string(values: pd.Series, col: ColumnSpec) -> ColumnResult:
    idx = values.index
    present = values.notna()
    v = values.copy()
    if col.case == "title":
        v = canonicalize(v, col)
    elif col.case == "upper":
        v = v.str.upper()
    if col.synonyms:
        lookup = {k.lower(): s for k, s in col.synonyms.items()}
        v = v.map(lambda x: lookup.get(x.lower(), x), na_action="ignore")
    v = v.where(present)

    bad_value = _false(idx)
    msg = pd.Series("", index=idx)
    if col.pattern:
        ok = v.fillna("").str.fullmatch(col.pattern)
        bad = present & ~ok
        hint = f" (expected like {col.pattern_hint})" if col.pattern_hint else ""
        msg = msg.mask(bad, values.map(lambda x: f"{col.name}: '{x}' has an invalid format{hint}",
                                       na_action="ignore"))
        bad_value |= bad
    if col.allowed:
        bad = present & ~v.isin(col.allowed) & ~bad_value
        allowed = ", ".join(col.allowed)
        msg = msg.mask(bad, values.map(lambda x: f"{col.name}: '{x}' is not one of [{allowed}]",
                                       na_action="ignore"))
        bad_value |= bad

    changed = present & (v != values)
    return ColumnResult(v, _false(idx), bad_value, changed, msg)


_CURRENCY = re.compile(r"^(?:rs\.?|inr|₹|\$)\s*|,", re.IGNORECASE)


def _range_msgs(col: ColumnSpec, num: pd.Series, raw: pd.Series) -> tuple[pd.Series, pd.Series]:
    bad = _false(num.index)
    if col.min is not None:
        bad |= num.notna() & (num < float(col.min))
    if col.max is not None:
        bad |= num.notna() & (num > float(col.max))
    lo = col.min if col.min is not None else "-∞"
    hi = col.max if col.max is not None else "∞"
    msg = pd.Series("", index=num.index).mask(
        bad, raw.map(lambda x: f"{col.name}: {x} is outside the allowed range {lo} to {hi}",
                     na_action="ignore"))
    return bad, msg


def clean_integer(values: pd.Series, col: ColumnSpec) -> ColumnResult:
    present = values.notna()
    text = values.str.replace(",", "", regex=False)
    num = pd.to_numeric(text, errors="coerce")
    bad_type = present & (num.isna() | (num % 1 != 0))
    num = num.where(~bad_type)
    bad_value, msg = _range_msgs(col, num, values)
    changed = present & ~bad_type & (text != values)
    return ColumnResult(num.round().astype("Int64"), bad_type, bad_value, changed, msg)


def clean_decimal(values: pd.Series, col: ColumnSpec) -> ColumnResult:
    present = values.notna()
    text = values.str.replace(_CURRENCY, "", regex=True).str.strip()
    num = pd.to_numeric(text, errors="coerce")
    bad_type = present & num.isna()
    bad_value, msg = _range_msgs(col, num, values)
    changed = present & ~bad_type & (text != values)
    return ColumnResult(num.round(2), bad_type, bad_value, changed, msg)


def clean_date(values: pd.Series, col: ColumnSpec, today: date) -> ColumnResult:
    idx = values.index
    present = values.notna()
    parsed = pd.Series(pd.NaT, index=idx, dtype="datetime64[ns]")
    matched_fmt = pd.Series(-1, index=idx)
    for i, fmt in enumerate(col.formats or ("%Y-%m-%d",)):
        todo = present & parsed.isna()
        if not todo.any():
            break
        attempt = pd.to_datetime(values[todo], format=fmt, errors="coerce")
        hit = attempt.notna()
        parsed.loc[attempt.index[hit]] = attempt[hit]
        matched_fmt.loc[attempt.index[hit]] = i

    bad_type = present & parsed.isna()
    msg = pd.Series("", index=idx)
    bad_value = _false(idx)
    if col.min is not None:
        lo = pd.Timestamp(col.min)
        bad = parsed.notna() & (parsed < lo)
        msg = msg.mask(bad, values.map(lambda x: f"{col.name}: {x} is before {col.min}",
                                       na_action="ignore"))
        bad_value |= bad
    if col.not_future:
        bad = parsed.notna() & (parsed > pd.Timestamp(today)) & ~bad_value
        msg = msg.mask(bad, values.map(lambda x: f"{col.name}: {x} is in the future",
                                       na_action="ignore"))
        bad_value |= bad
    changed = matched_fmt > 0  # parsed with a non-standard format, now ISO
    return ColumnResult(parsed.dt.date.where(parsed.notna()), bad_type, bad_value, changed, msg)


TYPE_ERROR_TEXT = {
    "integer": "is not a whole number",
    "decimal": "is not a number",
    "date": "is not a valid date",
    "string": "has an invalid value",
}
