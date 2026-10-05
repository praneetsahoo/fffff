"""Live end-to-end test of a running OpsIntel deployment, over HTTP only (stdlib, no installs).

Runs the brief's full demonstration and the "judges will break it" cases, then checks that
every number agrees across the API. Safe to re-run: files that were already uploaded are
reported as duplicates (409), which is itself one of the checks.

    python scripts/smoke_test.py http://<server>
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "sample_data"
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}{' — ' + detail if detail else ''}", flush=True)


def request(method: str, url: str, body: bytes | None = None,
            headers: dict | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def get_json(base: str, path: str, params: dict | None = None):
    url = base + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    status, _, body = request("GET", url, headers={"Accept": "application/json"})
    return status, json.loads(body)


def upload(base: str, name: str, data: bytes) -> tuple[int, dict]:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: text/csv\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    status, _, resp = request("POST", base + "/api/uploads", body,
                              {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        return status, json.loads(resp)
    except json.JSONDecodeError:
        return status, {"error": {"code": "non_json", "message": resp[:120].decode(errors="replace")}}


def wait_done(base: str, upload_id: str, timeout: float = 120) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        _, up = get_json(base, f"/api/uploads/{upload_id}")
        if up["status"] in ("COMPLETED", "FAILED"):
            return up
        time.sleep(1)
    raise TimeoutError(upload_id)


def main(base: str) -> int:
    base = base.rstrip("/")

    # ---- health & page ----
    status, h = get_json(base, "/api/health")
    check("health: database and storage reachable", status == 200 and h["status"] == "ok",
          f"storage={h.get('storage_backend')}, checks={h.get('checks')}")
    status, headers, page = request("GET", base + "/")
    check("web app served with security headers",
          status == 200 and b"OpsIntel" in page
          and "content-security-policy" in {k.lower() for k in headers})

    # ---- the demonstration: upload both datasets ----
    uploaded = {}
    for name in ("operations_orders.csv", "operations_orders_batch2.csv"):
        t0 = time.time()
        status, body = upload(base, name, (SAMPLES / name).read_bytes())
        if status == 202:
            up = wait_done(base, body["id"])
            check(f"upload {name}: processed", up["status"] == "COMPLETED",
                  f"{up['valid_rows']}/{up['total_rows']} loaded, {up['success_rate']}% in "
                  f"{time.time() - t0:.1f}s")
            uploaded[name] = up
        elif status == 409:
            check(f"upload {name}: already present (duplicate-file guard)",
                  body["error"]["code"] == "duplicate_upload")
            _, up = get_json(base, f"/api/uploads/{body['error']['details']['upload_id']}")
            uploaded[name] = up
        else:
            check(f"upload {name}", False, f"HTTP {status}: {body}")
            return 1

    for name, up in uploaded.items():
        check(f"{name}: every row accounted for",
              up["total_rows"] == up["valid_rows"] + up["invalid_rows"] + up["duplicate_rows"],
              f"{up['total_rows']} = {up['valid_rows']} + {up['invalid_rows']} + {up['duplicate_rows']}")
        check(f"{name}: raw/processed/rejected stored",
              all(up[k] for k in ("raw_key", "processed_key", "rejected_key")))
        for kind, first in (("raw", b"order_id"), ("processed", b"order_id"), ("rejected", b"row_number")):
            st, _, data = request("GET", f"{base}/api/uploads/{up['id']}/files/{kind}")
            check(f"{name}: {kind} file downloadable from storage", st == 200 and data.startswith(first),
                  f"{len(data):,} bytes")

    # ---- again: duplicate file ----
    status, body = upload(base, "copy.csv", (SAMPLES / "operations_orders.csv").read_bytes())
    check("re-upload of same file blocked", status == 409 and body["error"]["code"] == "duplicate_upload")

    # ---- judges break it ----
    for name, want_status, want_code in (
        ("empty.csv", 422, "empty_file"), ("header_only.csv", 422, "no_data"),
        ("missing_columns.csv", 422, "missing_columns"),
        ("not_really_csv.csv", 415, "unsupported_format"), ("notes.txt", 415, "unsupported_format"),
    ):
        status, body = upload(base, name, (SAMPLES / "edge_cases" / name).read_bytes())
        check(f"bad file {name} rejected clearly", status == want_status and body["error"]["code"] == want_code,
              body.get("error", {}).get("message", ""))
    status, _, resp = request("POST", base + "/api/uploads", b"x" * (12 * 1024 * 1024),
                              {"Content-Type": "multipart/form-data; boundary=zz"})
    check("oversized upload rejected", status == 413, resp[:80].decode(errors="replace"))
    for q in ("sort=password", "page=0", "date_from=2026-09-30&date_to=2026-01-01"):
        status, body = get_json(base, "/api/records?" + q)
        check(f"invalid query rejected ({q})", status == 422 and "error" in body)
    status, body = get_json(base, "/api/records", {"q": "'); DROP TABLE orders;--"})
    check("SQL-injection attempt is just a search", status == 200 and body["total"] == 0)

    # ---- dashboard numbers agree with each other ----
    _, d = get_json(base, "/api/dashboard")
    k, dq = d["kpis"], d["data_quality"]
    check("dashboard orders = sum of categories", sum(c["count"] for c in d["by_category"]) == k["orders"])
    check("dashboard orders = sum of months", sum(m["orders"] for m in d["monthly"]) == k["orders"])
    check("valid records = dashboard orders", dq["records_valid"] == k["orders"],
          f"{k['orders']:,} orders, revenue {k['revenue']:,.0f}")
    status, recs = get_json(base, "/api/records", {"page_size": 1})
    check("records total = dashboard orders", recs["total"] == k["orders"])
    _, east = get_json(base, "/api/dashboard", {"region": "East"})
    check("region filter applies", [r["label"] for r in east["by_region"]] == ["East"])

    _, ins = get_json(base, "/api/insights")
    titles = [i["title"] for i in ins]
    check("insights generated", len(ins) >= 3, "; ".join(titles))

    _, srch = get_json(base, "/api/records", {"q": "mumbai", "category": "Electronics",
                                              "sort": "revenue", "order": "desc", "page_size": 5})
    revs = [o["revenue"] for o in srch["items"]]
    check("search + filter + sort", srch["total"] > 0 and revs == sorted(revs, reverse=True)
          and all(o["city"] == "Mumbai" for o in srch["items"]), f"{srch['total']} matches")

    first = uploaded["operations_orders.csv"]
    _, rep = get_json(base, f"/api/uploads/{first['id']}/quality")
    check("quality report available", rep["upload"]["id"] == first["id"] and len(rep["issues"]) > 5)
    _, rej = get_json(base, f"/api/uploads/{first['id']}/rejected", {"page_size": 5})
    check("rejected rows listed with reasons", rej["total"] > 0 and all(r["reasons"] for r in rej["items"]))

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"))
