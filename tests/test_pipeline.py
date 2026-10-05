"""Phase 3: validation, cleaning, de-duplication and scoring rules."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.errors import InvalidFileError
from app.pipeline import clean_csv_bytes, rejected_csv_bytes, run_pipeline
from app.profile import load_profile

PROFILE = load_profile("operations_orders")
TODAY = date(2026, 10, 5)
HEADER = "order_id,order_date,region,state,city,category,product,quantity,unit_price,status,delivery_days"
GOOD = "ORD-000001,2026-05-01,North,Delhi,Delhi,Grocery,Atta 10kg,2,499.00,Delivered,3"
SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"


def run(*lines: str, header: str = HEADER, existing=frozenset()):
    data = ("\n".join([header, *lines]) + "\n").encode()
    return run_pipeline(data, "t.csv", PROFILE, existing_keys=existing, today=TODAY)


def reasons(result) -> list[str]:
    return [m for r in result.rejected for m in r["reasons"]]


# ---------- file-level rejection ----------

@pytest.mark.parametrize("data,name,code", [
    (b"", "a.csv", "empty_file"),
    (b"   \n\n", "a.csv", "empty_file"),
    (HEADER.encode() + b"\n", "a.csv", "no_data"),
    (b"order_id,region\nORD-000001,North\n", "a.csv", "missing_columns"),
    (b"\x89PNG\r\n\x1a\n\x00\x00", "a.csv", "unsupported_format"),
    (b"hello", "a.xlsx", "unsupported_format"),
    (b"a,a,b\n1,2,3\n", "a.csv", "duplicate_columns"),
])
def test_bad_files_rejected_with_clear_code(data, name, code):
    with pytest.raises(InvalidFileError) as exc:
        run_pipeline(data, name, PROFILE, today=TODAY)
    assert exc.value.code == code
    assert exc.value.message


def test_missing_columns_error_lists_them():
    with pytest.raises(InvalidFileError) as exc:
        run_pipeline(b"order_id,order_date\nORD-000001,2026-01-01\n", "a.csv", PROFILE)
    assert "city" in exc.value.details["missing_columns"]


def test_headers_are_normalised_and_optional_columns_may_be_absent():
    header = "Order ID,Order-Date,Region,City,Category,Product,Quantity,Unit Price,Status"
    r = run("ORD-000001,2026-05-01,North,Delhi,Grocery,Atta 10kg,2,499,Shipped", header=header)
    assert r.valid_rows == 1
    assert r.clean.loc[0, "state"] == "Unknown"  # filled default


def test_semicolon_delimiter_and_cp1252_encoding():
    text = HEADER.replace(",", ";") + "\n" + GOOD.replace(",", ";").replace("Atta 10kg", "Café Mix") + "\n"
    r = run_pipeline(text.encode("cp1252"), "t.csv", PROFILE, today=TODAY)
    assert r.valid_rows == 1 and r.clean.loc[0, "product"] == "Café Mix"


def test_malformed_line_rejected_but_file_processed():
    r = run(GOOD, "ORD-000002,2026-05-01,North")
    assert r.valid_rows == 1 and r.invalid_rows == 1
    assert r.rejected[0]["kind"] == "malformed"


def test_blank_lines_ignored():
    r = run(GOOD, "", ",,,,,,,,,,")
    assert r.total_rows == 1 and r.valid_rows == 1


# ---------- row-level validation ----------

@pytest.mark.parametrize("bad_row,fragment", [
    (GOOD.replace("Delhi,Delhi", "Delhi,"), "city: required value is missing"),
    (GOOD.replace(",2,499.00", ",abc,499.00"), "quantity: 'abc' is not a whole number"),
    (GOOD.replace(",2,499.00", ",2.5,499.00"), "not a whole number"),
    (GOOD.replace(",2,499.00", ",-3,499.00"), "outside the allowed range"),
    (GOOD.replace("499.00", "free"), "unit_price: 'free' is not a number"),
    (GOOD.replace("2026-05-01", "31/02/2026"), "is not a valid date"),
    (GOOD.replace("2026-05-01", "2027-01-01"), "is in the future"),
    (GOOD.replace("2026-05-01", "2019-12-31"), "is before 2020-01-01"),
    (GOOD.replace("Delivered", "Lost"), "status: 'Lost' is not one of"),
    (GOOD.replace("Grocery", "Toys"), "category: 'Toys' is not one of"),
    (GOOD.replace("ORD-000001", "12345"), "invalid format (expected like ORD-123456)"),
    (GOOD.replace(",3", ",99"), "delivery_days: 99 is outside"),
])
def test_invalid_rows_rejected_with_reason(bad_row, fragment):
    r = run(bad_row)
    assert r.valid_rows == 0 and r.invalid_rows == 1
    assert any(fragment in m for m in reasons(r)), reasons(r)


def test_all_reasons_collected_not_just_first():
    r = run("ORD-000001,yesterday,North,,,Toys,X,abc,1,Lost,")
    msgs = reasons(r)
    assert len(msgs) >= 4  # date, city, category, quantity, status


def test_null_tokens_treated_as_missing():
    r = run(GOOD.replace("499.00", "N/A"))
    assert any("unit_price: required value is missing" in m for m in reasons(r))


# ---------- cleaning / standardisation ----------

def test_standardisation():
    r = run("ord-000001, 01/05/2026 ,n,DELHI,bombay,groceries,ATTA 10KG,\"1,000\",Rs. 499,completed,2",
            GOOD.replace("ORD-000001", "ORD-000002"))
    row = r.clean.set_index("order_id").loc["ORD-000001"]
    assert row["order_date"] == date(2026, 5, 1)
    assert (row["region"], row["city"], row["category"], row["status"]) == \
        ("North", "Mumbai", "Grocery", "Delivered")
    assert row["product"] == "Atta 10kg"  # canonical form taken from the well-formed row
    assert row["quantity"] == 1000 and row["unit_price"] == 499.0
    assert r.issues[("city", "standardized")] == 1


def test_three_date_formats_parse_to_same_date():
    r = run(GOOD, GOOD.replace("ORD-000001", "ORD-000002").replace("2026-05-01", "01/05/2026"),
            GOOD.replace("ORD-000001", "ORD-000003").replace("2026-05-01", "01-May-2026"))
    assert set(r.clean["order_date"]) == {date(2026, 5, 1)}
    assert r.issues[("order_date", "standardized")] == 2


def test_correct_values_are_not_altered():
    r = run(GOOD.replace("Atta 10kg", "LED Bulb Pack"))
    assert r.clean.loc[0, "product"] == "LED Bulb Pack"
    assert r.rows_needing_fixes == 0 and r.quality_score == 100.0


def test_delivery_days_only_expected_for_completed_orders():
    shipped = GOOD.replace("Delivered", "Shipped").replace(",3", ",")
    delivered = GOOD.replace("ORD-000001", "ORD-000002").replace(",3", ",")
    r = run(shipped, delivered)
    assert r.valid_rows == 2
    assert r.issues[("delivery_days", "missing")] == 1


# ---------- duplicates ----------

def test_exact_duplicate_removed():
    r = run(GOOD, GOOD)
    assert r.valid_rows == 1 and r.duplicate_rows == 1
    assert "exact duplicate of row 2" in reasons(r)[0]


def test_conflicting_duplicate_keeps_first():
    r = run(GOOD, GOOD.replace(",2,499.00", ",9,499.00"))
    assert r.valid_rows == 1 and r.clean.loc[0, "quantity"] == 2
    assert "with different values" in reasons(r)[0]


def test_duplicate_detected_after_standardisation():
    r = run(GOOD, GOOD.replace("2026-05-01", "01/05/2026").replace("North", "north"))
    assert r.duplicate_rows == 1


def test_previously_loaded_key_is_duplicate():
    r = run(GOOD, existing={"ORD-000001"})
    assert r.valid_rows == 0 and r.duplicate_rows == 1
    assert "previous upload" in reasons(r)[0]


# ---------- derived metrics & accounting ----------

def test_derived_metrics():
    r = run(GOOD.replace(",3", ",7"))
    row = r.clean.loc[0]
    assert row["revenue"] == 998.0 and row["order_month"] == "2026-05"
    assert bool(row["sla_breached"]) is True


def test_every_row_accounted_for_on_demo_file():
    data = (SAMPLES / "operations_orders.csv").read_bytes()
    r = run_pipeline(data, "operations_orders.csv", PROFILE, today=TODAY)
    assert r.total_rows == r.valid_rows + r.invalid_rows + r.duplicate_rows
    assert r.total_rows == 5150
    assert 90 < r.success_rate < 97
    assert r.clean["order_id"].is_unique
    assert set(r.clean["region"]) == {"North", "South", "East", "West", "Central"}
    assert len(r.rejected) == r.invalid_rows + r.duplicate_rows


def test_output_csvs():
    r = run(GOOD, GOOD.replace("Delivered", "Lost").replace("000001", "000002"))
    clean = clean_csv_bytes(r, PROFILE).decode()
    rejected = rejected_csv_bytes(r, PROFILE).decode()
    assert clean.splitlines()[0].endswith("revenue,order_month,sla_breached")
    assert len(clean.splitlines()) == 2
    assert "Lost" in rejected and "row_number,kind,reasons" in rejected.splitlines()[0]
