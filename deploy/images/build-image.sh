#!/usr/bin/env bash
# deploy/images/build-image.sh
#
# Deterministic build-once wrapper for the CRM-CSKH production image (P4-01 / P4-02).
# Reads pinned revisions/checksums from deploy/images/release-lock.json, verifies
# `apps/crm_cskh` tree integrity before invoking BuildKit, builds the immutable
# runtime image, and exports dependency locks + image provenance metadata.
#
# Usage (local / isolated CI):
#   bash deploy/images/build-image.sh [--out-dir deploy/images/evidence] [--tag crm-cskh:0.0.1-92e14af]
#
# Optional registry push (for P4-02 when an approved registry + credentials exist):
#   REGISTRY_PUSH=true CSKH_REGISTRY_IMAGE=ghcr.io/<org>/crm-cskh bash deploy/images/build-image.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  SCRIPT_DIR="$(cygpath -m "${SCRIPT_DIR}")"
  REPO_ROOT="$(cygpath -m "${REPO_ROOT}")"
fi
LOCK_FILE="${SCRIPT_DIR}/release-lock.json"
CONTAINERFILE="${SCRIPT_DIR}/Containerfile"

OUT_DIR="${OUT_DIR:-${SCRIPT_DIR}/evidence}"
LOCAL_IMAGE_REPO="${LOCAL_IMAGE_REPO:-crm-cskh}"
LOCAL_IMAGE_TAG="${LOCAL_IMAGE_TAG:-}"
REGISTRY_PUSH="${REGISTRY_PUSH:-false}"
CSKH_REGISTRY_IMAGE="${CSKH_REGISTRY_IMAGE:-}"
NO_CACHE="${NO_CACHE:-false}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --tag)
      if [[ "$2" == *:* ]]; then
        LOCAL_IMAGE_REPO="${2%%:*}"
        LOCAL_IMAGE_TAG="${2#*:}"
      else
        LOCAL_IMAGE_TAG="$2"
      fi
      shift 2
      ;;
    --no-cache)
      NO_CACHE="true"
      shift 1
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "${PYTHON_BIN}" ]; then
  for cand in python3 python; do
    if command -v "${cand}" >/dev/null 2>&1 && "${cand}" -c "import json, sys" >/dev/null 2>&1; then
      PYTHON_BIN="${cand}"
      break
    fi
  done
  if [ -z "${PYTHON_BIN}" ]; then
    echo "ERROR: functional python3/python interpreter is required to parse release-lock.json" >&2
    exit 2
  fi
fi

mkdir -p "${OUT_DIR}"

readarray -t LOCK_VARS < <("${PYTHON_BIN}" - "${LOCK_FILE}" "${REPO_ROOT}" <<'PY'
import hashlib, json, sys
from pathlib import Path

sys.stdout.reconfigure(newline="\n")
lock = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
repo_root = Path(sys.argv[2])

py_constraints = lock["python_constraints"]
for fkey, hkey in (
    ("system_constraints_file", "system_constraints_sha256"),
    ("bench_constraints_file", "bench_constraints_sha256"),
):
    cpath = repo_root / py_constraints[fkey]
    normalized = cpath.read_bytes().replace(b"\r\n", b"\n")
    actual_sha = hashlib.sha256(normalized).hexdigest()
    if actual_sha != py_constraints[hkey]:
        raise SystemExit(
            f"ERROR: {py_constraints[fkey]} SHA-256 mismatch: actual={actual_sha} != expected={py_constraints[hkey]}"
        )

print(lock["baseline_main_sha"])
print(lock["apps_tree_sha"])
print(lock["crm_cskh_tree_sha"])
print(lock["base_image"]["pinned_ref"])
print(lock["toolchain"]["nvm_version"])
print(lock["toolchain"]["nvm_install_sha256"])
print(lock["toolchain"]["node_version"])
print(lock["toolchain"]["yarn_version"])
print(lock["toolchain"]["frappe_bench_version"])
print(lock["toolchain"]["wkhtmltopdf_version"])
print(lock["toolchain"]["wkhtmltopdf_distro"])
print(lock["toolchain"]["wkhtmltopdf_amd64_sha256"])
print("true" if lock["toolchain"]["install_chromium"] else "false")
print(lock["apps"]["frappe"]["repository"])
print(lock["apps"]["frappe"]["tag"])
print(lock["apps"]["frappe"]["commit_sha"])
print(lock["apps"]["frappe"]["yarn_lock_sha256"])
print(lock["apps"]["crm"]["repository"])
print(lock["apps"]["crm"]["tag"])
print(lock["apps"]["crm"]["commit_sha"])
print(lock["apps"]["crm"]["root_yarn_lock_sha256"])
print(lock["apps"]["crm"]["frontend_yarn_lock_sha256"])
print(lock["builder_upstream"]["tag"])
print(lock["builder_upstream"]["commit_sha"])
print(lock["toolchain"]["uv_version"])
print(py_constraints["system_constraints_sha256"])
print(py_constraints["bench_constraints_sha256"])
print(lock["security_patches"]["frappe_form_data_version"])
PY
)

