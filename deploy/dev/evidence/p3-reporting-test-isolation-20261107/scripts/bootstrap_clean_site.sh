#!/usr/bin/env bash
set -euo pipefail

SITE="${SITE_NAME:-p3-reptest-clean.localhost}"
cd /home/frappe/frappe-bench
mkdir -p sites/assets
if [ ! -f sites/apps.txt ]; then
  printf "frappe\ncrm\ncrm_cskh\n" > sites/apps.txt
fi

python3 - <<PY
import json
cfg = {
    "db_host": "db",
    "db_port": 3306,
    "redis_cache": "redis://redis-cache:6379",
    "redis_queue": "redis://redis-queue:6379",
    "redis_socketio": "redis://redis-queue:6379",
    "webserver_port": 8000,
    "serve_default_site": True,
    "default_site": "${SITE}",
}
with open("sites/common_site_config.json", "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
PY

if [ -d /home/frappe/frappe-bench/sites_baked/assets ]; then
  cp -rn /home/frappe/frappe-bench/sites_baked/assets/* sites/assets/ || true
fi
if [ -d /home/frappe/frappe-bench/apps/crm_cskh/crm_cskh/public ]; then
  ln -sfn /home/frappe/frappe-bench/apps/crm_cskh/crm_cskh/public sites/assets/crm_cskh
fi
if [ -d /home/frappe/frappe-bench/apps/crm/crm/public ] && [ ! -e sites/assets/crm ]; then
  ln -sfn /home/frappe/frappe-bench/apps/crm/crm/public sites/assets/crm
fi
if [ -d /home/frappe/frappe-bench/apps/frappe/frappe/public ] && [ ! -e sites/assets/frappe ]; then
  ln -sfn /home/frappe/frappe-bench/apps/frappe/frappe/public sites/assets/frappe
fi

bench new-site "$SITE" \
  --mariadb-root-password "$DB_ROOT_PASSWORD" \
  --admin-password "$ADMIN_PASSWORD" \
  --no-mariadb-socket
bench --site "$SITE" install-app crm
bench --site "$SITE" install-app crm_cskh

# Keep P3 flags governed strictly by CSKH Policy (no p3_*_enabled keys in site_config.json)
bench --site "$SITE" set-config allow_tests true
bench --site "$SITE" migrate
bench --site "$SITE" clear-cache
echo "BOOTSTRAP_CLEAN_SITE_OK site=$SITE"
