#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/klimovsk-open-data}"
BRANCH="${EIS_BRANCH:-main}"

cd "$PROJECT_DIR"
/usr/bin/flock -n /var/lock/klimovsk-eis-git.lock \
  /usr/bin/git pull --ff-only origin "$BRANCH"

exec /usr/bin/python3 "$PROJECT_DIR/watch_eis_exports.py" \
  --input-dir "$PROJECT_DIR/data/raw/eis" \
  --manifest "$PROJECT_DIR/data/state/eis_manifest.json" \
  --sql "$PROJECT_DIR/eis_etl_postgres.sql" \
  --database-url "${DATABASE_URL:?DATABASE_URL is required}"
