#!/usr/bin/env python3
"""Offline deterministic validator for P4-01 Compose & environment configurations.

Verifies:
1. Byte-exact provenance of vendored frappe/frappe_docker@v4.0.1 (2b26e9ab8d).
2. Secret-free template hygiene and @sha256:<64-hex> digest pinning across env templates.
3. Merged `docker compose config --services` and `config --format json` across
   production, staging, and isolated-smoke stacks (+ `--profile migrate`).
4. Network segmentation, volume/site/secret isolation, port exposure limits,
   healthchecks, and fail-closed error handling when required inputs are missing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

COMPOSE_ROOT = pathlib.Path(__file__).resolve().parents[1]
UPSTREAM_DIR = COMPOSE_ROOT / "upstream" / "frappe_docker"
OVERRIDES_DIR = COMPOSE_ROOT / "overrides"
ENV_DIR = COMPOSE_ROOT / "env"

DIGEST_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
PLACEHOLDER_DIGEST = "sha256:" + ("0" * 64)

EXPECTED_DEFAULT_SERVICES = sorted(
    [
        "backend",
        "configurator",
        "db",
        "frontend",
        "proxy",
        "queue-long",
        "queue-short",
        "redis-cache",
        "redis-queue",
        "scheduler",
        "websocket",
    ]
)
EXPECTED_LONG_RUNNING_SERVICES = sorted(
    s for s in EXPECTED_DEFAULT_SERVICES if s != "configurator"
)

STACK_PROFILES = {
    "production": {
        "env_template": ENV_DIR / "production.env.example",
        "compose_files": [
            UPSTREAM_DIR / "compose.yaml",
            UPSTREAM_DIR / "overrides" / "compose.mariadb.yaml",
            UPSTREAM_DIR / "overrides" / "compose.mariadb-secrets.yaml",
            UPSTREAM_DIR / "overrides" / "compose.redis.yaml",
            UPSTREAM_DIR / "overrides" / "compose.https.yaml",
            UPSTREAM_DIR / "overrides" / "compose.migrator.yaml",
            OVERRIDES_DIR / "compose.cskh-common.yaml",
            OVERRIDES_DIR / "compose.cskh-tls.yaml",
        ],
        "expected_ports": [
            ("0.0.0.0", "80", 80, "tcp"),
            ("0.0.0.0", "443", 443, "tcp"),
        ],
    },
    "staging": {
        "env_template": ENV_DIR / "staging.env.example",
        "compose_files": [
            UPSTREAM_DIR / "compose.yaml",
            UPSTREAM_DIR / "overrides" / "compose.mariadb.yaml",
            UPSTREAM_DIR / "overrides" / "compose.mariadb-secrets.yaml",
            UPSTREAM_DIR / "overrides" / "compose.redis.yaml",
            UPSTREAM_DIR / "overrides" / "compose.https.yaml",
            UPSTREAM_DIR / "overrides" / "compose.migrator.yaml",
            OVERRIDES_DIR / "compose.cskh-common.yaml",
            OVERRIDES_DIR / "compose.cskh-tls.yaml",
        ],
        "expected_ports": [
            ("127.0.0.1", "8080", 80, "tcp"),
            ("127.0.0.1", "8443", 443, "tcp"),
        ],
    },
    "isolated-smoke": {
        "env_template": ENV_DIR / "isolated-smoke.env.example",
        "compose_files": [
            UPSTREAM_DIR / "compose.yaml",
            UPSTREAM_DIR / "overrides" / "compose.mariadb.yaml",
            UPSTREAM_DIR / "overrides" / "compose.mariadb-secrets.yaml",
            UPSTREAM_DIR / "overrides" / "compose.redis.yaml",
            UPSTREAM_DIR / "overrides" / "compose.proxy.yaml",
            UPSTREAM_DIR / "overrides" / "compose.migrator.yaml",
            OVERRIDES_DIR / "compose.cskh-common.yaml",
            OVERRIDES_DIR / "compose.cskh-isolated-smoke.yaml",
        ],
        "expected_ports": [
            ("127.0.0.1", "18080", 80, "tcp"),
        ],
    },
}


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def write_env_file(path: pathlib.Path, values: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def verify_upstream_provenance() -> dict:
    prov_path = UPSTREAM_DIR / "UPSTREAM-PROVENANCE.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    assert prov["pinned_tag"] == "v4.0.1"
    assert prov["pinned_commit_sha"] == "2b26e9ab8d751e5ea33bb7b930aea3550bb81ded"
    for entry in prov["files"]:
        fp = UPSTREAM_DIR / entry["path"]
        assert fp.is_file(), f"Missing upstream file: {fp}"
        raw = fp.read_bytes()
        actual_blob = git_blob_sha1(raw)
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        assert actual_blob == entry["git_blob_sha1"], (
            f"Upstream git blob drift on {entry['path']}: {actual_blob} != {entry['git_blob_sha1']}"
        )
        assert actual_sha256 == entry["sha256"], (
            f"Upstream sha256 drift on {entry['path']}: {actual_sha256} != {entry['sha256']}"
        )
    return {
        "pinned_tag": prov["pinned_tag"],
        "pinned_commit_sha": prov["pinned_commit_sha"],
        "verified_files": len(prov["files"]),
    }


def run_compose(
    compose_files: list[pathlib.Path],
    env_file: pathlib.Path,
    extra_args: list[str],
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for cf in compose_files:
        cmd.extend(["-f", str(cf)])
    cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


def verify_env_template(
    stack_name: str,
    env_path: pathlib.Path,
    require_real_app_digest: bool,
) -> dict[str, str]:
    env_map = parse_env_file(env_path)
    raw_text = env_path.read_text(encoding="utf-8")

    # Ensure zero inline plaintext password keys in env template
    forbidden_keys = {
        "DB_PASSWORD",
        "DB_ROOT_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "ADMIN_PASSWORD",
        "ENCRYPTION_KEY",
        "HASHED_PASSWORD",
    }
    present_forbidden = forbidden_keys.intersection(env_map.keys())
    assert not present_forbidden, (
        f"{stack_name}: forbidden inline secret keys in template: {sorted(present_forbidden)}"
    )
    assert "BEGIN RSA PRIVATE KEY" not in raw_text and "BEGIN OPENSSH PRIVATE KEY" not in raw_text

    for img_key in ("APP_IMAGE", "MARIADB_IMAGE", "REDIS_IMAGE", "PROXY_IMAGE"):
        val = env_map.get(img_key, "")
        assert DIGEST_RE.match(val), (
            f"{stack_name}: {img_key}='{val}' must be pinned by immutable @sha256:<64-hex>"
        )
        if img_key != "APP_IMAGE":
            assert PLACEHOLDER_DIGEST not in val, (
                f"{stack_name}: {img_key} must use verified infrastructure digest, not placeholder"
            )
        elif require_real_app_digest:
            assert PLACEHOLDER_DIGEST not in val, (
                f"{stack_name}: APP_IMAGE still contains sample placeholder digest {PLACEHOLDER_DIGEST}"
            )

    assert env_map.get("MIGRATE_SITES") == "false", (
        f"{stack_name}: MIGRATE_SITES must default to 'false'"
    )
    return env_map


def validate_stack(
    stack_name: str,
    spec: dict,
    tmp_dir: pathlib.Path,
    require_real_app_digest: bool,
) -> dict:
    env_map = verify_env_template(
        stack_name, spec["env_template"], require_real_app_digest=require_real_app_digest
    )

    # Create ephemeral dummy secret file outside repo so Compose secret file resolution succeeds
    secret_file = tmp_dir / f"{stack_name}_db_root_password.txt"
    secret_file.write_text(f"dummy-ephemeral-secret-{stack_name}\n", encoding="utf-8")
    env_copy = dict(env_map)
    env_copy["DB_PASSWORD_SECRETS_FILE"] = str(secret_file)
    temp_env = tmp_dir / f"{stack_name}.env"
    write_env_file(temp_env, env_copy)

    # 1. Default runtime services (`config --services`)
    res_srv = run_compose(spec["compose_files"], temp_env, ["config", "--services"])
    assert res_srv.returncode == 0, f"{stack_name} config --services failed: {res_srv.stderr}"
    assert not res_srv.stderr.strip(), (
        f"{stack_name} config --services emitted stderr warnings: {res_srv.stderr}"
    )
    services_default = [s.strip() for s in res_srv.stdout.splitlines() if s.strip()]
    assert sorted(services_default) == EXPECTED_DEFAULT_SERVICES, (
        f"{stack_name} unexpected default services: {sorted(services_default)}"
    )

    # 2. Migrate profile services (`--profile migrate config --services`)
    res_mig = run_compose(
        spec["compose_files"], temp_env, ["--profile", "migrate", "config", "--services"]
    )
    assert res_mig.returncode == 0, f"{stack_name} migrate config failed: {res_mig.stderr}"
    services_migrate = [s.strip() for s in res_mig.stdout.splitlines() if s.strip()]
    assert sorted(services_migrate) == sorted(EXPECTED_DEFAULT_SERVICES + ["migrator"]), (
        f"{stack_name} unexpected migrate services: {sorted(services_migrate)}"
    )

    # 3. Full rendered JSON inspection (`--profile migrate config --format json`)
    res_json = run_compose(
        spec["compose_files"], temp_env, ["--profile", "migrate", "config", "--format", "json"]
    )
    assert res_json.returncode == 0, f"{stack_name} config --format json failed: {res_json.stderr}"
    rendered = json.loads(res_json.stdout)

    project_name = rendered["name"]
    assert project_name == env_map["COMPOSE_PROJECT_NAME"]

    # Check networks
    nets = rendered.get("networks", {})
    assert set(nets.keys()) == {"edge_net", "app_net", "data_net"}, (
        f"{stack_name} unexpected networks: {set(nets.keys())}"
    )
    assert not nets["edge_net"].get("internal", False)
    assert nets["app_net"].get("internal") is True
    assert nets["data_net"].get("internal") is True

    # Check services
    srvs = rendered["services"]
    published_ports: dict[str, list[tuple[str, str, int, str]]] = {}
    for sname, sdef in srvs.items():
        img = sdef.get("image", "")
        assert DIGEST_RE.match(img), f"{stack_name}.{sname}: image '{img}' lacks @sha256 digest"

        s_nets = set(sdef.get("networks", {}).keys())
        assert "default" not in s_nets, f"{stack_name}.{sname} leaked onto default network"

        ports = sdef.get("ports") or []
        if ports:
            published_ports[sname] = [
                (
                    p.get("host_ip", "0.0.0.0") or "0.0.0.0",
                    str(p.get("published")),
                    int(p.get("target")),
                    p.get("protocol", "tcp"),
                )
                for p in ports
            ]

        # Secret least-privilege: ONLY `db` may mount `db_password`
        s_secrets = [sec["source"] for sec in (sdef.get("secrets") or [])]
        if sname == "db":
            assert s_secrets == ["db_password"], f"{stack_name}.db missing db_password secret"
            db_env = sdef.get("environment", {})
            assert not db_env.get("MYSQL_ROOT_PASSWORD"), (
                f"{stack_name}.db leaked plaintext MYSQL_ROOT_PASSWORD"
            )
            assert db_env.get("MYSQL_ROOT_PASSWORD_FILE") == "/run/secrets/db_password"
        else:
            assert not s_secrets, f"{stack_name}.{sname} unexpectedly mounts secrets: {s_secrets}"

        if sname in EXPECTED_LONG_RUNNING_SERVICES:
            hc = sdef.get("healthcheck")
            assert hc and hc.get("test"), f"{stack_name}.{sname} missing healthcheck"

    # Strict network topology assertions
    assert set(srvs["proxy"]["networks"].keys()) == {"edge_net"}
    assert env_map["SITE_NAME"] in (srvs["proxy"]["networks"]["edge_net"].get("aliases") or [])
    assert set(srvs["frontend"]["networks"].keys()) == {"edge_net", "app_net"}
    assert set(srvs["backend"]["networks"].keys()) == {"app_net", "data_net"}
    assert set(srvs["websocket"]["networks"].keys()) == {"edge_net", "app_net", "data_net"}
    for data_only in (
        "configurator",
        "migrator",
        "queue-short",
        "queue-long",
        "scheduler",
        "db",
        "redis-cache",
        "redis-queue",
    ):
        assert set(srvs[data_only]["networks"].keys()) == {"data_net"}, (
            f"{stack_name}.{data_only} must attach ONLY to data_net"
        )

    assert not srvs["proxy"].get("userns_mode"), f"{stack_name}.proxy must not force userns_mode"
    assert srvs["frontend"]["environment"]["FRAPPE_SITE_NAME_HEADER"] == env_map["SITE_NAME"]

    # Port exposure check: ONLY `proxy` may publish host ports!
    assert list(published_ports.keys()) == ["proxy"], (
        f"{stack_name}: ONLY proxy may publish ports, got: {published_ports}"
    )
    assert published_ports["proxy"] == spec["expected_ports"], (
        f"{stack_name}: proxy ports {published_ports['proxy']} != {spec['expected_ports']}"
    )

    return {
        "project_name": project_name,
        "site_name": env_map["SITE_NAME"],
        "services_default": services_default,
        "services_with_migrate_profile": services_migrate,
        "networks": {k: v["name"] for k, v in nets.items()},
        "volumes": {k: v["name"] for k, v in rendered.get("volumes", {}).items()},
        "secrets": {k: v["name"] for k, v in rendered.get("secrets", {}).items()},
        "published_ports": {
            k: [f"{ip}:{pub}->{tgt}/{proto}" for ip, pub, tgt, proto in v]
            for k, v in published_ports.items()
        },
    }


def verify_negative_fail_closed(tmp_dir: pathlib.Path) -> None:
    spec = STACK_PROFILES["production"]
    base_env = parse_env_file(spec["env_template"])
    secret_file = tmp_dir / "neg_secret.txt"
    secret_file.write_text("dummy\n", encoding="utf-8")

    # Case 1: Missing APP_IMAGE must fail docker compose config
    env_no_img = dict(base_env)
    env_no_img.pop("APP_IMAGE", None)
    env_no_img["DB_PASSWORD_SECRETS_FILE"] = str(secret_file)
    f1 = tmp_dir / "no_img.env"
    write_env_file(f1, env_no_img)
    r1 = run_compose(spec["compose_files"], f1, ["config", "--services"])
    assert r1.returncode != 0, "Expected Compose failure when APP_IMAGE is unset"

    # Case 2: Missing DB_PASSWORD_SECRETS_FILE must fail docker compose config
    env_no_sec = dict(base_env)
    env_no_sec.pop("DB_PASSWORD_SECRETS_FILE", None)
    f2 = tmp_dir / "no_sec.env"
    write_env_file(f2, env_no_sec)
    r2 = run_compose(spec["compose_files"], f2, ["config", "--services"])
    assert r2.returncode != 0, "Expected Compose failure when DB_PASSWORD_SECRETS_FILE is unset"

    # Case 3: Floating tag without @sha256 digest must fail verify_env_template
    env_float = dict(base_env)
    env_float["APP_IMAGE"] = "registry.example.invalid/crm-cskh:latest"
    f3 = tmp_dir / "floating.env"
    write_env_file(f3, env_float)
    failed_floating = False
    try:
        verify_env_template("neg-floating", f3, require_real_app_digest=False)
    except AssertionError:
        failed_floating = True
    assert failed_floating, "Expected digest gate failure on :latest floating tag"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-real-app-digest",
        action="store_true",
        help="Fail if APP_IMAGE still uses the sample 0000... placeholder digest.",
    )
    parser.add_argument("--json-out", type=pathlib.Path, default=None)
    args = parser.parse_args()

    prov_summary = verify_upstream_provenance()
    stack_results: dict[str, dict] = {}

    with tempfile.TemporaryDirectory(prefix="cskh-p4-compose-validate-") as td:
        tmp_dir = pathlib.Path(td)
        for sname, spec in STACK_PROFILES.items():
            stack_results[sname] = validate_stack(
                sname,
                spec,
                tmp_dir,
                require_real_app_digest=args.require_real_app_digest,
            )
        verify_negative_fail_closed(tmp_dir)

    # Verify pairwise disjoint isolation across all environments + Owner DEV stacks
    reserved_owner_projects = {"crm-cskh-dev", "crm-cskh-p1", "crm-cskh-p2", "crm-cskh-p3-ui"}
    seen_projects: set[str] = set()
    seen_sites: set[str] = set()
    seen_networks: set[str] = set()
    seen_volumes: set[str] = set()
    seen_secrets: set[str] = set()

    for sname, info in stack_results.items():
        proj = info["project_name"]
        assert proj not in reserved_owner_projects, f"{sname} collides with Owner stack {proj}"
        assert proj not in seen_projects, f"Duplicate project name: {proj}"
        seen_projects.add(proj)

        site = info["site_name"]
        assert site not in {"dev.localhost", "p3ui.localhost"}, f"{sname} collides with Owner site"
        assert site not in seen_sites, f"Duplicate site name: {site}"
        seen_sites.add(site)

        for n in info["networks"].values():
            assert n not in seen_networks, f"Shared network across environments: {n}"
            seen_networks.add(n)
        for v in info["volumes"].values():
            assert v not in seen_volumes, f"Shared volume across environments: {v}"
            seen_volumes.add(v)
        for sec in info["secrets"].values():
            assert sec not in seen_secrets, f"Shared secret across environments: {sec}"
            seen_secrets.add(sec)

    report = {
        "status": "PASS",
        "upstream_provenance": prov_summary,
        "stacks": stack_results,
        "negative_checks": [
            "missing_APP_IMAGE_rejected",
            "missing_DB_PASSWORD_SECRETS_FILE_rejected",
            "floating_tag_without_sha256_rejected",
        ],
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
