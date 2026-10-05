#!/bin/bash
# OpsIntel installer for Amazon Linux 2023 (run as root; idempotent — safe to re-run).
# Called by EC2 user data on first boot, and by deploy/update.sh for redeploys.
#
# Required environment (non-secret; passwords are read from SSM at runtime):
#   DB_HOST, S3_BUCKET, AWS_REGION
set -euo pipefail
export HOME="${HOME:-/root}"   # cloud-init runs without HOME; git needs it

: "${DB_HOST:?DB_HOST is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"
: "${AWS_REGION:=ap-southeast-2}"
REPO_URL="${REPO_URL:-https://github.com/praneetsahoo/fffff.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/opsintel/app
VENV=/opt/opsintel/venv

echo "[opsintel] installing packages"
dnf install -y -q python3.11 python3.11-pip git nginx >/dev/null

id opsintel >/dev/null 2>&1 || useradd --system --home-dir /opt/opsintel --shell /sbin/nologin opsintel
mkdir -p /opt/opsintel /etc/opsintel

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

echo "[opsintel] fetching code ($BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" checkout --force --detach FETCH_HEAD   # read-only server copy; never pushes
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "[opsintel] python environment"
[ -d "$VENV" ] || python3.11 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "[opsintel] RDS TLS certificate bundle"
curl -fsSL -o /opt/opsintel/rds-ca.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem

# Non-secret configuration only. The DB password is fetched from SSM by the app at startup.
cat > /etc/opsintel/opsintel.env <<EOF
DB_HOST=${DB_HOST}
DB_PORT=3306
DB_NAME=opsintel
DB_USER=opsintel_app
DB_PASSWORD_SSM_PARAM=/opsintel/db/app_password
DB_SSL_CA=/opt/opsintel/rds-ca.pem
STORAGE_BACKEND=s3
S3_BUCKET=${S3_BUCKET}
AWS_REGION=${AWS_REGION}
MAX_UPLOAD_MB=10
JOB_MODE=thread
LOG_LEVEL=INFO
EOF
chmod 640 /etc/opsintel/opsintel.env
chown -R opsintel:opsintel /opt/opsintel
chgrp opsintel /etc/opsintel/opsintel.env

echo "[opsintel] database user (least privilege)"
DB_HOST="$DB_HOST" AWS_REGION="$AWS_REGION" "$VENV/bin/python" "$APP_DIR/deploy/bootstrap_db.py"

echo "[opsintel] services"
install -m 644 "$APP_DIR/deploy/opsintel.service" /etc/systemd/system/opsintel.service
install -m 644 "$APP_DIR/deploy/nginx.conf" /etc/nginx/nginx.conf
install -m 644 "$APP_DIR/deploy/nginx-proxy.conf" /etc/nginx/opsintel_proxy.conf
nginx -t
systemctl daemon-reload
systemctl enable --now nginx
systemctl enable opsintel
systemctl restart opsintel
systemctl reload nginx

echo "[opsintel] waiting for the app to report healthy"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1/api/health >/dev/null; then
    echo "[opsintel] healthy"; curl -sS http://127.0.0.1/api/health; echo; exit 0
  fi
  sleep 2
done
echo "[opsintel] app did not become healthy"; journalctl -u opsintel --no-pager -n 50; exit 1
