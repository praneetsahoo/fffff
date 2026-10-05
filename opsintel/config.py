"""Settings and logging.

All settings come from environment variables, so the same code runs on a laptop and on EC2.
No password is written in code: on AWS the database password is read from SSM Parameter Store
using the EC2 instance's IAM role.
"""
from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache

from sqlalchemy.engine import URL

REGION = os.getenv("AWS_REGION", "ap-southeast-2")
S3_BUCKET = os.getenv("S3_BUCKET", "")              # empty = use a local folder (development)
LOCAL_STORAGE_DIR = os.getenv("LOCAL_STORAGE_DIR", "./data/storage")
MAX_UPLOAD_MB = 10
SLA_DAYS = 5                                        # a delivery taking longer than this is "late"


@lru_cache
def database_url() -> URL | str:
    """Local development: DB_URL. On AWS: build the RDS URL, password fetched from SSM."""
    if os.getenv("DB_URL"):
        return os.environ["DB_URL"]
    import boto3

    password = boto3.client("ssm", region_name=REGION).get_parameter(
        Name=os.environ["DB_PASSWORD_PARAM"], WithDecryption=True)["Parameter"]["Value"]
    return URL.create(
        "mysql+pymysql",
        username=os.getenv("DB_USER", "opsintel_app"),
        password=password,                      # URL.create escapes special characters safely
        host=os.environ["DB_HOST"],
        port=3306,
        database=os.getenv("DB_NAME", "opsintel"),
        query={"ssl_ca": os.getenv("DB_SSL_CA", "/opt/opsintel/rds-ca.pem"), "charset": "utf8mb4"},
    )


def setup_logging() -> None:
    """Log to the console, and to LOG_FILE on EC2 (the CloudWatch agent ships that file)."""
    root = logging.getLogger()
    if getattr(root, "_opsintel", False):        # Streamlit reruns scripts; configure once
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if os.getenv("LOG_FILE"):
        handlers.append(logging.FileHandler(os.environ["LOG_FILE"]))
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    root.setLevel(logging.INFO)
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._opsintel = True
