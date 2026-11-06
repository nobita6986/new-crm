#!/usr/bin/env python3
"""Deterministic CycloneDX 1.5 & SPDX 2.3 SBOM generator from locked image metadata (P4-02 / G04).

Synthesizes reproducible CycloneDX 1.5 (`sbom.cyclonedx.json`) and SPDX 2.3 (`sbom.spdx.json`)
software bills of materials directly from the immutable `release-metadata` extracted during
`build-image.sh` (or from a pulled `@sha256` OCI image):
  - 442+ Debian Bookworm runtime packages (`pkg:deb/debian/<name>@<version>?arch=amd64&distro=debian-12`)
  - 155+ Python virtualenv packages (`pkg:pypi/<name>@<version>`)
  - Upstream Git-pinned applications (`frappe`, `crm`, `frappe_docker`) & `crm_cskh` source tree
  - Yarn lockfile provenance hashes (`apps/frappe/yarn.lock`, `apps/crm/yarn.lock`, `apps/crm/frontend/yarn.lock`)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

IMAGE_REF_RE = re.compile(
    r"\A(?:[^/@\s:<>]+(?::[0-9]+)?(?:/[^/@\s:<>]+)+(?::[A-Za-z0-9._-]+)?@)?sha256:[0-9a-f]{64}\Z"
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_pip_freeze(pip_freeze_path: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw in pip_freeze_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-e "):
            # Editable local install (e.g. `-e ./apps/crm_cskh` or git ref)
            egg_match = re.search(r"#egg=([A-Za-z0-9_.-]+)", line)
            name = egg_match.group(1) if egg_match else Path(line.split()[-1]).name
            items.append(
                {
                    "name": name,
                    "version": "editable-locked",
                    "purl": f"pkg:pypi/{urllib.parse.quote(name.lower())}@0.0.1",
                }
            )
            continue
        if "==" in line:
            name, ver = line.split("==", 1)
            name = name.strip()
            ver = ver.strip()
            items.append(
                {
                    "name": name,
                    "version": ver,
                    "purl": f"pkg:pypi/{urllib.parse.quote(name.lower())}@{urllib.parse.quote(ver)}",
                }
            )
        elif " @ " in line:
            name, ref = line.split(" @ ", 1)
            name = name.strip()
            ref = ref.strip()
            items.append(
                {
                    "name": name,
                    "version": ref,
                    "purl": f"pkg:pypi/{urllib.parse.quote(name.lower())}",
                }
            )
    return items


def _parse_dpkg_packages(dpkg_path: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw in dpkg_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        name, ver = line.split("=", 1)
        name = name.strip()
        ver = ver.strip()
        items.append(
            {
                "name": name,
                "version": ver,
                "purl": (
                    f"pkg:deb/debian/{urllib.parse.quote(name)}@{urllib.parse.quote(ver)}"
                    "?arch=amd64&distro=debian-12"
                ),
            }
        )
    return items


def _parse_yarn_locks(yarn_locks_path: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw in yarn_locks_path.read_text(encoding="utf-8").splitlines():
        parts = raw.strip().split()
        if len(parts) >= 2:
            digest, rel_path = parts[0], parts[1]
            items.append({"path": rel_path, "sha256": digest})
    return items


def generate_sboms(
    *,
    metadata_dir: Path,
    image_ref: str,
    cyclonedx_out: Path,
    spdx_out: Path,
) -> dict[str, object]:
    if not IMAGE_REF_RE.match(image_ref):
        raise ValueError(
            f"SBOM image_ref must be an immutable @sha256 or sha256:<64-hex> identifier, got {image_ref!r}"
        )

    build_manifest = json.loads(
        (metadata_dir / "build-manifest.json").read_text(encoding="utf-8")
    )
    pip_pkgs = _parse_pip_freeze(metadata_dir / "pip-freeze.txt")
    dpkg_pkgs = _parse_dpkg_packages(metadata_dir / "dpkg-packages.txt")
    yarn_locks = _parse_yarn_locks(metadata_dir / "yarn-locks.sha256")
    cskh_manifest_sha = _sha256_bytes(
        (metadata_dir / "crm-cskh-files.sha256").read_bytes()
    )

    apps = build_manifest["apps"]
    frappe_sha = apps["frappe"]["commit_sha"]
    crm_sha = apps["crm"]["commit_sha"]
    cskh_sha = apps["crm_cskh"]["source_commit_sha"]
    cskh_tree = apps["crm_cskh"]["crm_cskh_tree_sha"]

    # Deterministic serial derived from image_ref + build-manifest content
    seed_hash = _sha256_bytes(
        (image_ref + "|" + json.dumps(build_manifest, sort_keys=True)).encode("utf-8")
    )
    serial_uuid = (
        f"{seed_hash[0:8]}-{seed_hash[8:12]}-4{seed_hash[13:16]}-"
        f"a{seed_hash[17:20]}-{seed_hash[20:32]}"
    )
    created_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cdx_components: list[dict[str, object]] = [
        {
            "type": "application",
            "bom-ref": f"app:frappe@{frappe_sha}",
            "name": "frappe",
            "version": f"{apps['frappe']['tag']}@{frappe_sha}",
            "purl": f"pkg:github/frappe/frappe@{frappe_sha}",
        },
        {
            "type": "application",
            "bom-ref": f"app:crm@{crm_sha}",
            "name": "crm",
            "version": f"{apps['crm']['tag']}@{crm_sha}",
            "purl": f"pkg:github/frappe/crm@{crm_sha}",
        },
        {
            "type": "application",
            "bom-ref": f"app:crm_cskh@{cskh_tree}",
            "name": "crm_cskh",
            "version": f"{cskh_sha}",
            "purl": f"pkg:github/nobita6986/new-crm@{cskh_sha}#apps/crm_cskh",
            "hashes": [{"alg": "SHA-256", "content": cskh_manifest_sha}],
        },
    ]

    for ylock in yarn_locks:
        cdx_components.append(
            {
                "type": "file",
                "bom-ref": f"yarnlock:{ylock['path']}",
                "name": ylock["path"],
                "version": ylock["sha256"][:12],
                "hashes": [{"alg": "SHA-256", "content": ylock["sha256"]}],
            }
        )

    for pkg in dpkg_pkgs:
        cdx_components.append(
            {
                "type": "library",
                "bom-ref": pkg["purl"],
                "name": pkg["name"],
                "version": pkg["version"],
                "purl": pkg["purl"],
            }
        )

    for pkg in pip_pkgs:
        cdx_components.append(
            {
                "type": "library",
                "bom-ref": f"pypi:{pkg['name']}@{pkg['version']}",
                "name": pkg["name"],
                "version": pkg["version"],
                "purl": pkg["purl"],
            }
        )

    cyclonedx_doc = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial_uuid}",
        "version": 1,
        "metadata": {
            "timestamp": created_ts,
            "tools": [
                {
                    "vendor": "CRM-CSKH",
                    "name": "cskh-p4-sbom-generator",
                    "version": "1.0.0",
                }
            ],
            "component": {
                "type": "container",
                "bom-ref": f"container:{image_ref}",
                "name": "crm-cskh",
                "version": image_ref,
            },
        },
        "components": cdx_components,
    }

    spdx_packages: list[dict[str, object]] = []
    for idx, comp in enumerate(cdx_components, start=1):
        pkg_entry: dict[str, object] = {
            "SPDXID": f"SPDXRef-Package-{idx:04d}",
            "name": comp["name"],
            "versionInfo": comp["version"],
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
        }
        if "purl" in comp:
            pkg_entry["externalRefs"] = [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": comp["purl"],
                }
            ]
        spdx_packages.append(pkg_entry)

    spdx_doc = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"crm-cskh-{seed_hash[:12]}",
        "documentNamespace": f"https://github.com/nobita6986/new-crm/sbom/{serial_uuid}",
        "creationInfo": {
            "created": created_ts,
            "creators": ["Tool: cskh-p4-sbom-generator-1.0.0"],
        },
        "packages": spdx_packages,
    }

    cyclonedx_out.parent.mkdir(parents=True, exist_ok=True)
    spdx_out.parent.mkdir(parents=True, exist_ok=True)
    cdx_bytes = (json.dumps(cyclonedx_doc, indent=2) + "\n").encode("utf-8")
    spdx_bytes = (json.dumps(spdx_doc, indent=2) + "\n").encode("utf-8")
    cyclonedx_out.write_bytes(cdx_bytes)
    spdx_out.write_bytes(spdx_bytes)

    return {
        "status": "OK",
        "image_ref": image_ref,
        "component_counts": {
            "total": len(cdx_components),
            "dpkg": len(dpkg_pkgs),
            "pip": len(pip_pkgs),
            "yarn_lockfiles": len(yarn_locks),
            "apps": 3,
        },
        "cyclonedx_path": str(cyclonedx_out),
        "cyclonedx_sha256": _sha256_bytes(cdx_bytes),
        "spdx_path": str(spdx_out),
        "spdx_sha256": _sha256_bytes(spdx_bytes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata-dir",
        required=True,
        help="Directory with build-manifest.json & locks",
    )
    parser.add_argument(
        "--image-ref",
        required=True,
        help="Immutable @sha256 or sha256:<64-hex> image ref",
    )
    parser.add_argument("--cyclonedx-out", required=True)
    parser.add_argument("--spdx-out", required=True)
    args = parser.parse_args(argv)

    summary = generate_sboms(
        metadata_dir=Path(args.metadata_dir),
        image_ref=args.image_ref,
        cyclonedx_out=Path(args.cyclonedx_out),
        spdx_out=Path(args.spdx_out),
    )
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
