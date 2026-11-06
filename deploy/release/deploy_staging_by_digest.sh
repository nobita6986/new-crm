#!/usr/bin/env bash
# deploy/release/deploy_staging_by_digest.sh
#
# Fail-closed Staging/Production deployment script enforcing `Build-Once, Promote-By-Digest`
# (P4-02 / G05 / F-P4-REL-02 / F-P4-REL-04).
# Never runs `docker build`, `git pull` on runtime code, or `bench update`.
#
# Required inputs:
#   --manifest <path/to/release-manifest.json>
#   --sig <path/to/release-manifest.json.sig>
#   --public-key-file <path> (or RELEASE_SIGNING_PUBLIC_KEY env var)
#   --env-file <path/to/staging.env>
#   --build-inspect <path/to/image-inspect-summary.json>
#   --clean-site-report <path/to/clean-site-verify.json>
#   --out-dir <path/to/staging-evidence>
#
# Optional pull-only private registry authentication (logged out immediately after pull):
#   --registry-pull-user <username>          (or REGISTRY_PULL_USERNAME env var)
#   --registry-pull-token-file <token_file>  (or REGISTRY_PULL_TOKEN_FILE / REGISTRY_PULL_TOKEN env)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MANIFEST=""
SIG=""
PUBLIC_KEY_FILE=""
ENV_FILE=""
BUILD_INSPECT=""
CLEAN_SITE_REPORT=""
OUT_DIR=""
PULL_USER="${REGISTRY_PULL_USERNAME:-}"
PULL_TOKEN_FILE="${REGISTRY_PULL_TOKEN_FILE:-}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --manifest) MANIFEST="$2"; shift 2 ;;
    --sig) SIG="$2"; shift 2 ;;
    --public-key-file) PUBLIC_KEY_FILE="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --build-inspect) BUILD_INSPECT="$2"; shift 2 ;;
    --clean-site-report) CLEAN_SITE_REPORT="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --registry-pull-user) PULL_USER="$2"; shift 2 ;;
    --registry-pull-token-file) PULL_TOKEN_FILE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

for req in "${MANIFEST}" "${SIG}" "${ENV_FILE}" "${BUILD_INSPECT}" "${CLEAN_SITE_REPORT}" "${OUT_DIR}"; do
  if [ -z "${req}" ]; then
    echo "ERROR: Missing required argument to deploy_staging_by_digest.sh" >&2
    exit 2
  fi
done

mkdir -p "${OUT_DIR}"

# 1. Verify Ed25519 signature and production_candidate scope BEFORE touching Docker
KEY_ARGS=()
if [ -n "${PUBLIC_KEY_FILE}" ]; then
  KEY_ARGS=(--public-key-file "${PUBLIC_KEY_FILE}")
fi

python3 "${SCRIPT_DIR}/release_manifest.py" verify \
  --manifest "${MANIFEST}" \
  --sig "${SIG}" \
  --expected-scope production_candidate \
  "${KEY_ARGS[@]}" > "${OUT_DIR}/manifest-verify.json"

TARGET_IMAGE="$(jq -r '.image' "${MANIFEST}")"
sh "${SCRIPT_DIR}/validate_digest_uri.sh" "${TARGET_IMAGE}"

# 2. Enforce exact APP_IMAGE=@sha256 digest and PULL_POLICY=always in target env
ENV_APP_IMAGE="$(grep -E '^APP_IMAGE=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
ENV_PULL_POLICY="$(grep -E '^PULL_POLICY=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
ENV_MIGRATE_SITES="$(grep -E '^MIGRATE_SITES=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
COMPOSE_PROJECT="$(grep -E '^COMPOSE_PROJECT_NAME=' "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"

sh "${SCRIPT_DIR}/validate_digest_uri.sh" "${ENV_APP_IMAGE}"

if [ "${ENV_APP_IMAGE}" != "${TARGET_IMAGE}" ]; then
  echo "FATAL: Target env APP_IMAGE (${ENV_APP_IMAGE}) != signed manifest image (${TARGET_IMAGE})" >&2
  exit 1
fi
if [ "${ENV_PULL_POLICY}" != "always" ]; then
  echo "FATAL: Target env PULL_POLICY must be 'always', got '${ENV_PULL_POLICY}'" >&2
  exit 1
