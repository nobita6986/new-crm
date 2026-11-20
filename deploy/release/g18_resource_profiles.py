"""Canonical G18 resource profiles, compose overrides, 9-vs-10 service mapping, and runtime cgroup/worker verifier.

Closes finding `F-G18-RUNNER-4X4-CGROUP-01`:
1. Single source of truth shared between `deploy/release/benchmark_p4_isolated.py`,
   `deploy/release/diagnose_g18_drilldown.py`, and Ops `deploy/ops/manage_isolated_staging_dr.py`
   so compose cgroup limits (`cpus`, `mem_limit`), Gunicorn `workers x threads`, MariaDB InnoDB
   parameters, and `config_spec_sha256` / `compose_env_overrides_sha256` never drift.
2. Collects `cgroup_container_inspection` and actual live worker/daemon counts across ALL 9
   `compose.verify.yaml` daemon services immediately before acceptance measurement.
3. Enforces fail-closed validation rejecting:
   - Missing or incomplete runtime configuration snapshots (`MISSING_RUNTIME_CONFIG_SNAPSHOT`),
   - Uncapped runtime containers (`cpus_limit == 0` / `memory_limit_mib == 0`) when profile is capped,
   - Any CPU or RAM limit mismatch vs declared service limits,
   - Any Gunicorn worker/thread count, RQ worker count (`queue-short=1`, `queue-long=1`), or
     service daemon mismatch.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import textwrap
import time
from datetime import datetime, timezone
from typing import Any

VERIFY_DAEMON_SERVICES: tuple[str, ...] = (
    "frontend",
    "backend",
    "websocket",
    "queue-short",
    "queue-long",
    "scheduler",
    "db",
    "redis-cache",
    "redis-queue",
)

STAGING_FULL_10_SERVICES: tuple[str, ...] = (
    *VERIFY_DAEMON_SERVICES,
    "proxy",
)

STAGING_CONFIG_ENVELOPE_KEYS: tuple[str, ...] = (
    "profile_id",
    "g18_s1_benchmark_capable",
    "backend_cpus",
    "backend_mem_mib",
    "gunicorn_workers",
    "gunicorn_threads",
    "db_cpus",
    "db_mem_mib",
    "queue_long_cpus",
    "queue_long_mem_mib",
    "queue_short_cpus",
    "queue_short_mem_mib",
    "frontend_cpus",
    "frontend_mem_mib",
    "websocket_cpus",
    "websocket_mem_mib",
    "scheduler_cpus",
    "scheduler_mem_mib",
    "proxy_cpus",
    "proxy_mem_mib",
    "redis_cache_cpus",
    "redis_cache_mem_mib",
    "redis_queue_cpus",
    "redis_queue_mem_mib",
    "total_capped_cpus",
    "total_capped_ram_mib",
)

SERVICE_ENVELOPE_FIELD_MAP: dict[str, tuple[str, str]] = {
    "frontend": ("frontend_cpus", "frontend_mem_mib"),
    "backend": ("backend_cpus", "backend_mem_mib"),
    "websocket": ("websocket_cpus", "websocket_mem_mib"),
    "queue-short": ("queue_short_cpus", "queue_short_mem_mib"),
    "queue-long": ("queue_long_cpus", "queue_long_mem_mib"),
    "scheduler": ("scheduler_cpus", "scheduler_mem_mib"),
    "db": ("db_cpus", "db_mem_mib"),
    "redis-cache": ("redis_cache_cpus", "redis_cache_mem_mib"),
    "redis-queue": ("redis_queue_cpus", "redis_queue_mem_mib"),
    "proxy": ("proxy_cpus", "proxy_mem_mib"),
}

EXPECTED_INNODB_BUFFER_POOL_BYTES = 2_684_354_560  # 2560 MiB
EXPECTED_INNODB_LOG_FILE_BYTES = 536_870_912  # 512 MiB
RUNTIME_PROBE_MARKER = "G18_RUNTIME_TOPOLOGY_JSON:"


RAW_G18_RESOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "staging_vps_capped": {
        "profile_id": "staging_vps_capped",
        "slo_spec_profile_column": "staging_vps_capped",
        "variant_id": "config_b_2_0vcpu_4x4",
        "description": (
            "Cgroup-capped shared Staging VPS S1 G18 configuration (Config B: backend 2.00 vCPU / "
            "2048 MiB, Gunicorn 4w x 4t = 16 web threads, MariaDB 2.00 vCPU / 3584 MiB with "
            "2560 MiB InnoDB buffer pool + 512 MiB redo log, dedicated 1x queue-long 1.25 vCPU / "
            "1536 MiB + 1x queue-short 0.50 vCPU / 2048 MiB)"
        ),
        "g03_g05_smoke_capable": True,
        "g18_s1_benchmark_capable": True,
        "require_live_headroom_precheck": True,
        "backend_cpus": 2.0,
        "backend_mem_mib": 2048.0,
        "gunicorn_workers": 4,
        "gunicorn_threads": 4,
        "db_cpus": 2.0,
        "db_mem_mib": 3584.0,
        "queue_long_cpus": 1.25,
        "queue_long_mem_mib": 1536.0,
        "queue_short_cpus": 0.5,
        "queue_short_mem_mib": 2048.0,
        "frontend_cpus": 0.35,
        "frontend_mem_mib": 256.0,
        "websocket_cpus": 0.25,
        "websocket_mem_mib": 256.0,
        "scheduler_cpus": 0.15,
        "scheduler_mem_mib": 256.0,
        "proxy_cpus": 0.2,
        "proxy_mem_mib": 192.0,
        "redis_cache_cpus": 0.15,
        "redis_cache_mem_mib": 256.0,
        "redis_cache_maxmemory": "128mb",
        "redis_queue_cpus": 0.15,
        "redis_queue_mem_mib": 256.0,
        "total_capped_cpus": 7.0,
        "total_capped_ram_mib": 10688.0,
        "host_safety_requirements": {
            "min_host_cpu_cores": 4,
            "min_mem_available_mib": 3072.0,
            "min_live_ram_reserve_mib": 1536.0,
            "max_load_per_cpu": 0.85,
            "min_free_disk_gib": 12.0,
        },
        "compose_env_overrides": {
            "PROXY_CPUS": "0.20",
            "PROXY_MEM_LIMIT": "192m",
            "FRONTEND_CPUS": "0.35",
            "FRONTEND_MEM_LIMIT": "256m",
            "BACKEND_CPUS": "2.00",
            "BACKEND_MEM_LIMIT": "2048m",
            "GUNICORN_WORKERS": "4",
            "GUNICORN_THREADS": "4",
            "GUNICORN_TIMEOUT": "120",
            "WEBSOCKET_CPUS": "0.25",
            "WEBSOCKET_MEM_LIMIT": "256m",
            "QUEUE_SHORT_CPUS": "0.50",
            "QUEUE_SHORT_MEM_LIMIT": "2048m",
            "QUEUE_SHORT_CPU_SHARES": "512",
            "QUEUE_LONG_CPUS": "1.25",
            "QUEUE_LONG_MEM_LIMIT": "1536m",
            "QUEUE_LONG_CPU_SHARES": "1024",
            "SCHEDULER_CPUS": "0.15",
            "SCHEDULER_MEM_LIMIT": "256m",
            "DB_CPUS": "2.00",
            "DB_MEM_LIMIT": "3584m",
            "DB_INNODB_BUFFER_POOL_SIZE": "2560M",
            "DB_INNODB_LOG_FILE_SIZE": "512M",
            "DB_INNODB_LOG_BUFFER_SIZE": "64M",
            "DB_INNODB_FLUSH_LOG_AT_TRX_COMMIT": "1",
            "DB_INNODB_FLUSH_METHOD": "O_DIRECT",
            "DB_INNODB_IO_CAPACITY": "1000",
            "DB_INNODB_IO_CAPACITY_MAX": "2000",
            "DB_MAX_CONNECTIONS": "200",
            "REDIS_CACHE_CPUS": "0.15",
            "REDIS_CACHE_MEM_LIMIT": "256m",
            "REDIS_CACHE_MAXMEMORY": "128mb",
            "REDIS_QUEUE_CPUS": "0.15",
            "REDIS_QUEUE_MEM_LIMIT": "256m",
        },
    },
    "s1_canonical_4x4": {
        "profile_id": "s1_canonical_4x4",
        "slo_spec_profile_column": "s1_canonical_4x4",
        "variant_id": "s1_canonical_4x4_capped",
        "description": "Full 4-vCPU / Gunicorn 4w x 4t (16 threads) isolated S1 benchmark configuration with enforced cgroups",
        "g03_g05_smoke_capable": True,
        "g18_s1_benchmark_capable": True,
        "require_live_headroom_precheck": False,
        "backend_cpus": 2.0,
        "backend_mem_mib": 2048.0,
        "gunicorn_workers": 4,
        "gunicorn_threads": 4,
        "db_cpus": 2.0,
        "db_mem_mib": 4096.0,
        "queue_long_cpus": 1.5,
        "queue_long_mem_mib": 2048.0,
        "queue_short_cpus": 1.0,
        "queue_short_mem_mib": 1024.0,
        "frontend_cpus": 1.0,
        "frontend_mem_mib": 512.0,
        "websocket_cpus": 0.5,
        "websocket_mem_mib": 512.0,
        "scheduler_cpus": 0.5,
        "scheduler_mem_mib": 512.0,
        "proxy_cpus": 0.5,
        "proxy_mem_mib": 256.0,
        "redis_cache_cpus": 0.5,
        "redis_cache_mem_mib": 512.0,
        "redis_cache_maxmemory": "256mb",
        "redis_queue_cpus": 0.5,
        "redis_queue_mem_mib": 512.0,
        "total_capped_cpus": 10.0,
        "total_capped_ram_mib": 12032.0,
        "host_safety_requirements": {
            "min_host_cpu_cores": 6,
            "min_mem_available_mib": 8192.0,
            "min_live_ram_reserve_mib": 2048.0,
            "max_load_per_cpu": 0.65,
            "min_free_disk_gib": 20.0,
        },
        "compose_env_overrides": {
            "PROXY_CPUS": "0.5",
            "PROXY_MEM_LIMIT": "256m",
            "FRONTEND_CPUS": "1.0",
            "FRONTEND_MEM_LIMIT": "512m",
            "BACKEND_CPUS": "2.0",
            "BACKEND_MEM_LIMIT": "2048m",
            "GUNICORN_WORKERS": "4",
            "GUNICORN_THREADS": "4",
            "GUNICORN_TIMEOUT": "120",
            "WEBSOCKET_CPUS": "0.5",
            "WEBSOCKET_MEM_LIMIT": "512m",
            "QUEUE_SHORT_CPUS": "1.0",
            "QUEUE_SHORT_MEM_LIMIT": "1024m",
            "QUEUE_SHORT_CPU_SHARES": "512",
            "QUEUE_LONG_CPUS": "1.5",
            "QUEUE_LONG_MEM_LIMIT": "2048m",
            "QUEUE_LONG_CPU_SHARES": "1024",
            "SCHEDULER_CPUS": "0.5",
            "SCHEDULER_MEM_LIMIT": "512m",
            "DB_CPUS": "2.0",
            "DB_MEM_LIMIT": "4096m",
            "DB_INNODB_BUFFER_POOL_SIZE": "2560M",
            "DB_INNODB_LOG_FILE_SIZE": "512M",
            "DB_INNODB_LOG_BUFFER_SIZE": "64M",
            "DB_INNODB_FLUSH_LOG_AT_TRX_COMMIT": "1",
            "DB_INNODB_FLUSH_METHOD": "O_DIRECT",
            "DB_INNODB_IO_CAPACITY": "1000",
            "DB_INNODB_IO_CAPACITY_MAX": "2000",
            "DB_MAX_CONNECTIONS": "200",
            "REDIS_CACHE_CPUS": "0.5",
            "REDIS_CACHE_MEM_LIMIT": "512m",
            "REDIS_CACHE_MAXMEMORY": "256mb",
            "REDIS_QUEUE_CPUS": "0.5",
            "REDIS_QUEUE_MEM_LIMIT": "512m",
        },
    },
    "staging_vps_capped_b_plus_2_5vcpu": {
        "profile_id": "staging_vps_capped",
        "slo_spec_profile_column": "staging_vps_capped",
        "variant_id": "config_b_plus_2_5vcpu_4x4",
        "description": (
            "Contingency cgroup-capped shared Staging VPS S1 G18 configuration (Config B+: "
            "backend 2.50 vCPU / 2048 MiB, Gunicorn 4w x 4t = 16 web threads, MariaDB 2.00 vCPU / "
            "3584 MiB, queue-long 1.25 vCPU / 1536 MiB, queue-short 0.50 vCPU / 2048 MiB)"
        ),
        "g03_g05_smoke_capable": True,
        "g18_s1_benchmark_capable": True,
        "require_live_headroom_precheck": True,
        "backend_cpus": 2.5,
        "backend_mem_mib": 2048.0,
        "gunicorn_workers": 4,
        "gunicorn_threads": 4,
        "db_cpus": 2.0,
        "db_mem_mib": 3584.0,
        "queue_long_cpus": 1.25,
        "queue_long_mem_mib": 1536.0,
        "queue_short_cpus": 0.5,
        "queue_short_mem_mib": 2048.0,
        "frontend_cpus": 0.35,
        "frontend_mem_mib": 256.0,
        "websocket_cpus": 0.25,
        "websocket_mem_mib": 256.0,
        "scheduler_cpus": 0.15,
        "scheduler_mem_mib": 256.0,
        "proxy_cpus": 0.2,
        "proxy_mem_mib": 192.0,
        "redis_cache_cpus": 0.15,
        "redis_cache_mem_mib": 256.0,
        "redis_cache_maxmemory": "128mb",
        "redis_queue_cpus": 0.15,
        "redis_queue_mem_mib": 256.0,
        "total_capped_cpus": 7.5,
        "total_capped_ram_mib": 10688.0,
        "host_safety_requirements": {
            "min_host_cpu_cores": 6,
            "min_mem_available_mib": 3072.0,
            "min_live_ram_reserve_mib": 1536.0,
            "max_load_per_cpu": 0.85,
            "min_free_disk_gib": 12.0,
        },
        "compose_env_overrides": {
            "PROXY_CPUS": "0.20",
            "PROXY_MEM_LIMIT": "192m",
            "FRONTEND_CPUS": "0.35",
            "FRONTEND_MEM_LIMIT": "256m",
            "BACKEND_CPUS": "2.50",
            "BACKEND_MEM_LIMIT": "2048m",
            "GUNICORN_WORKERS": "4",
            "GUNICORN_THREADS": "4",
            "GUNICORN_TIMEOUT": "120",
            "WEBSOCKET_CPUS": "0.25",
            "WEBSOCKET_MEM_LIMIT": "256m",
            "QUEUE_SHORT_CPUS": "0.50",
            "QUEUE_SHORT_MEM_LIMIT": "2048m",
            "QUEUE_SHORT_CPU_SHARES": "512",
            "QUEUE_LONG_CPUS": "1.25",
            "QUEUE_LONG_MEM_LIMIT": "1536m",
            "QUEUE_LONG_CPU_SHARES": "1024",
            "SCHEDULER_CPUS": "0.15",
            "SCHEDULER_MEM_LIMIT": "256m",
            "DB_CPUS": "2.00",
            "DB_MEM_LIMIT": "3584m",
            "DB_INNODB_BUFFER_POOL_SIZE": "2560M",
            "DB_INNODB_LOG_FILE_SIZE": "512M",
            "DB_INNODB_LOG_BUFFER_SIZE": "64M",
            "DB_INNODB_FLUSH_LOG_AT_TRX_COMMIT": "1",
            "DB_INNODB_FLUSH_METHOD": "O_DIRECT",
            "DB_INNODB_IO_CAPACITY": "1000",
            "DB_INNODB_IO_CAPACITY_MAX": "2000",
            "DB_MAX_CONNECTIONS": "200",
            "REDIS_CACHE_CPUS": "0.15",
            "REDIS_CACHE_MEM_LIMIT": "256m",
            "REDIS_CACHE_MAXMEMORY": "128mb",
            "REDIS_QUEUE_CPUS": "0.15",
            "REDIS_QUEUE_MEM_LIMIT": "256m",
        },
    },
    "staging_vps_capped_legacy_2x4": {
        "profile_id": "staging_vps_capped",
        "slo_spec_profile_column": "staging_vps_capped",
        "variant_id": "config_a_legacy_1_5vcpu_2x4",
        "description": (
            "Legacy Config A control baseline (backend 1.50 vCPU / 1536 MiB, Gunicorn 2w x 4t = 8 threads)"
        ),
        "g03_g05_smoke_capable": True,
        "g18_s1_benchmark_capable": True,
        "require_live_headroom_precheck": True,
        "backend_cpus": 1.5,
        "backend_mem_mib": 1536.0,
        "gunicorn_workers": 2,
        "gunicorn_threads": 4,
        "db_cpus": 2.0,
        "db_mem_mib": 3584.0,
        "queue_long_cpus": 1.25,
        "queue_long_mem_mib": 1536.0,
        "queue_short_cpus": 0.5,
        "queue_short_mem_mib": 2048.0,
        "frontend_cpus": 0.35,
        "frontend_mem_mib": 256.0,
        "websocket_cpus": 0.25,
        "websocket_mem_mib": 256.0,
        "scheduler_cpus": 0.15,
        "scheduler_mem_mib": 256.0,
        "proxy_cpus": 0.2,
        "proxy_mem_mib": 192.0,
        "redis_cache_cpus": 0.15,
        "redis_cache_mem_mib": 256.0,
        "redis_cache_maxmemory": "128mb",
        "redis_queue_cpus": 0.15,
        "redis_queue_mem_mib": 256.0,
        "total_capped_cpus": 6.5,
        "total_capped_ram_mib": 10176.0,
        "host_safety_requirements": {
            "min_host_cpu_cores": 4,
            "min_mem_available_mib": 3072.0,
            "min_live_ram_reserve_mib": 1536.0,
            "max_load_per_cpu": 0.85,
            "min_free_disk_gib": 12.0,
        },
        "compose_env_overrides": {
            "PROXY_CPUS": "0.20",
            "PROXY_MEM_LIMIT": "192m",
            "FRONTEND_CPUS": "0.35",
            "FRONTEND_MEM_LIMIT": "256m",
            "BACKEND_CPUS": "1.50",
            "BACKEND_MEM_LIMIT": "1536m",
            "GUNICORN_WORKERS": "2",
            "GUNICORN_THREADS": "4",
            "GUNICORN_TIMEOUT": "120",
            "WEBSOCKET_CPUS": "0.25",
            "WEBSOCKET_MEM_LIMIT": "256m",
            "QUEUE_SHORT_CPUS": "0.50",
            "QUEUE_SHORT_MEM_LIMIT": "2048m",
            "QUEUE_SHORT_CPU_SHARES": "512",
            "QUEUE_LONG_CPUS": "1.25",
            "QUEUE_LONG_MEM_LIMIT": "1536m",
            "QUEUE_LONG_CPU_SHARES": "1024",
            "SCHEDULER_CPUS": "0.15",
            "SCHEDULER_MEM_LIMIT": "256m",
            "DB_CPUS": "2.00",
            "DB_MEM_LIMIT": "3584m",
            "DB_INNODB_BUFFER_POOL_SIZE": "2560M",
            "DB_INNODB_LOG_FILE_SIZE": "512M",
            "DB_INNODB_LOG_BUFFER_SIZE": "64M",
            "DB_INNODB_FLUSH_LOG_AT_TRX_COMMIT": "1",
            "DB_INNODB_FLUSH_METHOD": "O_DIRECT",
            "DB_INNODB_IO_CAPACITY": "1000",
            "DB_INNODB_IO_CAPACITY_MAX": "2000",
            "DB_MAX_CONNECTIONS": "200",
            "REDIS_CACHE_CPUS": "0.15",
            "REDIS_CACHE_MEM_LIMIT": "256m",
            "REDIS_CACHE_MAXMEMORY": "128mb",
            "REDIS_QUEUE_CPUS": "0.15",
            "REDIS_QUEUE_MEM_LIMIT": "256m",
        },
    },
}


class RuntimeConfigVerificationError(RuntimeError):
    """Fail-closed error raised when runtime cgroup limits, RAM, workers, or snapshots violate spec."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.reason = message
        self.details = details or {}