BASELINE_MAIN_SHA="${LOCK_VARS[0]}"
EXPECTED_APPS_TREE_SHA="${LOCK_VARS[1]}"
EXPECTED_CSKH_TREE_SHA="${LOCK_VARS[2]}"
BASE_IMAGE="${LOCK_VARS[3]}"
NVM_VERSION="${LOCK_VARS[4]}"
NVM_INSTALL_SHA256="${LOCK_VARS[5]}"
NODE_VERSION="${LOCK_VARS[6]}"
YARN_VERSION="${LOCK_VARS[7]}"
FRAPPE_BENCH_VERSION="${LOCK_VARS[8]}"
WKHTMLTOPDF_VERSION="${LOCK_VARS[9]}"
WKHTMLTOPDF_DISTRO="${LOCK_VARS[10]}"
WKHTMLTOPDF_AMD64_SHA256="${LOCK_VARS[11]}"
INSTALL_CHROMIUM="${LOCK_VARS[12]}"
FRAPPE_REPO="${LOCK_VARS[13]}"
FRAPPE_TAG="${LOCK_VARS[14]}"
FRAPPE_SHA="${LOCK_VARS[15]}"
FRAPPE_YARN_LOCK_SHA256="${LOCK_VARS[16]}"
CRM_REPO="${LOCK_VARS[17]}"
CRM_TAG="${LOCK_VARS[18]}"
CRM_SHA="${LOCK_VARS[19]}"
CRM_ROOT_YARN_LOCK_SHA256="${LOCK_VARS[20]}"
CRM_FRONTEND_YARN_LOCK_SHA256="${LOCK_VARS[21]}"
FRAPPE_DOCKER_TAG="${LOCK_VARS[22]}"
FRAPPE_DOCKER_SHA="${LOCK_VARS[23]}"
UV_VERSION="${LOCK_VARS[24]}"
SYSTEM_PYTHON_CONSTRAINTS_SHA256="${LOCK_VARS[25]}"
BENCH_PYTHON_CONSTRAINTS_SHA256="${LOCK_VARS[26]}"
FORM_DATA_PATCH_VERSION="${LOCK_VARS[27]}"

ACTUAL_HEAD_SHA="$(git -C "${REPO_ROOT}" rev-parse HEAD | tr -d '\r')"
ACTUAL_APPS_TREE_SHA="$(git -C "${REPO_ROOT}" rev-parse HEAD:apps | tr -d '\r')"
ACTUAL_CSKH_TREE_SHA="$(git -C "${REPO_ROOT}" rev-parse HEAD:apps/crm_cskh | tr -d '\r')"

if [ -n "$(git -C "${REPO_ROOT}" status --porcelain -- apps)" ]; then
  echo "ERROR: Uncommitted changes detected under apps/. Refusing non-reproducible build." >&2
  exit 1
fi
if [ "${ACTUAL_APPS_TREE_SHA}" != "${EXPECTED_APPS_TREE_SHA}" ]; then
  echo "ERROR: apps/ tree SHA mismatch: actual=${ACTUAL_APPS_TREE_SHA} expected=${EXPECTED_APPS_TREE_SHA}" >&2
  exit 1
fi
if [ "${ACTUAL_CSKH_TREE_SHA}" != "${EXPECTED_CSKH_TREE_SHA}" ]; then
  echo "ERROR: apps/crm_cskh tree SHA mismatch: actual=${ACTUAL_CSKH_TREE_SHA} expected=${EXPECTED_CSKH_TREE_SHA}" >&2
  exit 1
