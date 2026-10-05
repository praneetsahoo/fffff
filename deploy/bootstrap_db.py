"""Create the OpsIntel database and a least-privilege application user on RDS (idempotent).

Runs on the EC2 instance during install. Both passwords come from SSM Parameter Store
(SecureString) via the instance role; they are never printed, logged or written to disk.

The app connects as `opsintel_app`, which can only use the `opsintel` schema — it is not
the RDS master user.
"""
from __future__ import annotations

import os
import sys
import time

import boto3
import pymysql

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
HOST = os.environ["DB_HOST"]
CA = os.environ.get("DB_SSL_CA", "/opt/opsintel/rds-ca.pem")
APP_USER = "opsintel_app"
PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES"


def secret(name: str) -> str:
    ssm = boto3.client("ssm", region_name=REGION)
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def connect(password: str):
    last = None
    for attempt in range(90):  # up to ~15 min: the server may boot before RDS is available
        try:
            return pymysql.connect(host=HOST, user="opsintel_admin", password=password,
                                   ssl={"ca": CA}, connect_timeout=10, autocommit=True)
        except pymysql.err.OperationalError as exc:
            last = exc
            print(f"waiting for database (attempt {attempt + 1}): {exc.args[0]}", flush=True)
            time.sleep(10)
    raise SystemExit(f"could not connect to the database: {last}")


def main() -> int:
    master = secret("/opsintel/db/master_password")
    app_pw = secret("/opsintel/db/app_password")
    conn = connect(master)
    with conn.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS opsintel CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        cur.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s REQUIRE SSL", (APP_USER, app_pw))
        cur.execute("ALTER USER %s@'%%' IDENTIFIED BY %s REQUIRE SSL", (APP_USER, app_pw))  # keep in sync
        cur.execute(f"GRANT {PRIVILEGES} ON opsintel.* TO %s@'%%'", (APP_USER,))
        cur.execute("SHOW GRANTS FOR %s@'%%'", (APP_USER,))
        grants = [row[0] for row in cur.fetchall()]
    conn.close()
    print("database ready; app user grants:", *grants, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