def _canonical_sha256(obj: dict[str, Any]) -> str:
    canon = (json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    return hashlib.sha256(canon).hexdigest()


def get_g18_resource_profile_spec(profile_key: str) -> dict[str, Any]:
    """Return full canonical 10-service & 9-service verify envelopes, hashes, and service limits."""
    clean = str(profile_key or "").strip()
    if clean not in RAW_G18_RESOURCE_PROFILES:
        raise ValueError(
            f"Unsupported G18 resource profile {profile_key!r}; "
            f"expected one of {sorted(RAW_G18_RESOURCE_PROFILES)}"
        )
    raw = RAW_G18_RESOURCE_PROFILES[clean]
    env_10 = {k: raw[k] for k in STAGING_CONFIG_ENVELOPE_KEYS}
    config_spec_sha256 = _canonical_sha256(env_10)
    ov_10 = dict(raw["compose_env_overrides"])
    compose_env_overrides_sha256 = _canonical_sha256(ov_10)

    verify_cpus_sum = round(
        sum(
            float(raw[SERVICE_ENVELOPE_FIELD_MAP[s][0]]) for s in VERIFY_DAEMON_SERVICES
        ),
        3,
    )
    verify_ram_sum = round(
        sum(
            float(raw[SERVICE_ENVELOPE_FIELD_MAP[s][1]]) for s in VERIFY_DAEMON_SERVICES
        ),
        1,
    )
    env_9 = {
        **env_10,
        "proxy_cpus": 0.0,
        "proxy_mem_mib": 0.0,
        "total_capped_cpus": verify_cpus_sum,
        "total_capped_ram_mib": verify_ram_sum,
    }
    verify_9_sha256 = _canonical_sha256(env_9)
    ov_9 = {k: v for k, v in ov_10.items() if not k.startswith("PROXY_")}
    verify_9_ov_sha256 = _canonical_sha256(ov_9)

    service_limits_9 = {
        svc: {
            "cpus": float(raw[SERVICE_ENVELOPE_FIELD_MAP[svc][0]]),
            "memory_limit_mib": float(raw[SERVICE_ENVELOPE_FIELD_MAP[svc][1]]),
        }
        for svc in VERIFY_DAEMON_SERVICES
    }
    override_yaml = render_verify_compose_override_yaml(clean)
    override_yaml_sha256 = hashlib.sha256(
        override_yaml.replace("\r\n", "\n").encode("utf-8")
    ).hexdigest()

    return {
        **env_10,
        "lookup_key": clean,
        "variant_id": raw["variant_id"],
        "slo_spec_profile_column": raw["slo_spec_profile_column"],
        "description": raw["description"],
        "g03_g05_smoke_capable": bool(raw["g03_g05_smoke_capable"]),
        "require_live_headroom_precheck": bool(raw["require_live_headroom_precheck"]),
        "config_spec_sha256": config_spec_sha256,
        "compose_env_overrides_sha256": compose_env_overrides_sha256,
        "verify_9_service_config_spec_sha256": verify_9_sha256,
        "verify_9_service_compose_env_overrides_sha256": verify_9_ov_sha256,
        "verify_9_service_envelope": env_9,
        "verify_9_service_limits": service_limits_9,
        "verify_compose_override_yaml": override_yaml,
        "verify_compose_override_yaml_sha256": override_yaml_sha256,
        "host_safety_requirements": dict(raw["host_safety_requirements"]),
        "compose_env_overrides": ov_10,
        "verify_9_to_staging_10_mapping": build_verify_9_to_staging_10_mapping(clean),
    }


def build_verify_9_to_staging_10_mapping(profile_key: str) -> dict[str, Any]:
    """Explain exact structural & resource reconciliation between 9-service verify and 10-service Staging."""
    raw = RAW_G18_RESOURCE_PROFILES[profile_key]
    per_service: dict[str, dict[str, Any]] = {}
    for svc in VERIFY_DAEMON_SERVICES:
        cpu_k, mem_k = SERVICE_ENVELOPE_FIELD_MAP[svc]
        per_service[svc] = {
            "present_in_compose_verify_9": True,
            "present_in_staging_full_10": True,
            "declared_cpus": float(raw[cpu_k]),
            "declared_memory_mib": float(raw[mem_k]),
            "parity": "IDENTICAL_1_TO_1",
        }
    proxy_cpus = float(raw["proxy_cpus"])
    proxy_mem = float(raw["proxy_mem_mib"])
    per_service["proxy"] = {
        "present_in_compose_verify_9": False,
        "present_in_staging_full_10": True,
        "declared_cpus_staging_10": proxy_cpus,
        "declared_memory_mib_staging_10": proxy_mem,
        "declared_cpus_verify_9": 0.0,
        "declared_memory_mib_verify_9": 0.0,
        "parity": "EXCLUDED_IN_LOOPBACK_VERIFY_STACK",
        "rationale": (
            "`deploy/images/compose.verify.yaml` publishes `frontend` (nginx) directly on "
            "`127.0.0.1:${VERIFY_HTTP_PORT}:8080` with zero public TLS ingress, whereas the "
            "10-service `deploy/compose/` Staging stack adds `proxy` (Traefik/Caddy TLS edge). "
            "All 9 application, worker, cache, and database containers have 100% identical "
            "cgroup CPU, RAM, Gunicorn, RQ, and MariaDB limits across both stacks."
        ),
    }
    v9_cpus = round(
        sum(
            float(raw[SERVICE_ENVELOPE_FIELD_MAP[s][0]]) for s in VERIFY_DAEMON_SERVICES
        ),
        3,
    )
    v9_mem = round(
        sum(
            float(raw[SERVICE_ENVELOPE_FIELD_MAP[s][1]]) for s in VERIFY_DAEMON_SERVICES
        ),
        1,
    )
    return {
        "verify_stack_compose_file": "deploy/images/compose.verify.yaml",
        "verify_daemon_service_count": 9,
        "verify_one_shot_init_services": ["configurator"],
        "staging_full_daemon_service_count": 10,
        "staging_extra_edge_service": "proxy",
        "verify_9_total_capped_cpus": v9_cpus,
        "verify_9_total_capped_ram_mib": v9_mem,
        "staging_10_total_capped_cpus": float(raw["total_capped_cpus"]),
        "staging_10_total_capped_ram_mib": float(raw["total_capped_ram_mib"]),
        "delta_cpus_staging_minus_verify": round(
            float(raw["total_capped_cpus"]) - v9_cpus, 3
        ),
        "delta_ram_mib_staging_minus_verify": round(
            float(raw["total_capped_ram_mib"]) - v9_mem, 1
        ),
        "delta_equals_proxy_budget_exactly": (
            abs((float(raw["total_capped_cpus"]) - v9_cpus) - proxy_cpus) < 1e-6
            and abs((float(raw["total_capped_ram_mib"]) - v9_mem) - proxy_mem) < 1e-6
        ),
        "services": per_service,
    }


def render_verify_compose_override_yaml(profile_key: str) -> str:
    """Deterministically generate `compose.bench-override.yaml` with explicit cgroups on all 9 services."""
    if profile_key not in RAW_G18_RESOURCE_PROFILES:
        raise ValueError(f"Unsupported profile_key={profile_key!r}")
    raw = RAW_G18_RESOURCE_PROFILES[profile_key]
    redis_maxmem = str(raw.get("redis_cache_maxmemory") or "128mb")
    return textwrap.dedent(
        f"""\
        services:
          frontend:
            cpus: {float(raw["frontend_cpus"]):.2f}
            mem_limit: {int(raw["frontend_mem_mib"])}m
          backend:
            cpus: {float(raw["backend_cpus"]):.2f}
            mem_limit: {int(raw["backend_mem_mib"])}m
            cpu_shares: 1024
            environment:
              GUNICORN_WORKERS: "{int(raw["gunicorn_workers"])}"
              GUNICORN_THREADS: "{int(raw["gunicorn_threads"])}"
              GUNICORN_TIMEOUT: "120"
              TZ: Asia/Ho_Chi_Minh
          websocket:
            cpus: {float(raw["websocket_cpus"]):.2f}
            mem_limit: {int(raw["websocket_mem_mib"])}m
          queue-short:
            cpus: {float(raw["queue_short_cpus"]):.2f}
            mem_limit: {int(raw["queue_short_mem_mib"])}m
            cpu_shares: 512
            command: ["bench", "worker", "--queue", "short,default"]
          queue-long:
            image: ${{APP_IMAGE:?set APP_IMAGE}}
            pull_policy: ${{PULL_POLICY:-never}}
            restart: "no"
            cpus: {float(raw["queue_long_cpus"]):.2f}
            mem_limit: {int(raw["queue_long_mem_mib"])}m
            cpu_shares: 1024
            security_opt:
              - no-new-privileges:true
            command: ["bench", "worker", "--queue", "long"]
            environment:
              TZ: Asia/Ho_Chi_Minh
            volumes:
              - sites:/home/frappe/frappe-bench/sites
            depends_on:
              configurator:
                condition: service_completed_successfully
            networks:
              - data_net
          scheduler:
            cpus: {float(raw["scheduler_cpus"]):.2f}
            mem_limit: {int(raw["scheduler_mem_mib"])}m
          db:
            cpus: {float(raw["db_cpus"]):.2f}
            mem_limit: {int(raw["db_mem_mib"])}m
            command:
              - --character-set-server=utf8mb4
              - --collation-server=utf8mb4_unicode_ci
              - --skip-character-set-client-handshake
              - --innodb-buffer-pool-size=2560M
              - --innodb-log-file-size=512M
              - --innodb-log-buffer-size=64M
              - --innodb-flush-log-at-trx-commit=1
              - --innodb-flush-method=O_DIRECT
              - --innodb-io-capacity=1000
              - --innodb-io-capacity-max=2000
              - --max-connections=200
          redis-cache:
            cpus: {float(raw["redis_cache_cpus"]):.2f}
            mem_limit: {int(raw["redis_cache_mem_mib"])}m
            command:
              - redis-server
              - --save
              - ""
              - --appendonly
              - "no"
              - --maxmemory
              - {redis_maxmem}
              - --maxmemory-policy
              - allkeys-lru
          redis-queue:
            cpus: {float(raw["redis_queue_cpus"]):.2f}
            mem_limit: {int(raw["redis_queue_mem_mib"])}m
        """
    )


def export_ops_staging_resource_profiles() -> dict[str, dict[str, Any]]:
    """Export canonical dict compatible with `manage_isolated_staging_dr.STAGING_VPS_RESOURCE_PROFILES`."""
    exported: dict[str, dict[str, Any]] = {}
    for pid in ("s1_canonical_4x4", "staging_vps_capped"):
        raw = RAW_G18_RESOURCE_PROFILES[pid]
        exported[pid] = {
            k: (dict(v) if isinstance(v, dict) else v)
            for k, v in raw.items()
            if k
            not in ("slo_spec_profile_column", "variant_id", "redis_cache_maxmemory")
        }
    return exported


PRE_ACCEPTANCE_BACKEND_DB_RQ_PROBE_PY = textwrap.dedent(
    """\
    import json
    import os
    from pathlib import Path
    import frappe
    from rq import Worker
    from frappe.utils.background_jobs import get_redis_conn

    site_name = os.environ["BENCH_SITE"]

    cpu_max_raw = ""
    mem_max_raw = ""
    for p, attr in (("/sys/fs/cgroup/cpu.max", "cpu"), ("/sys/fs/cgroup/memory.max", "mem")):
        fp = Path(p)
        if fp.exists():
            val = fp.read_text(encoding="utf-8", errors="ignore").strip()
            if attr == "cpu":
                cpu_max_raw = val
            else:
                mem_max_raw = val

    cgroup_cpus = None
    if cpu_max_raw:
        parts = cpu_max_raw.split()
        if len(parts) == 2 and parts[0] != "max" and parts[0].isdigit() and parts[1].isdigit():
            period = int(parts[1])
            if period > 0:
                cgroup_cpus = round(int(parts[0]) / float(period), 3)
        elif parts and parts[0] == "max":
            cgroup_cpus = 0.0

    cgroup_mem_mib = None
    if mem_max_raw:
        if mem_max_raw == "max":
            cgroup_mem_mib = 0.0
        elif mem_max_raw.isdigit():
            cgroup_mem_mib = round(int(mem_max_raw) / (1024.0 * 1024.0), 1)

    master_pid = None
    worker_pids = []
    worker_thread_counts = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmd_raw = (entry / "cmdline").read_bytes()
            if not cmd_raw:
                continue
            args = [x.decode("utf-8", errors="ignore") for x in cmd_raw.split(b"\\x00") if x]
            joined = " ".join(args)
            if "gunicorn" not in joined or "frappe.app:application" not in joined:
                continue
            stat_txt = (entry / "stat").read_text(encoding="utf-8", errors="ignore")
            rparen = stat_txt.rfind(")")
            ppid = int(stat_txt[rparen + 2 :].split()[1]) if rparen != -1 else -1
            if pid == 1 or ppid == 0:
                master_pid = pid
            elif ppid == 1:
                worker_pids.append(pid)
                status_txt = (entry / "status").read_text(encoding="utf-8", errors="ignore")
                for sln in status_txt.splitlines():
                    if sln.startswith("Threads:"):
                        worker_thread_counts.append(int(sln.split()[1]))
                        break
        except Exception:
            continue

    os.chdir("/home/frappe/frappe-bench/sites")
    frappe.init(site=site_name, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        var_rows = frappe.db.sql(
            "SHOW VARIABLES WHERE Variable_name IN ("
            "'innodb_buffer_pool_size','innodb_log_file_size','innodb_log_buffer_size',"
            "'innodb_flush_log_at_trx_commit','innodb_flush_method','max_connections')"
        )
        mariadb_vars = {str(r[0]): str(r[1]) for r in var_rows}

        conn = get_redis_conn()
        rq_workers = []
        for w in Worker.all(connection=conn):
            raw_qs = [q.name for q in w.queues]
            norm_qs = [q.rsplit(":", 1)[-1] for q in raw_qs]
            rq_workers.append(
                {
                    "name": w.name,
                    "state": w.get_state(),
                    "queues": raw_qs,
                    "normalized_queues": norm_qs,
                }
            )
    finally:
        frappe.destroy()

    env_w = os.environ.get("GUNICORN_WORKERS", "")
    env_t = os.environ.get("GUNICORN_THREADS", "")
    payload = {
        "backend_runtime": {
            "env_gunicorn_workers": int(env_w) if env_w.isdigit() else 0,
            "env_gunicorn_threads": int(env_t) if env_t.isdigit() else 0,
            "env_gunicorn_timeout": os.environ.get("GUNICORN_TIMEOUT", ""),
            "cgroup_cpu_max_raw": cpu_max_raw,
            "cgroup_cpus": cgroup_cpus,
            "cgroup_memory_max_raw": mem_max_raw,
            "cgroup_mem_mib": cgroup_mem_mib,
            "gunicorn_master_pid": master_pid,
            "gunicorn_worker_pids": sorted(worker_pids),
            "live_gunicorn_worker_count": len(worker_pids),
            "os_threads_per_worker": worker_thread_counts,
            "effective_total_web_threads": (
                len(worker_pids) * (int(env_t) if env_t.isdigit() else 0)
            ),
        },
        "mariadb_variables": mariadb_vars,
        "rq_workers": rq_workers,
    }
    print("G18_RUNTIME_TOPOLOGY_JSON:" + json.dumps(payload))
    """
)

SERVICE_PROCESS_MATCHERS: dict[str, tuple[str, ...]] = {
    "frontend": ("nginx: worker process", "nginx: master process"),
    "websocket": ("socketio.js",),
    "queue-short": ("bench worker", "rq.worker", "frappe.utils.bench_helper"),
    "queue-long": ("bench worker", "rq.worker", "frappe.utils.bench_helper"),
    "scheduler": ("bench schedule", "frappe.utils.bench_helper"),
    "db": ("mariadbd", "mysqld"),
    "redis-cache": ("redis-server",),
    "redis-queue": ("redis-server",),
}


def _count_service_processes_in_container(
    cid: str,
    service: str,
    compose_env: dict[str, str],
) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            cid,
            "sh",
            "-c",
            'for p in /proc/[0-9]*/cmdline; do tr "\\0" " " < "$p" 2>/dev/null; echo; done',
        ],
        env=compose_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        return {"probe_ok": False, "live_process_count": 0, "matched_cmdlines": []}
    tokens = SERVICE_PROCESS_MATCHERS.get(service, ())
    matched: list[str] = []
    nginx_workers = 0
    for raw_ln in (proc.stdout or "").splitlines():
        ln = raw_ln.strip()
        if not ln or "for p in /proc/" in ln:
            continue
        if any(tok in ln for tok in tokens):
            matched.append(ln[:160])
            if service == "frontend" and "nginx: worker process" in ln:
                nginx_workers += 1
    return {
        "probe_ok": True,
        "live_process_count": len(matched),
        "nginx_worker_processes": nginx_workers if service == "frontend" else None,
        "matched_cmdlines": matched[:10],
    }


