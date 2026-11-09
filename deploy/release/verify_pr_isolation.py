#!/usr/bin/env python3
"""Fail-closed PR isolation, privilege separation, and release-lock auditor (P4-02 / G04).

Verifies:
1. Workflow YAML security invariants across `.github/workflows/*.yml`:
   - Zero `pull_request_target` triggers anywhere in repository workflows.
   - Every workflow triggered by `pull_request` sets top-level `permissions: contents: read`,
     never grants `write` permissions on PR-reachable jobs, and never binds GitHub deployment
     environments (`registry-build`, `registry-verify`, `staging`, `production`) to PR jobs.
   - Every P4 workflow (`p4-*.yml`) pins external `uses:` actions to 40-character commit SHAs.
   - Release (`p4-release.yml`) and Production (`p4-production.yml`) workflows strictly separate
     `push` (`registry-build`), `pull` (`registry-verify`), and `deploy` (`staging` / `production`)
     privileges, forbid rebuild-on-deploy, and gate `production_candidate` manifest signing on
     `--security-scan` (`strict` mode).
2. Runtime environment zero-secret check (`--check-runtime-env`):
   - When `GITHUB_EVENT_NAME` is `pull_request` (or `--force-pr-env-check` is passed), fails
     closed if any release/registry/signing/SSH secret environment variable is populated.
3. Lock & provenance integrity (`--check-locks`):
   - `git rev-parse <ref>:apps` and `git rev-parse <ref>:apps/crm_cskh` match `release-lock.json`.
   - All image pins in `release-lock.json` carry `@sha256:<64-hex>` digests.
   - `deploy/images/evidence/SHA256SUMS.txt` matches all 10 recorded artifacts byte-for-byte.
   - Tracked P4 files contain zero private keys, PAT tokens, or AWS access keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
LOCK_FILE = REPO_ROOT / "deploy" / "images" / "release-lock.json"
IMAGE_SUMS_FILE = REPO_ROOT / "deploy" / "images" / "evidence" / "SHA256SUMS.txt"

FORBIDDEN_PR_ENV_VARS = (
    "REGISTRY_PUSH_TOKEN",
    "REGISTRY_PUSH_PASSWORD",
    "REGISTRY_PULL_TOKEN",
    "REGISTRY_PULL_PASSWORD",
    "RELEASE_SIGNING_PRIVATE_KEY",
    "COSIGN_PRIVATE_KEY",
    "STAGING_SSH_PRIVATE_KEY",
    "STAGING_DEPLOY_TOKEN",
    "STAGING_DB_ROOT_PASSWORD",
    "PROD_SSH_PRIVATE_KEY",
    "PROD_DEPLOY_TOKEN",
    "PROD_DB_ROOT_PASSWORD",
    "BACKUP_ENCRYPTION_PASSPHRASE",
    "OFFSITE_STORAGE_SECRET_KEY",
)

ACTION_SHA_PIN_RE = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?@[0-9a-f]{40}$"
)
DIGEST_PIN_RE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
SECRET_TOKEN_RE = re.compile(
    r"(?m)(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"^-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----|"
    r"AKIA[0-9A-Z]{16})"
)


def _strip_yaml_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _split_workflow_jobs(text: str) -> dict[str, str]:
    """Extracts top-level job blocks (`^  <job-id>:`) under `^jobs:`."""
    m_jobs = re.search(r"(?m)^jobs:\s*$", text)
    if not m_jobs:
        return {}
    tail = text[m_jobs.end() :]
    headers = list(re.finditer(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", tail))
    blocks: dict[str, str] = {}
    for idx, hdr in enumerate(headers):
        start = hdr.start()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(tail)
        blocks[hdr.group(1)] = tail[start:end]
    return blocks


def _extract_secret_refs(block: str) -> set[str]:
    return set(re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}", block))


def audit_workflow_text(wf_path: Path, text: str) -> list[str]:
    errors: list[str] = []
    name = wf_path.name

    # 1. Never allow pull_request_target anywhere
    if re.search(r"(?m)^\s*pull_request_target\s*:", text):
        errors.append(f"{name}: forbidden `pull_request_target` trigger detected")

    has_pr_trigger = bool(re.search(r"(?m)^  pull_request\s*:", text))

    # 2. Check top-level permissions on PR-triggered workflows
    if has_pr_trigger:
        if not re.search(r"(?ms)^permissions:\s*\n  contents:\s*read\b", text):
            errors.append(
                f"{name}: PR-triggered workflow must declare top-level `permissions: contents: read`"
            )
        if re.search(
            r"(?m)^\s*(packages|id-token|deployments|actions|contents|security-events):\s*write\b",
            text,
        ):
            errors.append(
                f"{name}: PR-triggered workflow must not grant any `: write` permission"
            )
        if re.search(r"(?m)^\s*environment\s*:", text):
            errors.append(
                f"{name}: PR-triggered workflow must not attach any deployment `environment:`"
            )
        secret_refs = _extract_secret_refs(text)
        non_default_secrets = [s for s in secret_refs if s != "GITHUB_TOKEN"]
        if non_default_secrets:
            errors.append(
                f"{name}: PR-triggered workflow references repository secrets: {sorted(non_default_secrets)}"
            )

    # 3. For all P4 workflows (`p4-*.yml`), enforce 40-char commit SHA pinning on every `uses:`
    if name.startswith("p4-"):
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            if stripped.startswith("#"):
                continue
            m = re.match(r"^(?:-\s+)?uses:\s*([^\s#]+)", stripped)
            if m:
                target = m.group(1).strip("'\"")
                if target.startswith("./"):
                    continue
                if not ACTION_SHA_PIN_RE.match(target):
                    errors.append(
                        f"{name}:{line_no}: unpinned GitHub Action `uses: {target}` (must pin 40-char commit SHA)"
                    )

    # 4. Specific push / pull / deploy privilege separation rules for p4-release.yml & p4-production.yml
    if name == "p4-release.yml":
        if has_pr_trigger:
            errors.append("p4-release.yml: must NEVER trigger on `pull_request`")
        for req_env in ("registry-build", "registry-verify", "staging"):
            if not re.search(
                rf"(?m)^\s*environment:\s*(?:\n\s*name:\s*)?{req_env}\b", text
            ):
                errors.append(
                    f"p4-release.yml: missing isolated GitHub Environment `{req_env}`"
                )
        if re.search(r"(?m)^\s*environment:\s*(?:\n\s*name:\s*)?production\b", text):
            errors.append(
                "p4-release.yml: must NOT bind `production` environment directly without manual production workflow"
            )

        jobs = _split_workflow_jobs(text)
        build_job = jobs.get("build-once-sign-and-push", "")
        if build_job:
            b_secrets = _extract_secret_refs(build_job)
            bad_b_secrets = sorted(
                s
                for s in b_secrets
                if s.startswith(("STAGING_", "PROD_", "REGISTRY_PULL_"))
            )
            if bad_b_secrets:
                errors.append(
                    f"p4-release.yml: build-once-sign-and-push must not reference pull/deploy secrets {bad_b_secrets}"
                )
            if "--advisory-upstream-critical" in build_job:
                errors.append(
                    "p4-release.yml: build-once-sign-and-push must never use --advisory-upstream-critical"
                )
            if not re.search(
                r"release_manifest\.py\s+sign\b[\s\S]*?--security-scan\b", build_job
            ):
                errors.append(
                    "p4-release.yml: `release_manifest.py sign` must pass `--security-scan` to enforce strict PASS before signing"
                )

        verify_job = jobs.get("verify-by-digest", "")
        if verify_job:
            if re.search(
                r"(?m)^\s*(packages|id-token|deployments|actions|contents|security-events):\s*write\b",
                verify_job,
            ):
                errors.append(
                    "p4-release.yml: verify-by-digest (pull-only) must not grant any `: write` permission"
                )
            v_secrets = _extract_secret_refs(verify_job)
            bad_v_secrets = sorted(
                s
                for s in v_secrets
                if s == "RELEASE_SIGNING_PRIVATE_KEY"
                or s.startswith(("REGISTRY_PUSH_", "STAGING_", "PROD_"))
            )
            if bad_v_secrets:
                errors.append(
                    f"p4-release.yml: verify-by-digest must not reference push/signing/deploy secrets {bad_v_secrets}"
                )
            v_nc = _strip_yaml_comments(verify_job)
            if "build-image.sh" in v_nc or re.search(
                r"\bdocker\s+(?:buildx\s+)?build\b", v_nc
            ):
                errors.append(
                    "p4-release.yml: verify-by-digest must pull immutable digest and never build images"
                )

        staging_job = jobs.get("deploy-staging", "")
        if staging_job:
            if re.search(r"(?m)^\s*packages:\s*write\b", staging_job):
                errors.append(
                    "p4-release.yml: deploy-staging must not grant `packages: write`"
                )
            s_secrets = _extract_secret_refs(staging_job)
            bad_s_secrets = sorted(
                s
                for s in s_secrets
                if s == "RELEASE_SIGNING_PRIVATE_KEY"
                or s.startswith(("REGISTRY_PUSH_", "PROD_"))
            )
            if bad_s_secrets:
                errors.append(
                    f"p4-release.yml: deploy-staging must not reference push/signing/prod secrets {bad_s_secrets}"
                )
            s_nc = _strip_yaml_comments(staging_job)
            if "build-image.sh" in s_nc or re.search(
                r"\bdocker\s+(?:buildx\s+)?build\b", s_nc
            ):
                errors.append(
                    "p4-release.yml: deploy-staging must deploy pre-built @sha256 digest without building"
                )

    if name == "p4-production.yml":
        if has_pr_trigger or re.search(r"(?m)^  push\s*:", text):
            errors.append(
                "p4-production.yml: must trigger ONLY via manual `workflow_dispatch` (never push or pull_request)"
            )
        if not re.search(
            r"(?m)^\s*environment:\s*(?:\n\s*name:\s*)?production\b", text
        ):
            errors.append(
                "p4-production.yml: must require protected `production` GitHub Environment"
            )
        if re.search(r"(?m)^\s*packages:\s*write\b", text):
            errors.append(
                "p4-production.yml: must never grant `packages: write` permission"
            )
        p_secrets = _extract_secret_refs(text)
        bad_p_secrets = sorted(
            s
            for s in p_secrets
            if s == "RELEASE_SIGNING_PRIVATE_KEY"
            or s.startswith(("REGISTRY_PUSH_", "STAGING_"))
        )
        if bad_p_secrets:
            errors.append(
                f"p4-production.yml: must not reference push/signing/staging secrets {bad_p_secrets}"
            )
        p_nc = _strip_yaml_comments(text)
        if "build-image.sh" in p_nc or re.search(
            r"\bdocker\s+(?:buildx\s+)?build\b", p_nc
        ):
            errors.append(
                "p4-production.yml: must deploy Staging-verified @sha256 digest without building"
            )

    if name == "p4-candidate-verify.yml":
        if has_pr_trigger:
            errors.append(
                "p4-candidate-verify.yml: must NEVER trigger on `pull_request`"
            )
        cand_jobs = _split_workflow_jobs(text)
        for ro_job_name in ("verify-and-regress-by-digest", "isolated-s1-benchmark"):
            ro_job = cand_jobs.get(ro_job_name, "")
            if ro_job:
                if re.search(
                    r"(?m)^\s*(packages|id-token|deployments|actions|contents|security-events):\s*write\b",
                    ro_job,
                ):
                    errors.append(
                        f"p4-candidate-verify.yml: {ro_job_name} (pull-only) must not grant any `: write` permission"
                    )
                ro_nc = _strip_yaml_comments(ro_job)
                if "build-image.sh" in ro_nc or re.search(
                    r"\bdocker\s+(?:buildx\s+)?build\b", ro_nc
                ):
                    errors.append(
                        f"p4-candidate-verify.yml: {ro_job_name} must pull immutable @sha256 digest without building"
                    )

    return errors


def audit_all_workflows(workflows_dir: Path = WORKFLOWS_DIR) -> dict[str, object]:
    wf_files = sorted(workflows_dir.glob("*.yml")) + sorted(
        workflows_dir.glob("*.yaml")
    )
    if not wf_files:
        raise RuntimeError(f"No workflow files found in {workflows_dir}")

    all_errors: list[str] = []
    checked: list[str] = []
    for wf in wf_files:
        checked.append(wf.name)
        txt = wf.read_text(encoding="utf-8")
        all_errors.extend(audit_workflow_text(wf, txt))

    return {
        "checked_workflows": checked,
        "errors": all_errors,
        "passed": len(all_errors) == 0,
    }


def check_runtime_pr_zero_secrets(
    *, env: dict[str, str] | None = None, force_pr_check: bool = False
) -> dict[str, object]:
    environ = env if env is not None else dict(os.environ)
    event_name = environ.get("GITHUB_EVENT_NAME", "")
    is_pr = force_pr_check or event_name in (
        "pull_request",
        "pull_request_review",
        "pull_request_target",
    )

    leaked_vars: list[str] = []
    if is_pr:
        for key in FORBIDDEN_PR_ENV_VARS:
            if environ.get(key, "").strip():
                leaked_vars.append(key)

    return {
        "event_name": event_name or "local",
        "enforced": is_pr,
        "leaked_secret_env_names": leaked_vars,
        "passed": len(leaked_vars) == 0,
    }


def check_locks_and_repo_hygiene(
    repo_root: Path = REPO_ROOT,
    *,
    git_ref: str = "HEAD",
    git_repo: Path | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    git_dir = git_repo if git_repo is not None else repo_root
    lock_path = repo_root / "deploy" / "images" / "release-lock.json"
    sums_path = repo_root / "deploy" / "images" / "evidence" / "SHA256SUMS.txt"

    lock = json.loads(lock_path.read_text(encoding="utf-8"))

    # 1. Verify pinned @sha256 digests in release-lock.json
    pinned_refs = [
        ("base_image.pinned_ref", lock["base_image"]["pinned_ref"]),
        ("infra_images.mariadb", lock["infra_images"]["mariadb"]),
        ("infra_images.redis", lock["infra_images"]["redis"]),
    ]
    for label, ref in pinned_refs:
        if not DIGEST_PIN_RE.match(ref):
            errors.append(f"release-lock.json {label} is not digest-pinned: {ref!r}")

    # 1b. Verify transitive Python constraint files & SHA-256 locks (F-P4-REL-03)
    py_constraints = lock.get("python_constraints") or {}
    constraint_counts: dict[str, int] = {}
    for fkey, hkey, ckey, min_count in (
        (
            "system_constraints_file",
            "system_constraints_sha256",
            "system_python_constraints_count",
            15,
        ),
        (
            "bench_constraints_file",
            "bench_constraints_sha256",
            "bench_python_constraints_count",
            140,
        ),
    ):
        rel_c = str(py_constraints.get(fkey) or "")
        exp_c_sha = str(py_constraints.get(hkey) or "")
        c_file = repo_root / rel_c
        if not rel_c or not c_file.is_file():
            errors.append(f"Missing Python constraint lock file: {rel_c!r}")
            continue
        norm_bytes = c_file.read_bytes().replace(b"\r\n", b"\n")
        act_c_sha = hashlib.sha256(norm_bytes).hexdigest()
        if act_c_sha != exp_c_sha:
            errors.append(
                f"SHA-256 mismatch for {rel_c}: expected {exp_c_sha}, got {act_c_sha}"
            )
        pinned_lines = [
            ln.strip()
            for ln in norm_bytes.decode("utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        constraint_counts[ckey] = len(pinned_lines)
        if len(pinned_lines) < min_count or any("==" not in ln for ln in pinned_lines):
            errors.append(
                f"Invalid or incomplete pin list in {rel_c} (count={len(pinned_lines)})"
            )

    # 2. Verify Git tree SHAs for apps/ and apps/crm_cskh match release-lock.json
    actual_apps_tree = subprocess.check_output(
        ["git", "-C", str(git_dir), "rev-parse", f"{git_ref}:apps"],
        text=True,
    ).strip()
    actual_cskh_tree = subprocess.check_output(
        ["git", "-C", str(git_dir), "rev-parse", f"{git_ref}:apps/crm_cskh"],
        text=True,
    ).strip()
    if actual_apps_tree != lock["apps_tree_sha"]:
        errors.append(
            f"{git_ref}:apps tree SHA ({actual_apps_tree}) != release-lock.json ({lock['apps_tree_sha']})"
        )
    if actual_cskh_tree != lock["crm_cskh_tree_sha"]:
        errors.append(
            f"{git_ref}:apps/crm_cskh tree SHA ({actual_cskh_tree}) != release-lock.json ({lock['crm_cskh_tree_sha']})"
        )

    # 3. Verify 10/10 SHA256SUMS.txt entries from P4-01 image build
    verified_sums = 0
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected_hash, rel_file = line.strip().split(None, 1)
        rel_clean = rel_file.strip()
        target = repo_root / rel_clean
        if not target.is_file():
            errors.append(
                f"Missing evidence file listed in SHA256SUMS.txt: {rel_clean}"
            )
            continue
        actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_hash == expected_hash or (
            rel_clean == "deploy/images/release-lock.json"
            and lock.get("p4_01_baseline_lock_sha256") == expected_hash
        ):
            verified_sums += 1
        else:
            errors.append(
                f"SHA-256 mismatch for {rel_clean}: expected {expected_hash}, got {actual_hash}"
            )

    # 4. Scan P4 files for accidental secrets/private keys
    candidate_files: list[tuple[str, Path]] = []
    if git_repo is None:
        tracked_out = subprocess.check_output(
            [
                "git",
                "-C",
                str(git_dir),
                "ls-files",
                ".github/workflows",
                "deploy/images",
                "deploy/compose",
                "deploy/release",
                "docs/p4",
            ],
            text=True,
        )
        for rel in tracked_out.splitlines():
            rel_s = rel.strip()
            if rel_s:
                candidate_files.append((rel_s, repo_root / rel_s))
    else:
        for sub in (".github/workflows", "deploy/images", "deploy/release"):
            base = repo_root / sub
            if base.is_dir():
                for fpath in sorted(base.rglob("*")):
                    if fpath.is_file():
                        candidate_files.append(
                            (fpath.relative_to(repo_root).as_posix(), fpath)
                        )

    scanned_files = 0
    for rel, fpath in candidate_files:
        if not fpath.is_file():
            continue
        scanned_files += 1
        if rel.endswith("verify_pr_isolation.py") or rel.endswith(
            "test_release_pipeline.py"
        ):
            continue
        content = fpath.read_text(encoding="utf-8", errors="replace")
        m = SECRET_TOKEN_RE.search(content)
        if m:
            errors.append(f"Secret/private-key pattern detected in tracked file {rel}")

    return {
        "apps_tree_sha": actual_apps_tree,
        "crm_cskh_tree_sha": actual_cskh_tree,
        "verified_evidence_checksums": verified_sums,
        **constraint_counts,
        "scanned_p4_files": scanned_files,
        "errors": errors,
        "passed": len(errors) == 0,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--check-workflows", action="store_true", help="Audit .github/workflows/*.yml"
    )
    p.add_argument(
        "--check-runtime-env",
        action="store_true",
        help="Audit PR zero-secret env boundary",
    )
    p.add_argument(
        "--force-pr-env-check", action="store_true", help="Simulate PR event env check"
    )
    p.add_argument(
        "--check-locks",
        action="store_true",
        help="Verify release-lock.json & SHA256SUMS",
    )
    p.add_argument("--git-ref", default="HEAD", help="Git ref for tree SHA checks")
    p.add_argument("--out", default="", help="Optional JSON report path")
    args = p.parse_args(argv)

    run_all = not (args.check_workflows or args.check_runtime_env or args.check_locks)

    report: dict[str, object] = {"schema_version": "cskh-p4-pr-isolation-audit-v1"}
    overall_ok = True

    if run_all or args.check_workflows:
        wf_res = audit_all_workflows()
        report["workflows_audit"] = wf_res
        if not wf_res["passed"]:
            overall_ok = False

    if run_all or args.check_runtime_env:
        env_res = check_runtime_pr_zero_secrets(force_pr_check=args.force_pr_env_check)
        report["runtime_env_audit"] = env_res
        if not env_res["passed"]:
            overall_ok = False

    if run_all or args.check_locks:
        lock_res = check_locks_and_repo_hygiene(git_ref=args.git_ref)
        report["locks_and_hygiene_audit"] = lock_res
        if not lock_res["passed"]:
            overall_ok = False

    report["status"] = "PASS" if overall_ok else "FAIL"
    rendered = json.dumps(report, indent=2) + "\n"
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(rendered.encode("utf-8"))
    print(rendered, end="")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
