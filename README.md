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
| `app/routers/` | API endpoints |
| `scripts/generate_data.py` | Messy demo data + edge-case files |
| `sample_data/` | Generated demo CSVs and edge cases |
| `tests/` | pytest suite (S3 mocked with moto) |

## Run locally

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # SQLite + local storage for development
uvicorn app.main:app --reload   # http://localhost:8000/api/health , /docs
pytest -q
```

## Security notes

* No credentials in code or git. On AWS the app uses the EC2 instance role for S3 and reads the
  DB password from SSM Parameter Store (SecureString) at startup.
* `.env` is git-ignored; only `.env.example` (no secrets) is committed.