fi

SHORT_BASELINE="${BASELINE_MAIN_SHA:0:7}"
if [ -z "${LOCAL_IMAGE_TAG}" ]; then
  LOCAL_IMAGE_TAG="0.0.1-${SHORT_BASELINE}"
fi
LOCAL_IMAGE_FULL="${LOCAL_IMAGE_REPO}:${LOCAL_IMAGE_TAG}"

BUILD_LOG="${OUT_DIR}/build.log"
IID_FILE="${OUT_DIR}/.image-id.txt"
rm -f "${IID_FILE}"

EXTRA_BUILD_FLAGS=()
if [ "${NO_CACHE}" = "true" ]; then
  EXTRA_BUILD_FLAGS+=(--no-cache)
fi

echo "[build-image] Building ${LOCAL_IMAGE_FULL} from source=${ACTUAL_HEAD_SHA} (apps_tree=${ACTUAL_APPS_TREE_SHA}, no_cache=${NO_CACHE})"
docker buildx build \
  --platform linux/amd64 \
  --load \
  --progress=plain \
  "${EXTRA_BUILD_FLAGS[@]}" \
  --iidfile "${IID_FILE}" \
  --file "${CONTAINERFILE}" \
  --tag "${LOCAL_IMAGE_FULL}" \
  --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
  --build-arg "NVM_VERSION=${NVM_VERSION}" \
  --build-arg "NVM_INSTALL_SHA256=${NVM_INSTALL_SHA256}" \
  --build-arg "NODE_VERSION=${NODE_VERSION}" \
  --build-arg "YARN_VERSION=${YARN_VERSION}" \
  --build-arg "FRAPPE_BENCH_VERSION=${FRAPPE_BENCH_VERSION}" \
  --build-arg "UV_VERSION=${UV_VERSION}" \
  --build-arg "SYSTEM_PYTHON_CONSTRAINTS_SHA256=${SYSTEM_PYTHON_CONSTRAINTS_SHA256}" \
  --build-arg "BENCH_PYTHON_CONSTRAINTS_SHA256=${BENCH_PYTHON_CONSTRAINTS_SHA256}" \
  --build-arg "FORM_DATA_PATCH_VERSION=${FORM_DATA_PATCH_VERSION}" \
  --build-arg "WKHTMLTOPDF_VERSION=${WKHTMLTOPDF_VERSION}" \
  --build-arg "WKHTMLTOPDF_DISTRO=${WKHTMLTOPDF_DISTRO}" \
  --build-arg "WKHTMLTOPDF_AMD64_SHA256=${WKHTMLTOPDF_AMD64_SHA256}" \
  --build-arg "INSTALL_CHROMIUM=${INSTALL_CHROMIUM}" \
  --build-arg "FRAPPE_REPO=${FRAPPE_REPO}" \
  --build-arg "FRAPPE_TAG=${FRAPPE_TAG}" \
  --build-arg "FRAPPE_SHA=${FRAPPE_SHA}" \
  --build-arg "FRAPPE_YARN_LOCK_SHA256=${FRAPPE_YARN_LOCK_SHA256}" \
  --build-arg "CRM_REPO=${CRM_REPO}" \
  --build-arg "CRM_TAG=${CRM_TAG}" \
  --build-arg "CRM_SHA=${CRM_SHA}" \
  --build-arg "CRM_ROOT_YARN_LOCK_SHA256=${CRM_ROOT_YARN_LOCK_SHA256}" \
  --build-arg "CRM_FRONTEND_YARN_LOCK_SHA256=${CRM_FRONTEND_YARN_LOCK_SHA256}" \
  --build-arg "FRAPPE_DOCKER_TAG=${FRAPPE_DOCKER_TAG}" \
  --build-arg "FRAPPE_DOCKER_SHA=${FRAPPE_DOCKER_SHA}" \
  --build-arg "BASELINE_MAIN_SHA=${BASELINE_MAIN_SHA}" \
  --build-arg "CSKH_SOURCE_SHA=${ACTUAL_HEAD_SHA}" \
  --build-arg "APPS_TREE_SHA=${ACTUAL_APPS_TREE_SHA}" \
  --build-arg "CRM_CSKH_TREE_SHA=${ACTUAL_CSKH_TREE_SHA}" \
  "${REPO_ROOT}" 2>&1 | tee "${BUILD_LOG}"