fi
if [ "${ENV_MIGRATE_SITES}" != "false" ]; then
  echo "FATAL: Steady-state target env MIGRATE_SITES must be 'false', got '${ENV_MIGRATE_SITES}'" >&2
  exit 1
fi

COMPOSE_ARGS=(
  --project-name "${COMPOSE_PROJECT}"
  --env-file "${ENV_FILE}"
  -f "${REPO_ROOT}/deploy/compose/upstream/frappe_docker/compose.yaml"
  -f "${REPO_ROOT}/deploy/compose/upstream/frappe_docker/overrides/compose.mariadb.yaml"
  -f "${REPO_ROOT}/deploy/compose/upstream/frappe_docker/overrides/compose.mariadb-secrets.yaml"
  -f "${REPO_ROOT}/deploy/compose/upstream/frappe_docker/overrides/compose.redis.yaml"
  -f "${REPO_ROOT}/deploy/compose/upstream/frappe_docker/overrides/compose.https.yaml"
  -f "${REPO_ROOT}/deploy/compose/upstream/frappe_docker/overrides/compose.migrator.yaml"
  -f "${REPO_ROOT}/deploy/compose/overrides/compose.cskh-common.yaml"
  -f "${REPO_ROOT}/deploy/compose/overrides/compose.cskh-tls.yaml"
)

# 3. Optional pull-only private registry login -> pull exact immutable digest -> immediate logout
REGISTRY_HOST="${TARGET_IMAGE%%/*}"
LOGGED_IN_REGISTRY="false"
logout_pull_registry() {
  if [ "${LOGGED_IN_REGISTRY}" = "true" ]; then
    docker logout "${REGISTRY_HOST}" >/dev/null 2>&1 || true
  fi
}
trap logout_pull_registry EXIT

if [ -n "${PULL_USER}" ]; then
  if [ -n "${PULL_TOKEN_FILE}" ] && [ -s "${PULL_TOKEN_FILE}" ]; then
    tr -d '\r\n' < "${PULL_TOKEN_FILE}" | docker login "${REGISTRY_HOST}" \
      --username "${PULL_USER}" \
      --password-stdin
    LOGGED_IN_REGISTRY="true"
  elif [ -n "${REGISTRY_PULL_TOKEN:-}" ]; then
    printf '%s\n' "${REGISTRY_PULL_TOKEN}" | docker login "${REGISTRY_HOST}" \
      --username "${PULL_USER}" \
      --password-stdin
    LOGGED_IN_REGISTRY="true"
  else
    echo "FATAL: REGISTRY_PULL_USERNAME set without token file or REGISTRY_PULL_TOKEN" >&2
    exit 1
  fi
fi

docker pull "${TARGET_IMAGE}"
logout_pull_registry
LOGGED_IN_REGISTRY="false"
trap - EXIT

docker compose "${COMPOSE_ARGS[@]}" up -d db redis-cache redis-queue
docker compose "${COMPOSE_ARGS[@]}" up -d configurator
MIGRATE_SITES=true docker compose "${COMPOSE_ARGS[@]}" --profile migrate run --rm migrator

# 4. Roll application & proxy services on the identical digest (PULL_POLICY=never post-verified pull
#    if transient logout occurred, or PULL_POLICY=always when host daemon keeps read-only auth; pass
#    PULL_POLICY=never to `up -d` ONLY after step 3 verified `docker pull "${TARGET_IMAGE}"` so private
#    registries do not fail after pull-token logout!)
PULL_POLICY=never docker compose "${COMPOSE_ARGS[@]}" up -d backend frontend websocket queue-short queue-long scheduler proxy

# 5. Inspect all running Staging app containers & prove zero digest drift (G05)
mapfile -t APP_CIDS < <(
  docker compose "${COMPOSE_ARGS[@]}" ps -q backend frontend websocket queue-short queue-long scheduler
)
docker inspect "${APP_CIDS[@]}" > "${OUT_DIR}/staging-containers-inspect.json"

python3 "${SCRIPT_DIR}/verify_digest_promotion.py" \
  --manifest "${MANIFEST}" \
  --sig "${SIG}" \
  --expected-scope production_candidate \
  "${KEY_ARGS[@]}" \
  --build-inspect "${BUILD_INSPECT}" \
  --clean-site-report "${CLEAN_SITE_REPORT}" \
  --staging-containers-inspect "${OUT_DIR}/staging-containers-inspect.json" \
  --out "${OUT_DIR}/staging-digest-attestation.json"
