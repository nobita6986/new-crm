#!/usr/bin/env bash
# deploy/dev/bootstrap.sh
#
# Bootstrap DEV cục bộ cho CRM-CSKH — LẶP LẠI ĐƯỢC và IDEMPOTENT.
# Chạy BÊN TRONG container `bench` (user `frappe`), thư mục làm việc /workspace.
#
#   docker compose exec bench bash /src/deploy/dev/bootstrap.sh
#
# Mọi revision được GHIM theo biến môi trường (xem deploy/dev/.env.example và
# docs/architecture/version-lock.md). Không dùng latest/main/develop.
#
# KHÔNG dùng script này cho staging/production. Chỉ DEV + dữ liệu giả.

set -euo pipefail

log() { printf '\n\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[bootstrap][WARN]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[bootstrap][ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. Kiểm tra đầu vào
# ---------------------------------------------------------------------------
for v in DB_ROOT_PASSWORD ADMIN_PASSWORD SITE_NAME FRAPPE_TAG FRAPPE_SHA CRM_TAG CRM_SHA; do
  [ -n "${!v:-}" ] || die "Thiếu biến môi trường: $v (kiểm tra deploy/dev/.env)"
done

BENCH_DIR="${BENCH_DIR:-/workspace/frappe-bench}"
SRC_DIR="${SRC_DIR:-/src}"
SITE="${SITE_NAME}"
CRM_REPO="${CRM_REPO:-https://github.com/frappe/crm.git}"

log "Site=${SITE}  bench=${BENCH_DIR}"
log "Frappe ${FRAPPE_TAG} (${FRAPPE_SHA})  CRM ${CRM_TAG} (${CRM_SHA})"

# Cần quyền ghi vào /workspace (named volume). Nếu chưa, thoát với hướng dẫn rõ ràng
# thay vì lỗi khó hiểu ở giữa chừng.
if [ ! -w /workspace ]; then
  die "/workspace không ghi được (thường do volume do root tạo). Chạy 1 lần:
    docker compose exec -u root bench bash -lc 'mkdir -p /workspace && chown -R frappe:frappe /workspace'"
fi

# ---------------------------------------------------------------------------
# 1. Khởi tạo Frappe bench (nếu chưa có) — ghim framework theo tag
# ---------------------------------------------------------------------------
if [ ! -d "$BENCH_DIR/apps/frappe/.git" ]; then
  log "Tạo bench mới với Frappe $FRAPPE_TAG ..."
  bench init --skip-redis-config-generation --frappe-branch "$FRAPPE_TAG" "$BENCH_DIR"
else
  log "Đã có bench tại $BENCH_DIR — bỏ qua bench init."
fi

cd "$BENCH_DIR"

# Ghim framework đúng full SHA (tag có thể trùng branch tip, nhưng SHA mới là khóa).
log "Ghim Frappe framework về $FRAPPE_SHA ..."
git -C apps/frappe fetch --quiet upstream "refs/tags/$FRAPPE_TAG:refs/tags/$FRAPPE_TAG" || warn "fetch frappe thất bại (có thể do mạng) — dùng commit sẵn có"
git -C apps/frappe checkout --quiet "$FRAPPE_SHA" \
  || die "Không checkout được Frappe SHA $FRAPPE_SHA"
ACTUAL_FRAPPE_SHA="$(git -C apps/frappe rev-parse HEAD)"
[ "$ACTUAL_FRAPPE_SHA" = "$FRAPPE_SHA" ] || die "Frappe SHA lệch: $ACTUAL_FRAPPE_SHA != $FRAPPE_SHA"

# ---------------------------------------------------------------------------
# 2. Cấu hình dịch vụ phụ trợ (MariaDB/Redis là service của compose, không phải localhost)
# ---------------------------------------------------------------------------
log "Cấu hình common_site_config trỏ tới service của compose ..."
bench set-config -g db_host db
bench set-config -gp db_port 3306
bench set-config -g redis_cache  redis://redis-cache:6379
bench set-config -g redis_queue  redis://redis-queue:6379
bench set-config -g redis_socketio redis://redis-queue:6379

# ---------------------------------------------------------------------------
# 3. Lấy app CRM theo tag đã ghim
# ---------------------------------------------------------------------------
if [ ! -d "apps/crm/.git" ]; then
  log "Lấy Frappe CRM $CRM_TAG ..."
  bench get-app --branch "$CRM_TAG" "$CRM_REPO"
else
  log "Đã có app crm — bỏ qua get-app."
fi

log "Ghim CRM về $CRM_SHA ..."
git -C apps/crm fetch --quiet upstream "refs/tags/$CRM_TAG:refs/tags/$CRM_TAG" || warn "fetch crm thất bại — dùng commit sẵn có"
git -C apps/crm checkout --quiet "$CRM_SHA" || die "Không checkout được CRM SHA $CRM_SHA"
ACTUAL_CRM_SHA="$(git -C apps/crm rev-parse HEAD)"
[ "$ACTUAL_CRM_SHA" = "$CRM_SHA" ] || die "CRM SHA lệch: $ACTUAL_CRM_SHA != $CRM_SHA"

# ---------------------------------------------------------------------------
# 4. Tạo site DEV (nếu chưa có)
# ---------------------------------------------------------------------------
if [ ! -d "sites/$SITE" ]; then
  log "Tạo site $SITE ..."
  bench new-site \
    --db-root-password "$DB_ROOT_PASSWORD" \
    --admin-password "$ADMIN_PASSWORD" \
    --no-mariadb-socket \
    --mariadb-user-host-login-scope=% \
    "$SITE"
else
  log "Đã có site $SITE — bỏ qua new-site."
fi

# Site DEV cần developer_mode để tạo DocType/migrate ở WP-02.
bench --site "$SITE" set-config developer_mode 1
bench --site "$SITE" set-config time_zone "Asia/Ho_Chi_Minh"

# ---------------------------------------------------------------------------
# 5. Cài app (idempotent theo list-apps)
# ---------------------------------------------------------------------------
installed() { bench --site "$SITE" list-apps 2>/dev/null | grep -qx "$1"; }

if installed crm; then
  log "App 'crm' đã cài — bỏ qua."
else
  log "Cài app 'crm' vào site $SITE ..."
  bench --site "$SITE" install-app crm
fi

# crm_cskh: repo là nguồn duy nhất. /src được mount READ-ONLY và apps/crm_cskh
# không phải git repo riêng, nên `bench get-app` không dùng được ở đây — dùng symlink
# (đã kiểm chứng ở WP-02: install-app + migrate + run-tests đều chạy).
if [ "${INSTALL_CSKH:-0}" = "1" ]; then
  APP_SRC="$SRC_DIR/apps/crm_cskh"
  if [ -d "$APP_SRC/crm_cskh" ]; then
    if [ -e "$BENCH_DIR/apps/crm_cskh" ]; then
      log "Đã có $BENCH_DIR/apps/crm_cskh — bỏ qua symlink."
    else
      log "Symlink $APP_SRC -> $BENCH_DIR/apps/crm_cskh"
      ln -s "$APP_SRC" "$BENCH_DIR/apps/crm_cskh"
    fi
    # bench cần biết app tồn tại trong bench trước khi install-app.
    if ! grep -qx "crm_cskh" "$BENCH_DIR/sites/apps.txt" 2>/dev/null; then
      python -c 'from pathlib import Path; p=Path("sites/apps.txt"); s=p.read_text(); p.write_text(s + ("" if s.endswith("\n") else "\n") + "crm_cskh\n")'
    fi
    uv pip install --quiet -e "$BENCH_DIR/apps/crm_cskh" --python "$BENCH_DIR/env/bin/python"

    if installed crm_cskh; then
      log "App 'crm_cskh' đã cài trên site $SITE — bỏ qua."
    else
      log "Cài app 'crm_cskh' vào site $SITE ..."
      bench --site "$SITE" install-app crm_cskh
    fi
  else
    warn "INSTALL_CSKH=1 nhưng không thấy $APP_SRC/crm_cskh — bỏ qua."
  fi
else
  log "INSTALL_CSKH != 1 — chưa cài crm_cskh (bật =1 để cài app tùy chỉnh)."
fi

# ---------------------------------------------------------------------------
# 6. Migrate + xác minh
# ---------------------------------------------------------------------------
log "Chạy migrate ..."
bench --site "$SITE" migrate

log "===== XÁC MINH ====="
printf 'frappe  SHA: %s\n' "$(git -C apps/frappe rev-parse HEAD)"
printf 'crm     SHA: %s\n' "$(git -C apps/crm rev-parse HEAD)"
printf 'apps       :\n'
bench --site "$SITE" list-apps | sed 's/^/  - /'

log "HOÀN TẤT. Bước tiếp theo:"
cat <<EOF
  # (tuỳ chọn) nạp dữ liệu giả
  docker compose exec bench bash /src/deploy/dev/seed/run-seed.sh

  # chạy server DEV
  docker compose exec bench bash -lc 'cd $BENCH_DIR && bench start'

  # mở UI:  http://$SITE:8000/crm
EOF