def collect_pre_acceptance_runtime_snapshot(
    *,
    compose_base: list[str],
    compose_env: dict[str, str],
    site_name: str,
    profile_key: str,
    worker_registration_wait_sec: float = 10.0,
) -> dict[str, Any]:
    """Collect cgroup limits + actual worker/daemon counts across ALL 9 `compose.verify` services."""
    profile_spec = get_g18_resource_profile_spec(profile_key)
    exp_gw = int(profile_spec["gunicorn_workers"])
    cgroup_inspection: dict[str, dict[str, Any]] = {}
    cids: dict[str, str] = {}

    for svc in VERIFY_DAEMON_SERVICES:
        ps_proc = subprocess.run(
            [*compose_base, "ps", "-q", svc],
            env=compose_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        cid = (
            ps_proc.stdout.strip().splitlines()[0].strip()
            if ps_proc.stdout.strip()
            else ""
        )
        if not cid:
            continue
        cids[svc] = cid
        insp_proc = subprocess.run(
            ["docker", "inspect", cid],
            env=compose_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if insp_proc.returncode != 0 or not insp_proc.stdout.strip():
            continue
        obj = json.loads(insp_proc.stdout)[0]
        hc = obj.get("HostConfig") or {}
        st = obj.get("State") or {}
        cfg = obj.get("Config") or {}
        nano_cpus = int(hc.get("NanoCpus") or 0)
        mem_bytes = int(hc.get("Memory") or 0)
        cgroup_inspection[svc] = {
            "container_id": str(obj.get("Id") or ""),
            "container_name": str(obj.get("Name") or "").lstrip("/"),
            "running": bool(st.get("Running")),
            "cpus_limit": round(nano_cpus / 1_000_000_000.0, 3)
            if nano_cpus > 0
            else 0.0,
            "memory_limit_mib": round(mem_bytes / (1024.0 * 1024.0), 1)
            if mem_bytes > 0
            else 0.0,
            "cpu_shares": int(hc.get("CpuShares") or 0),
            "oom_killed": bool(st.get("OOMKilled")),
            "restart_count": int(obj.get("RestartCount") or 0),
            "cmd": cfg.get("Cmd") or [],
        }

    backend_db_rq: dict[str, Any] = {}
    if "backend" in cids:
        deadline = time.monotonic() + max(0.0, float(worker_registration_wait_sec))
        while True:
            probe_proc = subprocess.run(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    f"BENCH_SITE={site_name}",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-",
                ],
                input=PRE_ACCEPTANCE_BACKEND_DB_RQ_PROBE_PY,
                env=compose_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if probe_proc.returncode == 0:
                markers = [
                    ln[len(RUNTIME_PROBE_MARKER) :]
                    for ln in (probe_proc.stdout or "").splitlines()
                    if ln.startswith(RUNTIME_PROBE_MARKER)
                ]
                if markers:
                    backend_db_rq = json.loads(markers[-1])
            rq_list = list(backend_db_rq.get("rq_workers") or [])
            be_rt = backend_db_rq.get("backend_runtime") or {}
            if (
                len(rq_list) >= 2
                and int(be_rt.get("live_gunicorn_worker_count") or 0) == exp_gw
            ) or time.monotonic() >= deadline:
                break
            time.sleep(1.0)

    rq_workers = list(backend_db_rq.get("rq_workers") or [])
    short_rq = [
        w
        for w in rq_workers
        if isinstance(w, dict) and w.get("normalized_queues") == ["short", "default"]
    ]
    long_rq = [
        w
        for w in rq_workers
        if isinstance(w, dict) and w.get("normalized_queues") == ["long"]
    ]

    service_worker_inspection: dict[str, dict[str, Any]] = {}
    if "backend_runtime" in backend_db_rq:
        be_rt = backend_db_rq["backend_runtime"]
        service_worker_inspection["backend"] = {
            "worker_model": "gunicorn_gthread",
            **be_rt,
        }

    for svc in VERIFY_DAEMON_SERVICES:
        if svc == "backend" or svc not in cids:
            continue
        p_info = _count_service_processes_in_container(cids[svc], svc, compose_env)
        entry: dict[str, Any] = {
            "worker_model": "rq_worker"
            if svc in ("queue-short", "queue-long")
            else "daemon",
            **p_info,
        }
        if svc == "queue-short":
            entry["registered_rq_worker_count"] = len(short_rq)
            entry["registered_rq_workers"] = short_rq
        elif svc == "queue-long":
            entry["registered_rq_worker_count"] = len(long_rq)
            entry["registered_rq_workers"] = long_rq
        service_worker_inspection[svc] = entry

    return {
        "schema_version": "cskh-p4-pre-acceptance-runtime-snapshot-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "profile_key": profile_key,
        "profile_id": profile_spec["profile_id"],
        "slo_spec_profile_column": profile_spec["slo_spec_profile_column"],
        "config_spec_sha256": profile_spec["config_spec_sha256"],
        "compose_env_overrides_sha256": profile_spec["compose_env_overrides_sha256"],
        "verify_9_service_config_spec_sha256": profile_spec[
            "verify_9_service_config_spec_sha256"
        ],
        "declared_profile_envelope": {
            k: profile_spec[k] for k in STAGING_CONFIG_ENVELOPE_KEYS
        },
        "verify_9_service_envelope": profile_spec["verify_9_service_envelope"],
        "verify_9_to_staging_10_mapping": profile_spec[
            "verify_9_to_staging_10_mapping"
        ],
        "cgroup_container_inspection": cgroup_inspection,
        "service_worker_inspection": service_worker_inspection,
        "mariadb_variables": backend_db_rq.get("mariadb_variables") or {},
        "rq_workers": rq_workers,
    }


def verify_declared_vs_runtime_config_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    profile_key: str,
) -> dict[str, Any]:
    """Fail-closed check of runtime cgroups (`cpus_limit`, `memory_limit_mib`), workers & DB vars."""
    spec = get_g18_resource_profile_spec(profile_key)

    if not isinstance(snapshot, dict) or not snapshot:
        raise RuntimeConfigVerificationError(
            "MISSING_RUNTIME_CONFIG_SNAPSHOT",
            "Pre-acceptance runtime configuration snapshot is missing or empty",
        )

    cgroups = snapshot.get("cgroup_container_inspection")
    workers = snapshot.get("service_worker_inspection")
    mariadb_vars = snapshot.get("mariadb_variables")

    if not isinstance(cgroups, dict) or not cgroups:
        raise RuntimeConfigVerificationError(
            "MISSING_RUNTIME_CONFIG_SNAPSHOT",
            "Runtime snapshot `cgroup_container_inspection` is missing or empty `{}`",
        )
    if not isinstance(workers, dict) or not workers:
        raise RuntimeConfigVerificationError(
            "MISSING_RUNTIME_CONFIG_SNAPSHOT",
            "Runtime snapshot `service_worker_inspection` is missing or empty `{}`",
        )
    if not isinstance(mariadb_vars, dict) or not mariadb_vars:
        raise RuntimeConfigVerificationError(
            "MISSING_RUNTIME_CONFIG_SNAPSHOT",
            "Runtime snapshot `mariadb_variables` is missing or empty `{}`",
        )

    missing_cgroups = [s for s in VERIFY_DAEMON_SERVICES if s not in cgroups]
    if missing_cgroups:
        raise RuntimeConfigVerificationError(
            "MISSING_RUNTIME_CONFIG_SNAPSHOT",
            f"Missing cgroup inspection for required verify service(s): {missing_cgroups}",
            details={"missing_services": missing_cgroups},
        )
    missing_workers = [s for s in VERIFY_DAEMON_SERVICES if s not in workers]
    if missing_workers:
        raise RuntimeConfigVerificationError(
            "MISSING_RUNTIME_CONFIG_SNAPSHOT",
            f"Missing worker inspection for required verify service(s): {missing_workers}",
            details={"missing_services": missing_workers},
        )

    declared_9 = spec["verify_9_service_limits"]
    actual_cpus_total = 0.0
    actual_mem_total = 0.0

    for svc in VERIFY_DAEMON_SERVICES:
        cg = cgroups[svc]
        exp_cpu = float(declared_9[svc]["cpus"])
        exp_mem = float(declared_9[svc]["memory_limit_mib"])
        act_cpu = float(cg.get("cpus_limit") or 0.0)
        act_mem = float(cg.get("memory_limit_mib") or 0.0)

        if cg.get("running") is False:
            raise RuntimeConfigVerificationError(
                "RUNTIME_SERVICE_NOT_RUNNING",
                f"Service {svc!r} container is not running before acceptance",
                details={"service": svc, "cgroup": cg},
            )
        if bool(cg.get("oom_killed")):
            raise RuntimeConfigVerificationError(
                "RUNTIME_SERVICE_OOM_KILLED",
                f"Service {svc!r} recorded OOMKilled=True before acceptance",
                details={"service": svc, "cgroup": cg},
            )

        if exp_cpu > 0.0 and act_cpu <= 0.0:
            raise RuntimeConfigVerificationError(
                "RUNTIME_CPU_UNCAPPED_ZERO",
                f"Declared capped service {svc!r} ({exp_cpu} vCPU) is running UNCAPPED (cpus_limit=0.0)",
                details={
                    "service": svc,
                    "expected_cpus": exp_cpu,
                    "actual_cpus": act_cpu,
                },
            )
        if abs(act_cpu - exp_cpu) > 0.02:
            raise RuntimeConfigVerificationError(
                "RUNTIME_CPU_LIMIT_MISMATCH",
                f"Service {svc!r} cpus_limit={act_cpu} != declared {exp_cpu} vCPU",
                details={
                    "service": svc,
                    "expected_cpus": exp_cpu,
                    "actual_cpus": act_cpu,
                },
            )

        if exp_mem > 0.0 and act_mem <= 0.0:
            raise RuntimeConfigVerificationError(
                "RUNTIME_RAM_UNCAPPED_ZERO",
                f"Declared capped service {svc!r} ({exp_mem} MiB) has memory_limit_mib=0.0",
                details={
                    "service": svc,
                    "expected_mem_mib": exp_mem,
                    "actual_mem_mib": act_mem,
                },
            )
        if abs(act_mem - exp_mem) > 1.5:
            raise RuntimeConfigVerificationError(
                "RUNTIME_RAM_LIMIT_MISMATCH",
                f"Service {svc!r} memory_limit_mib={act_mem} != declared {exp_mem} MiB",
                details={
                    "service": svc,
                    "expected_mem_mib": exp_mem,
                    "actual_mem_mib": act_mem,
                },
            )

        actual_cpus_total += act_cpu
        actual_mem_total += act_mem

    # Check backend in-container cgroup + Gunicorn workers x threads
    be_w = workers["backend"]
    be_cg_cpu = be_w.get("cgroup_cpus")
    if be_cg_cpu is not None and float(be_cg_cpu) <= 0.0:
        raise RuntimeConfigVerificationError(
            "RUNTIME_CPU_UNCAPPED_ZERO",
            "Backend in-container /sys/fs/cgroup/cpu.max reports 'max' (0.0 uncapped)",
            details={"backend_worker_inspection": be_w},
        )
    if (
        be_cg_cpu is not None
        and abs(float(be_cg_cpu) - float(spec["backend_cpus"])) > 0.02
    ):
        raise RuntimeConfigVerificationError(
            "RUNTIME_CPU_LIMIT_MISMATCH",
            f"Backend in-container cgroup_cpus={be_cg_cpu} != declared {spec['backend_cpus']}",
            details={"backend_worker_inspection": be_w},
        )

    exp_gw = int(spec["gunicorn_workers"])
    exp_gt = int(spec["gunicorn_threads"])
    live_gw = int(be_w.get("live_gunicorn_worker_count") or 0)
    env_gw = int(be_w.get("env_gunicorn_workers") or 0)
    env_gt = int(be_w.get("env_gunicorn_threads") or 0)
    if live_gw != exp_gw or env_gw != exp_gw or env_gt != exp_gt:
        raise RuntimeConfigVerificationError(
            "RUNTIME_WORKER_COUNT_MISMATCH",
            (
                f"Backend Gunicorn mismatch: live_workers={live_gw}, env={env_gw}x{env_gt}; "
                f"expected {exp_gw}x{exp_gt} ({exp_gw * exp_gt} threads)"
            ),
            details={"backend_worker_inspection": be_w},
        )

    # Check dedicated RQ workers (queue-short = 1, queue-long = 1) and daemon processes
    qs_w = workers["queue-short"]
    if (
        int(qs_w.get("registered_rq_worker_count") or 0) != 1
        or int(qs_w.get("live_process_count") or 0) < 1
    ):
        raise RuntimeConfigVerificationError(
            "RUNTIME_WORKER_COUNT_MISMATCH",
            f"queue-short expected 1 registered RQ worker (short,default), got {qs_w}",
            details={"queue_short_worker_inspection": qs_w},
        )

    ql_w = workers["queue-long"]
    if (
        int(ql_w.get("registered_rq_worker_count") or 0) != 1
        or int(ql_w.get("live_process_count") or 0) < 1
    ):
        raise RuntimeConfigVerificationError(
            "RUNTIME_WORKER_COUNT_MISMATCH",
            f"queue-long expected 1 registered RQ worker (long), got {ql_w}",
            details={"queue_long_worker_inspection": ql_w},
        )

    for daemon_svc in (
        "frontend",
        "websocket",
        "scheduler",
        "db",
        "redis-cache",
        "redis-queue",
    ):
        dw = workers[daemon_svc]
        if (
            not bool(dw.get("probe_ok", True))
            or int(dw.get("live_process_count") or 0) < 1
        ):
            raise RuntimeConfigVerificationError(
                "RUNTIME_WORKER_COUNT_MISMATCH",
                f"Service {daemon_svc!r} has 0 live daemon/worker processes: {dw}",
                details={"service": daemon_svc, "worker_inspection": dw},
            )

    bp_bytes = int(mariadb_vars.get("innodb_buffer_pool_size") or 0)
    log_bytes = int(mariadb_vars.get("innodb_log_file_size") or 0)
    flush_trx = str(mariadb_vars.get("innodb_flush_log_at_trx_commit") or "")
    if bp_bytes != EXPECTED_INNODB_BUFFER_POOL_BYTES:
        raise RuntimeConfigVerificationError(
            "RUNTIME_DB_BUFFER_POOL_MISMATCH",
            f"MariaDB innodb_buffer_pool_size={bp_bytes} != expected {EXPECTED_INNODB_BUFFER_POOL_BYTES}",
            details={"mariadb_variables": mariadb_vars},
        )
    if log_bytes != EXPECTED_INNODB_LOG_FILE_BYTES or flush_trx != "1":
        raise RuntimeConfigVerificationError(
            "RUNTIME_DB_REDO_OR_DURABILITY_MISMATCH",
            f"MariaDB log/durability mismatch: log={log_bytes}, flush_trx={flush_trx!r}",
            details={"mariadb_variables": mariadb_vars},
        )

    return {
        "verified": True,
        "profile_key": profile_key,
        "profile_id": spec["profile_id"],
        "config_spec_sha256": spec["config_spec_sha256"],
        "verify_9_service_config_spec_sha256": spec[
            "verify_9_service_config_spec_sha256"
        ],
        "verified_service_count": len(VERIFY_DAEMON_SERVICES),
        "actual_total_capped_cpus_9_services": round(actual_cpus_total, 3),
        "actual_total_capped_ram_mib_9_services": round(actual_mem_total, 1),
        "live_gunicorn_workers": live_gw,
        "live_gunicorn_threads_per_worker": exp_gt,
        "effective_total_web_threads": live_gw * exp_gt,
        "live_queue_short_rq_workers": 1,
        "live_queue_long_rq_workers": 1,
    }
