# OpsIntel — Smart Operations Data Intelligence Platform

Upload an operational CSV → it is stored in S3 → validated and cleaned by a backend pipeline →
loaded into RDS MySQL → explored through a dashboard, searchable records and a data-quality report.

```text
Browser ──upload──► FastAPI (EC2) ──raw file──► S3  raw/
                       │ background pipeline: validate → clean → transform → derive
                       ├──clean rows / rejects / quality stats──► RDS MySQL (private)
                       └──clean + rejected CSVs──► S3  processed/  rejected/
Dashboard · Records · Quality report  ◄── REST API (SQL aggregations)
```

## Project layout

| Path | Purpose |
|---|---|
| `app/main.py` | App factory, request-id logging middleware |
| `app/config.py` | Env-based settings; DB password from SSM on AWS |
| `app/db.py`, `app/models.py` | SQLAlchemy engine/session and schema |
| `app/profile.py`, `app/profiles/*.json` | Dataset contract driving validation & dashboard |
| `app/storage.py` | S3 / local storage with raw–processed–rejected layout |
| `app/errors.py` | One JSON error format for every failure |
| `app/pipeline/` | Parse → validate → clean → de-duplicate → derive → score |
| `app/repositories/` | All SQL for uploads/results |
| `app/services/` | Upload workflow + background job queue |
| `app/routers/` | API endpoints (+ web app and sample files) |
| `app/static/` | Web app: HTML/CSS + ES modules, hand-built SVG charts, no build step |
| `scripts/generate_data.py` | Messy demo data + edge-case files |
| `sample_data/` | Generated demo CSVs and edge cases |
| `tests/` | pytest suite (S3 mocked with moto) |

## Run locally

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # SQLite + local storage for development
uvicorn app.main:app --reload   # http://localhost:8000/api/health , /docs
pytest -q
# also run the DB tests against real MySQL/MariaDB (same engine family as RDS):
TEST_MYSQL_URL='mysql+pymysql://user:password@127.0.0.1/opsintel_test' pytest -q
```

## Web app

Open `http://localhost:8000/`. Four views: **Upload** (drag & drop, live pipeline progress,
row accounting), **Dashboard** (KPIs, automatic insights, charts — filterable),
**Records** (search, filter, sort, paginate, CSV export) and **Data quality** (issues per column,
every refused row with reasons, raw/cleaned/rejected downloads).
`python scripts/browser_check.py http://localhost:8000` drives the UI in headless Chromium and
saves screenshots.

## How an upload is processed

1. `accept_upload` — type/size/empty checks, SHA-256 duplicate-file check, raw file → `raw/` in S3,
   upload row created as `QUEUED`. The request returns immediately.
2. Background worker (single thread, FIFO) — reads raw file, runs the pipeline, writes
   `processed/` and `rejected/` CSVs, then saves orders + rejected rows + quality issues +
   final counts in **one database transaction**.
3. Any failure marks the upload `FAILED` with a readable message; nothing is half-written and the
   raw file is kept, so the upload can be retried. Interrupted jobs resume on restart.

## Deployment (AWS, ap-southeast-2)

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and the reason for each
service, and [`docs/DEMO.md`](docs/DEMO.md) for the judging walkthrough.

| Resource | Name |
|---|---|
| EC2 (Amazon Linux 2023, nginx + FastAPI via systemd) | `opsintel-app` |
| RDS MySQL 8.4 (private, encrypted) | `opsintel-db` |
| S3 (private, encrypted, versioned, TLS-only) | `opsintel-data-748348797173` |
| IAM role (one bucket, `/opsintel/db/*` SSM params) | `opsintel-ec2-role` |
| Security groups | `opsintel-app-sg` (80 only), `opsintel-db-sg` (3306 from app SG) |
| SSM SecureString | `/opsintel/db/master_password`, `/opsintel/db/app_password` |

* **First boot:** `deploy/user_data.sh` clones this repo and runs `deploy/install.sh`.
* **Redeploy after a push:** run `bash /opt/opsintel/app/deploy/update.sh` on the server via
  SSM Run Command (no SSH port is open).
* **Verify a deployment:** `python scripts/smoke_test.py http://<server>` (34 live checks).
* **Teardown order:** EC2 → RDS → SSM parameters → empty and delete the versioned bucket →
  IAM role/instance profile → DB subnet group → security groups (after RDS is gone).

## Security notes

* No credentials in code or git. On AWS the app uses the EC2 instance role for S3 and reads the
  DB password from SSM Parameter Store (SecureString) at startup.
* `.env` is git-ignored; only `.env.example` (no secrets) is committed.
