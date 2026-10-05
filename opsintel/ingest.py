"""The upload workflow (what happens when the user clicks "Upload and process").

    1. Fingerprint the file (SHA-256) and refuse a file that was already loaded
    2. Save the original in S3              raw/
    3. Run the ETL (pandas)                 validate -> clean -> deduplicate -> transform
    4. Save outputs in S3                   processed/  and  rejected/
    5. Load into RDS in ONE transaction     uploads + orders + rejected_rows, all or nothing
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import text

from opsintel import db, etl, storage

log = logging.getLogger(__name__)

ORDER_COLUMNS = ["order_id", "order_date", "order_month", "region", "state", "city", "category",
                 "product", "quantity", "unit_price", "revenue", "status", "delivery_days", "is_late"]


class UploadRejected(Exception):
    """A clear, user-facing reason the file was not accepted."""


@dataclass
class UploadSummary:
    upload_id: int
    file_name: str
    total_rows: int
    loaded_rows: int
    rejected_rows: int
    duplicate_rows: int
    fixed_values: int
    seconds: float


def process_upload(file_name: str, data: bytes,
                   progress: Callable[[str], None] = lambda step: None) -> UploadSummary:
    started = time.time()
    file_name = re.sub(r"[^A-Za-z0-9._ -]", "_", file_name.split("/")[-1].split("\\")[-1])[:120] or "upload.csv"
    log.info("Upload received: %s (%d bytes)", file_name, len(data))

    # 1. duplicate-file check
    file_hash = hashlib.sha256(data).hexdigest()
    earlier = db.query("upload_by_hash", {"file_hash": file_hash})
    if len(earlier):
        raise UploadRejected(f"This exact file was already uploaded as '{earlier.iloc[0]['file_name']}'.")

    # quick structure check before anything is stored (instant, clear errors)
    try:
        etl.check_columns(etl.extract(file_name, data))
    except etl.DataError as exc:
        log.warning("Upload refused (%s): %s", file_name, exc)
        raise UploadRejected(str(exc)) from exc

    # 2. original file to S3
    folder = f"{time.strftime('%Y%m%d-%H%M%S')}-{file_hash[:8]}"
    raw_key = f"raw/{folder}/{file_name}"
    storage.save(raw_key, data)
    progress("stored")

    # 3. ETL
    result = etl.run(file_name, data, existing_ids_lookup=_existing_ids)
    progress("processed")

    # 4. outputs to S3
    clean_key, rejected_key = f"processed/{folder}/clean.csv", f"rejected/{folder}/rejected.csv"
    storage.save(clean_key, result.clean.reindex(columns=ORDER_COLUMNS).to_csv(index=False).encode())
    storage.save(rejected_key, result.problems.to_csv(index=False).encode())

    # 5. one database transaction: if any insert fails, nothing for this file is kept
    with db.engine().begin() as conn:
        upload_id = conn.execute(text(
            """INSERT INTO uploads (file_name, file_hash, s3_raw_key, s3_clean_key, s3_rejected_key,
                                    total_rows, loaded_rows, rejected_rows, duplicate_rows, fixed_values)
               VALUES (:file_name, :file_hash, :raw, :clean, :rejected,
                       :total, :loaded, :rejected_rows, :duplicates, :fixed)"""),
            {"file_name": file_name, "file_hash": file_hash, "raw": raw_key, "clean": clean_key,
             "rejected": rejected_key, "total": result.total_rows, "loaded": result.loaded_rows,
             "rejected_rows": result.rejected_rows, "duplicates": result.duplicate_rows,
             "fixed": result.fixed_values}).lastrowid
        if result.loaded_rows:
            orders = result.clean[ORDER_COLUMNS].assign(upload_id=upload_id)
            orders = orders.astype(object).where(orders.notna(), None)   # NaN -> SQL NULL
            orders.to_sql("orders", conn, if_exists="append", index=False, chunksize=1000, method="multi")
        if len(result.problems):
            result.problems.assign(upload_id=upload_id).to_sql(
                "rejected_rows", conn, if_exists="append", index=False, chunksize=1000, method="multi")
    progress("loaded")

    summary = UploadSummary(upload_id, file_name, result.total_rows, result.loaded_rows,
                            result.rejected_rows, result.duplicate_rows, result.fixed_values,
                            round(time.time() - started, 2))
    log.info("Upload %s loaded: %d of %d rows (%d rejected, %d duplicates) in %.2fs",
             file_name, summary.loaded_rows, summary.total_rows, summary.rejected_rows,
             summary.duplicate_rows, summary.seconds)
    return summary


def _existing_ids(ids: list[str]) -> set[str]:
    """Which of these order ids are already in RDS? Asked in chunks of 1,000."""
    found: set[str] = set()
    for i in range(0, len(ids), 1000):
        found |= set(db.query("existing_order_ids", {"ids": ids[i:i + 1000]})["order_id"])
    return found


def as_csv(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.replace({np.nan: None}).to_csv(buf, index=False)
    return buf.getvalue().encode()
