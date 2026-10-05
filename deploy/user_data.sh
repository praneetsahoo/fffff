#!/bin/bash
# EC2 user data (first boot). __DB_HOST__ and __S3_BUCKET__ are filled in at launch.
# Contains no secrets: the app reads its DB password from SSM using the instance role.
set -euo pipefail
exec > >(tee -a /var/log/opsintel-install.log) 2>&1
echo "[opsintel] first boot $(date -u +%FT%TZ)"
dnf install -y -q git >/dev/null
export HOME=/root
rm -rf /tmp/opsintel-src
git clone --depth 1 https://github.com/praneetsahoo/fffff.git /tmp/opsintel-src
DB_HOST="__DB_HOST__" S3_BUCKET="__S3_BUCKET__" AWS_REGION="ap-southeast-2" \
  bash /tmp/opsintel-src/deploy/install.sh
