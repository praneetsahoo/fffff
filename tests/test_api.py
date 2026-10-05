"""Phase 5: HTTP API (runs on SQLite and MySQL)."""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"


@pytest.fixture
def api(db_env):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def loaded(api):
    """API with both demo batches uploaded and processed."""
    ids = []
    for name in ("operations_orders.csv", "operations_orders_batch2.csv"):
        r = api.post("/api/uploads", files={"file": (name, (SAMPLES / name).read_bytes(), "text/csv")})
        assert r.status_code == 202, r.text
        ids.append(r.json()["id"])
    return api, ids


def err(r) -> dict:
    return r.json()["error"]


# ---------- uploads ----------

def test_upload_flow(loaded):
    api, (first, second) = loaded
    up = api.get(f"/api/uploads/{first}").json()
    assert up["status"] == "COMPLETED" and up["total_rows"] == 5150
    assert [u["id"] for u in api.get("/api/uploads").json()][:2] == [second, first]


@pytest.mark.parametrize("name,code,status", [
    ("empty.csv", "empty_file", 422),
    ("header_only.csv", "no_data", 422),
    ("missing_columns.csv", "missing_columns", 422),
    ("not_really_csv.csv", "unsupported_format", 415),
    ("notes.txt", "unsupported_format", 415),
])
def test_bad_files_get_immediate_clear_errors(api, name, code, status):
    data = (SAMPLES / "edge_cases" / name).read_bytes()
    r = api.post("/api/uploads", files={"file": (name, data, "text/csv")})
    assert r.status_code == status and err(r)["code"] == code and err(r)["message"]
    assert api.get("/api/uploads").json() == []  # nothing stored for rejected files


def test_missing_file_field(api):
    r = api.post("/api/uploads")
    assert r.status_code == 422 and err(r)["code"] == "validation_error"


