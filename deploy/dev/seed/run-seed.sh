#!/usr/bin/env bash
# deploy/dev/seed/run-seed.sh
#
# Wrapper chạy seed giả lập bên trong container `bench`.
#   docker compose exec bench bash /src/deploy/dev/seed/run-seed.sh
#
# Có thể chạy lại nhiều lần: seed_demo.py là idempotent.

set -euo pipefail

SITE="${SITE_NAME:-dev.localhost}"
BENCH_DIR="${BENCH_DIR:-/workspace/frappe-bench}"

[ -d "$BENCH_DIR" ] || { echo "Không thấy bench tại $BENCH_DIR. Chạy bootstrap.sh trước." >&2; exit 1; }
[ -x "$BENCH_DIR/env/bin/python" ] || { echo "Không thấy $BENCH_DIR/env/bin/python. Chạy bootstrap.sh trước." >&2; exit 1; }

cd "$BENCH_DIR"
FRAPPE_STREAM_LOGGING=1 BENCH_DIR="$BENCH_DIR" SITE_NAME="$SITE" exec ./env/bin/python /src/deploy/dev/seed/seed_demo.py "$SITE"
