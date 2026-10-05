"""End-to-end browser check: drives the real UI and saves screenshots.

Uploads a broken file and both demo files, then visits every page on desktop and mobile
and reports any JavaScript errors.

    pip install playwright && python -m playwright install chromium
    python scripts/browser_check.py http://localhost:8000 ./screenshots
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main(base: str, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    def track(page, label):
        page.on("pageerror", lambda e: errors.append(f"{label}: {e}"))
        expected = ("fonts", "ERR_TUNNEL", "409", "422")  # offline fonts; deliberate bad file (422) and re-upload (409)
        page.on("console", lambda m: m.type == "error" and not any(x in m.text for x in expected)
                and errors.append(f"{label} console: {m.text}"))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1366, "height": 900})
        track(pg, "desktop")
        pg.goto(f"{base}/#/upload")
        pg.wait_for_selector("input[type=file]")
        pg.set_input_files("input[type=file]", str(ROOT / "sample_data/edge_cases/missing_columns.csv"))
        pg.wait_for_selector(".alert")
        pg.screenshot(path=out / "1_upload_error.png", full_page=True)
        for name in ("operations_orders.csv", "operations_orders_batch2.csv"):
            pg.set_input_files("input[type=file]", str(ROOT / "sample_data" / name))
            try:
                pg.wait_for_selector("text=Open dashboard", timeout=60000)
            except Exception:  # already uploaded earlier: the 409 message is shown instead
                pg.wait_for_selector(".alert", timeout=5000)
            pg.wait_for_timeout(500)
        pg.screenshot(path=out / "2_upload_done.png", full_page=True)
        for i, route in enumerate(("dashboard", "records", "quality"), start=3):
            pg.goto(f"{base}/#/{route}")
            pg.wait_for_timeout(2500)
            pg.screenshot(path=out / f"{i}_{route}.png", full_page=True)

        m = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        track(m, "mobile")
        m.goto(f"{base}/#/dashboard")
        m.wait_for_timeout(2500)
        m.screenshot(path=out / "6_mobile_dashboard.png", full_page=True)
        browser.close()

    print(f"Screenshots saved to {out}")
    if errors:
        print("JavaScript errors:", *errors, sep="\n  ")
        return 1
    print("No JavaScript errors.")
    return 0


if __name__ == "__main__":
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    folder = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("screenshots")
    sys.exit(main(base_url.rstrip("/"), folder))