def test_oversized_file(api, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    from app.config import get_settings
    get_settings.cache_clear()
    r = api.post("/api/uploads", files={"file": ("big.csv", b"a" * (1024 * 1024 + 10), "text/csv")})
    assert r.status_code == 413 and err(r)["code"] == "file_too_large"


def test_duplicate_file_conflict(loaded):
    api, (first, _) = loaded
    data = (SAMPLES / "operations_orders.csv").read_bytes()
    r = api.post("/api/uploads", files={"file": ("copy.csv", data, "text/csv")})
    assert r.status_code == 409 and err(r)["details"]["upload_id"] == first


def test_unknown_upload_404(api):
    for path in ("", "/quality", "/rejected", "/files/raw"):
        r = api.get(f"/api/uploads/nope{path}")
        assert r.status_code == 404, path


def test_retry_rules(loaded):
    api, (first, _) = loaded
    r = api.post(f"/api/uploads/{first}/retry")
    assert r.status_code == 409 and err(r)["code"] == "not_retryable"


def test_quality_report(loaded):
    api, (first, _) = loaded
    rep = api.get(f"/api/uploads/{first}/quality").json()
    up = rep["upload"]
    assert up["valid_rows"] + up["invalid_rows"] + up["duplicate_rows"] == up["total_rows"]
    assert rep["rejected_by_kind"]["duplicate"] == up["duplicate_rows"]
    assert rep["issues_by_type"]["missing"] > 0
    assert {"column", "issue_type", "count"} <= set(rep["issues"][0])


def test_rejected_rows_paged_and_filtered(loaded):
    api, (first, _) = loaded
    up = api.get(f"/api/uploads/{first}").json()
    page = api.get(f"/api/uploads/{first}/rejected?page_size=10").json()
    assert page["total"] == up["invalid_rows"] + up["duplicate_rows"]
    assert len(page["items"]) == 10 and page["items"][0]["reasons"]
    dups = api.get(f"/api/uploads/{first}/rejected?kind=duplicate&page_size=100").json()
    assert dups["total"] == up["duplicate_rows"]
    assert all(i["kind"] == "duplicate" for i in dups["items"])


def test_file_downloads(loaded):
    api, (first, _) = loaded
    for kind, first_col in (("raw", "order_id"), ("processed", "order_id"), ("rejected", "row_number")):
        r = api.get(f"/api/uploads/{first}/files/{kind}")
        assert r.status_code == 200 and r.text.startswith(first_col)
        assert "attachment" in r.headers["content-disposition"]
    assert api.get(f"/api/uploads/{first}/files/secret").status_code == 422


# ---------- records ----------

def test_records_pagination_and_totals(loaded):
    api, _ = loaded
    dash = api.get("/api/dashboard").json()
    p1 = api.get("/api/records?page_size=50").json()
    assert p1["total"] == dash["kpis"]["orders"] and len(p1["items"]) == 50
    p2 = api.get("/api/records?page_size=50&page=2").json()
    assert not {o["order_id"] for o in p1["items"]} & {o["order_id"] for o in p2["items"]}


def test_records_filters_and_search(loaded):
    api, _ = loaded
    r = api.get("/api/records?region=East&category=Electronics&status=Delivered&page_size=100").json()
    assert r["total"] > 0
    assert all((o["region"], o["category"], o["status"]) == ("East", "Electronics", "Delivered")
               for o in r["items"])
    s = api.get("/api/records?q=mumbai").json()
    assert s["total"] > 0 and all(o["city"] == "Mumbai" for o in s["items"])
    one = api.get("/api/records?q=ORD-100001").json()
    assert one["total"] <= 1
    multi = api.get("/api/records?region=East&region=West").json()
    assert multi["total"] > r["total"]


def test_records_date_and_sla_filters(loaded):
    api, _ = loaded
    r = api.get("/api/records?date_from=2026-09-01&date_to=2026-09-30&page_size=100").json()
    assert all("2026-09-01" <= o["order_date"] <= "2026-09-30" for o in r["items"])
    late = api.get("/api/records?sla_breached=true&page_size=100").json()
    assert late["total"] > 0 and all(o["sla_breached"] is True for o in late["items"])


def test_records_sorting(loaded):
    api, _ = loaded
    items = api.get("/api/records?sort=revenue&order=desc&page_size=20").json()["items"]
    revs = [o["revenue"] for o in items]
    assert revs == sorted(revs, reverse=True)


@pytest.mark.parametrize("query", [
    "sort=password", "order=sideways", "page=0", "page_size=1000",
    "date_from=2026-09-30&date_to=2026-01-01", "date_from=notadate", "sla_breached=maybe",
])
def test_records_invalid_params_rejected(api, query):
    r = api.get(f"/api/records?{query}")
    assert r.status_code == 422 and "error" in r.json()


def test_search_input_is_safe(loaded):
    api, _ = loaded
    for q in ("%", "_", "' OR 1=1 --", "\\", "Robert'); DROP TABLE orders;--"):
        r = api.get("/api/records", params={"q": q})
        assert r.status_code == 200 and r.json()["total"] == 0
    assert api.get("/api/records").json()["total"] > 0  # table still there


def test_records_export(loaded):
    api, _ = loaded
    r = api.get("/api/records/export?region=North")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert r.status_code == 200 and rows and all(x["region"] == "North" for x in rows)
    assert int(r.headers["x-row-count"]) == len(rows)


# ---------- dashboard / insights / meta ----------

def test_dashboard_numbers_match_records(loaded):
    api, _ = loaded
    d = api.get("/api/dashboard").json()
    assert sum(c["count"] for c in d["by_category"]) == d["kpis"]["orders"]
    assert sum(m["orders"] for m in d["monthly"]) == d["kpis"]["orders"]
    assert abs(sum(c["value"] for c in d["by_region"]) - d["kpis"]["revenue"]) < 1
    dq = d["data_quality"]
    assert dq["records_valid"] == d["kpis"]["orders"]
    assert dq["records_received"] == dq["records_valid"] + dq["records_invalid"] + dq["records_duplicate"]
    assert len(d["top_cities"]) == 10


def test_dashboard_filters(loaded):
    api, _ = loaded
    east = api.get("/api/dashboard?region=East").json()
    assert [r["label"] for r in east["by_region"]] == ["East"]
    assert east["sla_by_region"][0]["breach_pct"] > 10  # planted delivery problem


def test_dashboard_empty_state(api):
    d = api.get("/api/dashboard").json()
    assert d["kpis"]["orders"] == 0 and d["monthly"] == [] and d["kpis"]["sla_breach_pct"] is None
    ins = api.get("/api/insights").json()
    assert ins[0]["title"] == "No data yet"


def test_insights_find_planted_stories(loaded):
    api, _ = loaded
    titles = " | ".join(i["title"] for i in api.get("/api/insights").json())
    assert "Delivery delays rising in East" in titles
    assert "Electronics demand surging" in titles


def test_meta(loaded):
    api, _ = loaded
    m = api.get("/api/meta").json()
    assert m["filter_options"]["region"] == ["Central", "East", "North", "South", "West"]
    assert "order_id" in m["required_columns"] and m["date_range"]["max"] == "2026-09-30"


def test_openapi_docs_available(api):
    assert api.get("/docs").status_code == 200
    assert "/api/records" in api.get("/openapi.json").json()["paths"]
