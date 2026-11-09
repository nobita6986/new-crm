#!/usr/bin/env python3
"""Preflight auditor for Container Registry, Ed25519 signing, and privilege separation (P4-02).

Implements Work Item 4:
1. Checks existing configuration and built-in GitHub Actions permissions BEFORE requesting
   any new secrets:
   - Detects when `CSKH_REGISTRY_IMAGE` targets `ghcr.io/<this-owner>/...` where workflow
     `GITHUB_TOKEN` permissions (`packages: write` in `registry-build`, `packages: read` in
     `registry-verify`) ALREADY satisfy CI push and pull authentication without creating extra
     robot tokens.
   - Identifies the minimal missing variables/secrets per isolated role (`push`, `pull`,
     `deploy-staging`, `deploy-production`) without ever printing secret values.
2. Enforces strict privilege separation across:
   - `push` (`registry-build`: image push + Ed25519 manifest signing; ZERO SSH deploy keys),
   - `pull` (`registry-verify`: read-only image pull + clean-site/regression verification;
     ZERO push token, ZERO signing private key, ZERO SSH deploy keys),
   - `deploy` (`staging` / `production`: read-only image pull + target host SSH; ZERO push
     token, ZERO signing private key, ZERO local `docker build`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_manifest import normalize_public_key_fingerprint  # noqa: E402

REPO_IMAGE_RE = re.compile(r"\A[^/@\s:<>]+(?::[0-9]+)?(?:/[^/@\s:<>]+)+\Z")
ROLES = ("preflight", "push", "pull", "deploy-staging", "deploy-production")


def _has_env(environ: dict[str, str], key: str) -> bool:
    return bool(environ.get(key, "").strip())


def _validate_public_key_pem(pem_text: str) -> tuple[bool, str | None, str | None]:
    clean = pem_text.strip().encode("utf-8").replace(b"\r\n", b"\n")
    if not clean:
        return False, None, "empty"
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        pub = serialization.load_pem_public_key(clean)
        if not isinstance(pub, ed25519.Ed25519PublicKey):
            return False, None, "not an Ed25519 public key"
        return True, normalize_public_key_fingerprint(clean), None
    except ImportError:
        if b"-----BEGIN PUBLIC KEY-----" not in clean:
            return False, None, "missing PEM PUBLIC KEY header"
        try:
            return True, normalize_public_key_fingerprint(clean), None
        except Exception as exc:
            return False, None, str(exc)
    except Exception as exc:
        return False, None, str(exc)


def audit_registry_and_signing(
    *,
    env: dict[str, str] | None = None,
    role: str = "preflight",
) -> dict[str, object]:
    if role not in ROLES:
        raise ValueError(f"Unsupported role {role!r}; expected one of {ROLES}")

    environ = env if env is not None else dict(os.environ)
    reg_image = environ.get("CSKH_REGISTRY_IMAGE", "").strip()
    github_repo = environ.get("GITHUB_REPOSITORY", "").strip()
    is_public_reg = environ.get("CSKH_REGISTRY_IS_PUBLIC", "").strip().lower() == "true"

    reg_valid = False
    reg_host = ""
    reg_error: str | None = None
    if not reg_image:
        reg_error = "vars.CSKH_REGISTRY_IMAGE is unset"
    elif "BLOCKED" in reg_image.upper() or "<" in reg_image or ">" in reg_image:
        reg_error = f"vars.CSKH_REGISTRY_IMAGE contains placeholder/BLOCKED value: {reg_image!r}"
    elif not REPO_IMAGE_RE.match(reg_image):
        reg_error = (
            f"vars.CSKH_REGISTRY_IMAGE {reg_image!r} must be '<registry>/<namespace>/<repo>' "
            "(without tag or @sha256 suffix)"
        )
    else:
        reg_valid = True
        reg_host = reg_image.split("/", 1)[0].lower()

    # Check whether built-in GitHub Actions GITHUB_TOKEN permissions cover GHCR push/pull
    repo_owner = github_repo.split("/", 1)[0].lower() if "/" in github_repo else ""
    reg_parts = reg_image.lower().split("/") if reg_valid else []
    ghcr_same_owner = (
        reg_valid
        and reg_host == "ghcr.io"
        and len(reg_parts) >= 3
        and (not repo_owner or reg_parts[1] == repo_owner)
    )
    has_github_token = (
        _has_env(environ, "GITHUB_TOKEN")
        or _has_env(environ, "GITHUB_TOKEN_PUSH")
        or _has_env(environ, "GITHUB_TOKEN_READONLY")
    )

    has_explicit_push = _has_env(environ, "REGISTRY_PUSH_USERNAME") and _has_env(
        environ, "REGISTRY_PUSH_TOKEN"
    )
    pull_user_present = _has_env(environ, "REGISTRY_PULL_USERNAME") or _has_env(
        environ, "CSKH_REGISTRY_PULL_USERNAME"
    )
    has_explicit_pull = pull_user_present and _has_env(environ, "REGISTRY_PULL_TOKEN")

    pub_key_raw = environ.get("RELEASE_SIGNING_PUBLIC_KEY", "").strip()
    pub_valid, pub_fp, pub_err = _validate_public_key_pem(pub_key_raw)
    has_signing_priv = _has_env(environ, "RELEASE_SIGNING_PRIVATE_KEY")

    # Determine effective auth mechanism per separated role BEFORE asking for new secrets
    push_auth_mode = (
        "explicit_robot_secret"
        if has_explicit_push
        else (
            "github_token_packages_write"
            if ghcr_same_owner and (has_github_token or role == "preflight")
            else "missing"
        )
    )
    ci_pull_auth_mode = (
        "explicit_robot_secret"
        if has_explicit_pull
        else (
            "github_token_packages_read"
            if ghcr_same_owner and (has_github_token or role == "preflight")
            else ("anonymous_public_pull" if is_public_reg else "missing")
        )
    )
    vps_pull_auth_mode = (
        "explicit_robot_secret"
        if has_explicit_pull
        else ("anonymous_public_pull" if is_public_reg else "missing_on_remote_vps")
    )

    missing_vars: list[str] = []
    missing_secrets: list[str] = []
    role_violations: list[str] = []

    if not reg_valid:
        missing_vars.append("CSKH_REGISTRY_IMAGE")
    if not pub_valid:
        missing_vars.append("RELEASE_SIGNING_PUBLIC_KEY")

    if role == "push":
        if push_auth_mode == "missing":
            missing_secrets.extend(["REGISTRY_PUSH_USERNAME", "REGISTRY_PUSH_TOKEN"])
        if not has_signing_priv:
            missing_secrets.append("RELEASE_SIGNING_PRIVATE_KEY")
        # Privilege separation: push job must not hold SSH deploy keys
        for forbidden in ("STAGING_SSH_PRIVATE_KEY", "PROD_SSH_PRIVATE_KEY"):
            if _has_env(environ, forbidden):
                role_violations.append(
                    f"Privilege separation violation: push role has {forbidden} set"
                )

    elif role == "pull":
        if ci_pull_auth_mode == "missing":
            missing_secrets.extend(["REGISTRY_PULL_USERNAME", "REGISTRY_PULL_TOKEN"])
        for forbidden in (
            "REGISTRY_PUSH_TOKEN",
            "RELEASE_SIGNING_PRIVATE_KEY",
            "STAGING_SSH_PRIVATE_KEY",
            "PROD_SSH_PRIVATE_KEY",
        ):
            if _has_env(environ, forbidden):
                role_violations.append(
                    f"Privilege separation violation: pull role has {forbidden} set"
                )

    elif role == "deploy-staging":
        for var_name in ("STAGING_DEPLOY_PATH", "STAGING_ENV_FILE"):
            if not _has_env(environ, var_name):
                missing_vars.append(var_name)
        for sec_name in (
            "STAGING_SSH_HOST",
            "STAGING_SSH_USER",
            "STAGING_SSH_PRIVATE_KEY",
            "STAGING_KNOWN_HOSTS",
        ):
            if not _has_env(environ, sec_name):
                missing_secrets.append(sec_name)
        if vps_pull_auth_mode == "missing_on_remote_vps":
            missing_secrets.extend(["REGISTRY_PULL_USERNAME", "REGISTRY_PULL_TOKEN"])
        for forbidden in (
            "REGISTRY_PUSH_TOKEN",
            "RELEASE_SIGNING_PRIVATE_KEY",
            "PROD_SSH_PRIVATE_KEY",
        ):
            if _has_env(environ, forbidden):
                role_violations.append(
                    f"Privilege separation violation: deploy-staging has {forbidden} set"
                )

    elif role == "deploy-production":
        for var_name in (
            "PROD_DEPLOY_PATH",
            "PROD_ENV_FILE",
            "PROD_PRE_RELEASE_BACKUP_CMD",
        ):
            if not _has_env(environ, var_name):
                missing_vars.append(var_name)
        for sec_name in (
            "PROD_SSH_HOST",
            "PROD_SSH_USER",
            "PROD_SSH_PRIVATE_KEY",
            "PROD_KNOWN_HOSTS",
        ):
            if not _has_env(environ, sec_name):
                missing_secrets.append(sec_name)
        if vps_pull_auth_mode == "missing_on_remote_vps":
            missing_secrets.extend(["REGISTRY_PULL_USERNAME", "REGISTRY_PULL_TOKEN"])
        for forbidden in (
            "REGISTRY_PUSH_TOKEN",
            "RELEASE_SIGNING_PRIVATE_KEY",
            "STAGING_SSH_PRIVATE_KEY",
        ):
            if _has_env(environ, forbidden):
                role_violations.append(
                    f"Privilege separation violation: deploy-production has {forbidden} set"
                )

    ready = not missing_vars and not missing_secrets and not role_violations
    return {
        "schema_version": "cskh-p4-registry-signing-config-audit-v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "status": "READY" if ready else "BLOCKED",
        "blocker_id": None if ready else "BLK-INFRA-03",
        "registry": {
            "configured_repo": reg_image or None,
            "valid": reg_valid,
            "registry_host": reg_host or None,
            "validation_error": reg_error,
            "ghcr_builtin_github_token_eligible": ghcr_same_owner,
            "is_public_pull": is_public_reg,
            "effective_push_auth": push_auth_mode,
            "effective_ci_pull_auth": ci_pull_auth_mode,
            "effective_vps_pull_auth": vps_pull_auth_mode,
        },
        "manifest_signing": {
            "public_key_configured": pub_valid,
            "public_key_fingerprint_sha256": pub_fp,
            "public_key_validation_error": pub_err if not pub_valid else None,
            "private_key_present_in_current_env": has_signing_priv,
            "strict_gate_required_for_production_candidate": True,
        },
        "privilege_separation_matrix": {
            "registry-build": {
                "allowed": [
                    "packages:write / REGISTRY_PUSH_*",
                    "RELEASE_SIGNING_PRIVATE_KEY",
                ],
                "forbidden": ["REGISTRY_PULL_*", "STAGING_SSH_*", "PROD_SSH_*"],
            },
            "registry-verify": {
                "allowed": ["packages:read / REGISTRY_PULL_*"],
                "forbidden": [
                    "packages:write",
                    "REGISTRY_PUSH_*",
                    "RELEASE_SIGNING_PRIVATE_KEY",
                    "STAGING_SSH_*",
                    "PROD_SSH_*",
                ],
            },
            "staging": {
                "allowed": ["REGISTRY_PULL_*", "STAGING_SSH_*"],
                "forbidden": [
                    "packages:write",
                    "REGISTRY_PUSH_*",
                    "RELEASE_SIGNING_PRIVATE_KEY",
                    "PROD_SSH_*",
                ],
            },
            "production": {
                "allowed": ["REGISTRY_PULL_*", "PROD_SSH_*"],
                "forbidden": [
                    "packages:write",
                    "REGISTRY_PUSH_*",
                    "RELEASE_SIGNING_PRIVATE_KEY",
                    "STAGING_SSH_*",
                ],
            },
        },
        "missing_repository_or_env_variables": sorted(set(missing_vars)),
        "minimal_secrets_to_request": sorted(set(missing_secrets)),
        "privilege_separation_violations": role_violations,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--role", default="preflight", choices=list(ROLES))
    p.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero if configuration for --role is BLOCKED or violates separation",
    )
    p.add_argument("--out", default="", help="Optional JSON report path")
    args = p.parse_args(argv)

    report = audit_registry_and_signing(role=args.role)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(rendered.encode("utf-8"))
    print(rendered, end="")
    if args.require_ready and report["status"] != "READY":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
