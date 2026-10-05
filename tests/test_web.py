"""Phase 6: web app serving, sample files and security headers."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def web(app_env):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def test_index_served_with_security_headers(web):
    r = web.get("/")
    assert r.status_code == 200 and "<title>OpsIntel" in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/static/app.css", "/static/js/app.js", "/static/js/ui.js",
                                  "/static/js/api.js", "/static/js/charts.js", "/static/js/motion.js",
                                  "/static/js/pages/upload.js", "/static/js/pages/dashboard.js",
                                  "/static/js/pages/records.js", "/static/js/pages/quality.js"])
def test_static_assets(web, path):
    assert web.get(path).status_code == 200


def test_frontend_never_uses_innerhtml(web):
    """Uploaded CSV values are rendered as text only (XSS protection)."""
    for path in ("/static/js/ui.js", "/static/js/charts.js", "/static/js/motion.js", "/static/js/pages/upload.js",
                 "/static/js/pages/dashboard.js", "/static/js/pages/records.js",
                 "/static/js/pages/quality.js"):
        src = web.get(path).text
        assert not re.search(r"\.(innerHTML|outerHTML)\s*\+?=|insertAdjacentHTML|document\.write", src), path


def test_docs_not_blocked_by_csp(web):
    r = web.get("/docs")
    assert r.status_code == 200 and "content-security-policy" not in r.headers


def test_samples_list_and_download(web):
    names = {s["name"]: s for s in web.get("/api/samples").json()}
    assert names["operations_orders.csv"]["kind"] == "dataset"
    assert names["empty.csv"]["kind"] == "edge_case"
    r = web.get("/api/samples/operations_orders.csv")
    assert r.status_code == 200 and r.text.startswith("order_id")


@pytest.mark.parametrize("name", ["..%2F..%2Fapp%2Fconfig.py", "nope.csv", "..%2F.env"])
def test_samples_cannot_escape_folder(web, name):
    assert web.get(f"/api/samples/{name}").status_code == 404
