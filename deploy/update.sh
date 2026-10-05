#!/bin/bash
# Redeploy the latest code from GitHub on the running server (run as root, e.g. via SSM Run Command).
# Re-uses the existing non-secret config; data in S3 and RDS is untouched.
set -euo pipefail
export HOME=/root
set -a; . /etc/opsintel/opsintel.env; set +a
bash /opt/opsintel/app/deploy/install.sh
