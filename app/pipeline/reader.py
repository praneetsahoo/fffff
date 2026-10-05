"""Stage 1 — file-level checks and parsing.

Rejects the WHOLE file when it cannot be a dataset (empty, binary, wrong format,
missing required columns, no data rows). Individual malformed lines (wrong number
of fields) do NOT fail the file: they are returned so the pipeline can reject
just those rows with a reason.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field

import pandas as pd

from app.errors import InvalidFileError
from app.profile import DatasetProfile

log = logging.getLogger(__name__)

ROW_COL = "_row"  # 1-based line number in the original file (header is line 1)


@dataclass
class ParsedFile:
    df: pd.DataFrame                       # profile columns (as text) + _row
    malformed: list[dict] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)
    blank_lines: int = 0
    encoding: str = "utf-8"
    delimiter: str = ","


def normalize_header(name: str) -> str:
    name = name.strip().lstrip("﻿").lower()
    return re.sub(r"[\s\-]+", "_", name)


def _decode(data: bytes) -> tuple[str, str]:
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        # Excel on Windows often exports cp1252; accept it rather than failing.
        try:
            return data.decode("cp1252"), "cp1252"
        except UnicodeDecodeError as exc:
            raise InvalidFileError(
                "The file's text encoding could not be read. Please save it as UTF-8 CSV."
            ) from exc


def _sniff_delimiter(sample: str) -> str:
    first_line = sample.splitlines()[0] if sample else ""
    counts = {d: first_line.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def parse_csv(data: bytes, filename: str, profile: DatasetProfile) -> ParsedFile:
    if not filename.lower().endswith(".csv"):
        raise InvalidFileError(
            f"Unsupported file type '{filename}'. Please upload a .csv file.",
            code="unsupported_format", status_code=415)
    if not data or not data.strip():
        raise InvalidFileError("The file is empty.", code="empty_file")
    if b"\x00" in data[:8192]:
        raise InvalidFileError(
            "This does not look like a CSV text file (binary content detected).",
            code="unsupported_format", status_code=415)

    text, encoding = _decode(data)
    delimiter = _sniff_delimiter(text[:4096])
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)

    try:
        header_raw = next(reader, None)
        if not header_raw or not any(h.strip() for h in header_raw):
            raise InvalidFileError("The file has no header row.", code="no_header")
        header = [normalize_header(h) for h in header_raw]

        dupes = sorted({h for h in header if header.count(h) > 1 and h})
        if dupes:
            raise InvalidFileError(f"Duplicate column names in header: {', '.join(dupes)}",
                                   code="duplicate_columns", details={"columns": dupes})

        missing = [c for c in profile.required_columns if c not in header]
        if missing:
            raise InvalidFileError(
                f"Missing required column(s): {', '.join(missing)}.",
                code="missing_columns",
                details={"missing_columns": missing, "expected_columns": profile.column_names,
                         "found_columns": header},
            )

        rows: list[list[str]] = []
        lines: list[int] = []
        malformed: list[dict] = []
        blank = 0
        for rec in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in rec):
                blank += 1
                continue
            if len(rec) != len(header):
                malformed.append({
                    "row_number": line,
                    "raw": {f"field_{i + 1}": v for i, v in enumerate(rec)},
                    "reasons": [f"malformed row: expected {len(header)} fields, found {len(rec)}"],
                    "kind": "malformed",
                })
                continue
            rows.append(rec)
            lines.append(line)
    except csv.Error as exc:
        raise InvalidFileError(f"The CSV could not be parsed: {exc}", code="parse_error") from exc

    if not rows and not malformed:
        raise InvalidFileError("The file has a header but no data rows.", code="no_data")

    df = pd.DataFrame(rows, columns=header, dtype=str)
    extra = [c for c in header if c not in profile.column_names]
    for c in profile.column_names:
        if c not in df.columns:
            df[c] = ""  # optional column absent from the file
    df = df[profile.column_names].copy()
    df[ROW_COL] = lines

    log.info("Parsed %s: %d rows, %d malformed, %d blank, encoding=%s, delimiter=%r",
             filename, len(df), len(malformed), blank, encoding, delimiter)
    return ParsedFile(df=df.reset_index(drop=True), malformed=malformed, extra_columns=extra,
                      blank_lines=blank, encoding=encoding, delimiter=delimiter)
