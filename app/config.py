"""Central configuration.

All settings come from environment variables (or a local .env file in development).
No secret is ever hard-coded: on AWS the database password is fetched at startup
from SSM Parameter Store using the EC2 instance's IAM role.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    db_url: str | None = Field(default=None, description="Full SQLAlchemy URL (dev/tests).")
    db_host: str | None = None
    db_port: int = 3306
    db_name: str = "opsintel"
    db_user: str = "opsintel_app"
    db_password_ssm_param: str | None = None
    db_ssl_ca: str | None = None

    # --- Storage ---
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_dir: str = "./data/storage"
    s3_bucket: str | None = None
    aws_region: str = "ap-southeast-2"

    # --- App ---
    max_upload_mb: int = 10
    log_level: str = "INFO"
    dataset_profile: str = "operations_orders"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def database_url(self) -> URL | str:
        """Return the DB URL. Uses DB_URL if given, else builds a MySQL URL for RDS."""
        if self.db_url:
            return self.db_url
        if not self.db_host:
            raise RuntimeError("Database not configured: set DB_URL or DB_HOST.")
        password = _fetch_ssm_secret(self.db_password_ssm_param, self.aws_region)
        query = {"charset": "utf8mb4"}
        if self.db_ssl_ca:
            query["ssl_ca"] = self.db_ssl_ca  # RDS MySQL 8.4 requires TLS
        # URL.create escapes special characters in the password safely.
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query=query,
        )


def _fetch_ssm_secret(param_name: str | None, region: str) -> str:
    if not param_name:
        raise RuntimeError("DB_PASSWORD_SSM_PARAM must be set when DB_HOST is used.")
    import boto3  # imported lazily so local dev does not need AWS

    ssm = boto3.client("ssm", region_name=region)
    resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
    return resp["Parameter"]["Value"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