LOCAL_IMAGE_ID="$(tr -d '\r\n' < "${IID_FILE}")"
rm -f "${IID_FILE}"

echo "[build-image] Extracting embedded release-metadata locks from ${LOCAL_IMAGE_ID}"
CID="$(docker create "${LOCAL_IMAGE_ID}")"
trap 'docker rm -f "${CID}" >/dev/null 2>&1 || true' EXIT
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" docker cp "${CID}:/home/frappe/frappe-bench/release-metadata/." "${OUT_DIR}/"
docker rm -f "${CID}" >/dev/null
trap - EXIT

REGISTRY_STATUS="BLOCKED"
REGISTRY_BLOCKER="No approved container registry URL or push credentials provisioned in P4-00 infra-inventory.md."
REGISTRY_REPO_DIGEST=""

if [ "${REGISTRY_PUSH}" = "true" ]; then
  if [ -z "${CSKH_REGISTRY_IMAGE}" ]; then
    echo "ERROR: REGISTRY_PUSH=true requires CSKH_REGISTRY_IMAGE=<registry>/<namespace>/crm-cskh" >&2
    exit 2
  fi
  REMOTE_TAGGED="${CSKH_REGISTRY_IMAGE}:${LOCAL_IMAGE_TAG}"
  docker tag "${LOCAL_IMAGE_ID}" "${REMOTE_TAGGED}"
  docker push "${REMOTE_TAGGED}"
  REGISTRY_REPO_DIGEST="$(docker image inspect "${REMOTE_TAGGED}" --format '{{index .RepoDigests 0}}' | tr -d '\r\n')"
  sh "${REPO_ROOT}/deploy/release/validate_digest_uri.sh" "${REGISTRY_REPO_DIGEST}"
  REGISTRY_STATUS="VERIFIED"
  REGISTRY_BLOCKER=""
fi

docker image inspect "${LOCAL_IMAGE_ID}" > "${OUT_DIR}/.raw-inspect.json"
"${PYTHON_BIN}" - "${OUT_DIR}/.raw-inspect.json" "${OUT_DIR}/image-inspect-summary.json" "${LOCAL_IMAGE_FULL}" "${LOCAL_IMAGE_ID}" "${REGISTRY_STATUS}" "${REGISTRY_REPO_DIGEST}" "${REGISTRY_BLOCKER}" <<'PY'
import json, sys
from pathlib import Path

raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))[0]
out_path = Path(sys.argv[2])
local_tag, local_id, reg_status, reg_digest, reg_blocker = sys.argv[3:8]

summary = {
    "schema_version": "cskh-image-inspect-v1",
    "local_image_tag": local_tag,
    "local_image_id": local_id,
    "repo_tags": raw.get("RepoTags") or [],
    "repo_digests": raw.get("RepoDigests") or [],
    "architecture": raw.get("Architecture"),
    "os": raw.get("Os"),
    "size_bytes": raw.get("Size"),
    "user": raw.get("Config", {}).get("User"),
    "working_dir": raw.get("Config", {}).get("WorkingDir"),
    "entrypoint": raw.get("Config", {}).get("Entrypoint"),
    "cmd": raw.get("Config", {}).get("Cmd"),
    "volumes": sorted((raw.get("Config", {}).get("Volumes") or {}).keys()),
    "labels": raw.get("Config", {}).get("Labels") or {},
    "rootfs_layers": (raw.get("RootFS") or {}).get("Layers") or [],
    "registry_verification": {
        "status": reg_status,
        "approved_registry_repo_digest": reg_digest or None,
        "blocker": reg_blocker or None,
    },
    "compose_handoff": {
        "APP_IMAGE_for_remote_deploy": reg_digest if reg_digest else None,
        "APP_IMAGE_for_local_isolated_smoke": (
            (raw.get("RepoDigests") or [None])[0] or local_id
        ),
        "LOCAL_TAG_REFERENCE": local_tag,
        "PULL_POLICY_local": "never",
        "PULL_POLICY_staging_prod": "always",
    },
}
out_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
PY
rm -f "${OUT_DIR}/.raw-inspect.json"

echo "[build-image] Build complete: local_image_id=${LOCAL_IMAGE_ID} registry_status=${REGISTRY_STATUS}"
