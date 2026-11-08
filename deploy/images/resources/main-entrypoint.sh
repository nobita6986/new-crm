#!/bin/bash
set -e

umask 0002

SITES_DIR="/home/frappe/frappe-bench/sites"
ASSETS_PATH="${SITES_DIR}/assets"
BAKED_PATH="/home/frappe/frappe-bench/assets"
APPS_ORDER_PATH="/home/frappe/frappe-bench/apps-order.txt"
APPS_TXT_PATH="${SITES_DIR}/apps.txt"

mkdir -p "$SITES_DIR"

# Avoid concurrent rm -rf races across backend/frontend/websocket/workers sharing sites volume.
if [ -d "$ASSETS_PATH" ] && [ ! -L "$ASSETS_PATH" ]; then
  rm -rf "$ASSETS_PATH"
fi
if [ ! -L "$ASSETS_PATH" ] || [ "$(readlink "$ASSETS_PATH")" != "$BAKED_PATH" ]; then
  echo "Linking image-baked assets ($BAKED_PATH -> $ASSETS_PATH)..."
  ln -sfn "$BAKED_PATH" "$ASSETS_PATH"
fi

# Preserve canonical app load order (frappe -> crm -> crm_cskh) so crm_cskh hooks
# deterministically override upstream Frappe/CRM whitelisted methods.
if [ -f "$APPS_ORDER_PATH" ] && { [ ! -f "$APPS_TXT_PATH" ] || ! cmp -s "$APPS_ORDER_PATH" "$APPS_TXT_PATH"; }; then
  cp "$APPS_ORDER_PATH" "$APPS_TXT_PATH"
fi

if [ ! -f "${SITES_DIR}/common_site_config.json" ]; then
  echo "{}" > "${SITES_DIR}/common_site_config.json"
fi

exec "$@"
