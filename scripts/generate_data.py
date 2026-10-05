"""Generate realistic, deliberately messy operational CSV files for demos and tests.

Planted problems (so the pipeline has real work to do):
  * 3 date formats, inconsistent casing / stray whitespace, synonyms (Bombay, Completed...)
  * missing optional values (state, delivery_days)
  * invalid rows: missing required fields, wrong types, impossible values, bad dates,
    unknown status, malformed order ids, future dates
  * exact duplicate rows and conflicting duplicates of the same order_id

Planted business stories (so the dashboard has something to say):
  * Electronics demand jumps in September (festive season)
  * East region delivery times get worse from August (SLA breaches rise)

Usage:
  python scripts/generate_data.py                       # main demo file + edge cases
  python scripts/generate_data.py --rows 2000 --start-id 200000 --out sample_data/batch2.csv
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

import numpy as np

COLUMNS = ["order_id", "order_date", "region", "state", "city", "category", "product",
           "quantity", "unit_price", "status", "delivery_days"]

CITIES = {
    "North": [("Delhi", "Delhi"), ("Gurugram", "Haryana"), ("Chandigarh", "Chandigarh"),
              ("Jaipur", "Rajasthan"), ("Lucknow", "Uttar Pradesh")],
    "South": [("Bengaluru", "Karnataka"), ("Chennai", "Tamil Nadu"),
              ("Hyderabad", "Telangana"), ("Kochi", "Kerala")],
    "East": [("Kolkata", "West Bengal"), ("Bhubaneswar", "Odisha"), ("Patna", "Bihar"),
             ("Guwahati", "Assam")],
    "West": [("Mumbai", "Maharashtra"), ("Pune", "Maharashtra"), ("Ahmedabad", "Gujarat"),
             ("Panaji", "Goa")],
    "Central": [("Bhopal", "Madhya Pradesh"), ("Indore", "Madhya Pradesh"),
                ("Nagpur", "Maharashtra"), ("Raipur", "Chhattisgarh")],
}
REGION_WEIGHTS = {"North": 0.26, "South": 0.24, "West": 0.24, "East": 0.14, "Central": 0.12}

PRODUCTS = {
    "Electronics": [("Wireless Earbuds", 2499), ("Smartphone", 18999), ("Power Bank", 1299),
                    ("Smartwatch", 4999), ("Bluetooth Speaker", 2999)],
    "Grocery": [("Basmati Rice 5kg", 649), ("Cooking Oil 1L", 189), ("Atta 10kg", 499),
                ("Green Tea 100 Bags", 349), ("Dry Fruits 500g", 799)],
    "Apparel": [("Cotton T-Shirt", 499), ("Denim Jeans", 1499), ("Running Shoes", 2999),
                ("Kurta", 899), ("Winter Jacket", 2499)],
    "Home & Kitchen": [("Pressure Cooker", 1899), ("Non-Stick Pan", 999),
                       ("Mixer Grinder", 3499), ("Bedsheet Set", 1199), ("LED Bulb Pack", 399)],
    "Health & Beauty": [("Face Wash", 249), ("Shampoo 400ml", 399), ("Vitamin C Tablets", 549),
                        ("Sunscreen", 449), ("Hair Dryer", 1599)],
}
CATEGORY_WEIGHTS = {"Grocery": 0.30, "Electronics": 0.20, "Apparel": 0.20,
                    "Home & Kitchen": 0.16, "Health & Beauty": 0.14}
STATUS_WEIGHTS = {"Delivered": 0.70, "Shipped": 0.12, "Pending": 0.08,
                  "Cancelled": 0.06, "Returned": 0.04}

SYNONYMS = {
    "city": {"Mumbai": "Bombay", "Bengaluru": "Bangalore", "Kolkata": "Calcutta",
             "Chennai": "Madras", "Delhi": "New Delhi", "Gurugram": "Gurgaon"},
    "status": {"Delivered": "Completed", "Cancelled": "Canceled", "Shipped": "In Transit"},
    "category": {"Home & Kitchen": "Home and Kitchen", "Grocery": "Groceries",
                 "Apparel": "Clothing", "Health & Beauty": "Health and Beauty"},
    "region": {"North": "N", "South": "Southern", "East": "E", "West": "Western"},
}


def _pick(rng, weights: dict[str, float]) -> str:
    keys = list(weights)
    p = np.array([weights[k] for k in keys], dtype=float)
    return keys[rng.choice(len(keys), p=p / p.sum())]


def _fmt_date(rng, d: date) -> str:
    r = rng.random()
    if r < 0.70:
        return d.strftime("%Y-%m-%d")
    if r < 0.90:
        return d.strftime("%d/%m/%Y")
    return d.strftime("%d-%b-%Y")


def _messy_text(rng, value: str) -> str:
    r = rng.random()
    if r < 0.06:
        return value.lower()
    if r < 0.10:
        return value.upper()
    if r < 0.15:
        return f"  {value} "
    return value


def _clean_row(rng, order_num: int, start: date, days: int) -> dict:
    # Later months slightly busier (growing business) — skew dates toward the end.
    offset = int(min(days - 1, rng.beta(1.3, 1.0) * days))
    d = start + timedelta(days=offset)

    category = _pick(rng, CATEGORY_WEIGHTS)
    if d.month == 9 and rng.random() < 0.35:
        category = "Electronics"  # festive-season spike
    region = _pick(rng, REGION_WEIGHTS)
    city, state = CITIES[region][rng.integers(len(CITIES[region]))]
    product, base_price = PRODUCTS[category][rng.integers(len(PRODUCTS[category]))]
    price = round(base_price * rng.uniform(0.9, 1.1), 2)
    qty = int(max(1, rng.poisson(3 if category == "Grocery" else 1.6)))
    status = _pick(rng, STATUS_WEIGHTS)

    delivery = ""
    if status in ("Delivered", "Returned"):
        base = {"North": 2.5, "South": 2.8, "West": 2.4, "Central": 3.4, "East": 3.8}[region]
        if region == "East" and d.month >= 8:
            base += 3.0  # planted operational problem
        delivery = str(int(max(1, round(rng.normal(base, 1.2)))))

    return {"order_id": f"ORD-{order_num:06d}", "order_date": d, "region": region,
            "state": state, "city": city, "category": category, "product": product,
            "quantity": str(qty), "unit_price": f"{price:.2f}", "status": status,
            "delivery_days": delivery}


def _messify(rng, row: dict) -> dict:
    row = dict(row)
    row["order_date"] = _fmt_date(rng, row["order_date"])
    for col, mapping in SYNONYMS.items():
        if row[col] in mapping and rng.random() < 0.08:
            row[col] = mapping[row[col]]
    for col in ("region", "city", "category", "product", "status", "state"):
        row[col] = _messy_text(rng, row[col])
    if rng.random() < 0.05:
        row["state"] = ""
    if row["delivery_days"] and rng.random() < 0.03:
        row["delivery_days"] = ""
    return row


BREAKERS = [
    ("missing city", lambda r: r.update(city="")),
    ("missing category", lambda r: r.update(category="")),
    ("missing order id", lambda r: r.update(order_id="")),
    ("quantity not a number", lambda r: r.update(quantity="abc")),
    ("negative quantity", lambda r: r.update(quantity="-3")),
    ("price not a number", lambda r: r.update(unit_price="N/A")),
    ("impossible date", lambda r: r.update(order_date="31/02/2026")),
    ("unparseable date", lambda r: r.update(order_date="yesterday")),
    ("future date", lambda r: r.update(order_date="2027-03-15")),
    ("unknown status", lambda r: r.update(status="Lost")),
    ("bad order id", lambda r: r.update(order_id="12345")),
    ("unknown category", lambda r: r.update(category="Toys")),
]


def generate(rows: int, start_id: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    start, end = date(2026, 1, 1), date(2026, 9, 30)
    days = (end - start).days + 1

    out = [_messify(rng, _clean_row(rng, start_id + i, start, days)) for i in range(rows)]

    # ~4% invalid rows
    for idx in rng.choice(len(out), size=int(rows * 0.04), replace=False):
        BREAKERS[rng.integers(len(BREAKERS))][1](out[idx])

    # ~2% exact duplicates, ~1% conflicting duplicates (same order id, different data)
    for idx in rng.choice(len(out), size=int(rows * 0.02), replace=False):
        out.append(dict(out[idx]))
    for idx in rng.choice(len(out), size=int(rows * 0.01), replace=False):
        dup = dict(out[idx])
        dup["quantity"] = str(int(rng.integers(1, 9)))
        out.append(dup)

    rng.shuffle(out)
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def write_edge_cases(folder: Path) -> None:
    """Files the judges might try, to prove graceful failure."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "empty.csv").write_bytes(b"")
    (folder / "header_only.csv").write_text(",".join(COLUMNS) + "\n", encoding="utf-8")
    (folder / "missing_columns.csv").write_text(
        "order_id,order_date,region\nORD-900001,2026-05-01,North\n", encoding="utf-8")
    (folder / "not_really_csv.csv").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(64))
    (folder / "notes.txt").write_text("This is a plain text file, not a dataset.\n",
                                      encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=5000)
    ap.add_argument("--start-id", type=int, default=100000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=Path("sample_data/operations_orders.csv"))
    ap.add_argument("--no-edge-cases", action="store_true")
    args = ap.parse_args()

    rows = generate(args.rows, args.start_id, args.seed)
    write_csv(args.out, rows)
    print(f"Wrote {len(rows)} rows to {args.out}")
    if not args.no_edge_cases:
        write_edge_cases(args.out.parent / "edge_cases")
        print(f"Wrote edge-case files to {args.out.parent / 'edge_cases'}")


if __name__ == "__main__":
    main()
