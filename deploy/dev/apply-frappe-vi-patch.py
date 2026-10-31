#!/usr/bin/env python3
"""Apply the small, pinned Vietnamese accessibility-label patch to DEV Frappe."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PINNED_FRAPPE_SHA = "d2efa79657ba5c1abf96098b3bfacc199517d57e"
REPLACEMENTS = {
    "frappe/templates/includes/navbar/navbar.html": (
        'aria-label="Toggle navigation"',
        "aria-label=\"{{ _('Toggle navigation')|e }}\"",
    ),
    "frappe/templates/doc.html": (
        'aria-label="Toggle navigation"',
        "aria-label=\"{{ _('Toggle navigation')|e }}\"",
    ),
}


def patch_once(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if text.count(new) == 1 and old not in text:
        return False
    if text.count(old) != 1 or new in text:
        raise RuntimeError(f"Pinned template does not match expected text: {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: apply-frappe-vi-patch.py <frappe-app-dir>", file=sys.stderr)
        return 2

    frappe_root = Path(sys.argv[1]).resolve()
    actual_sha = subprocess.check_output(
        ["git", "-C", str(frappe_root), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual_sha != PINNED_FRAPPE_SHA:
        print(
            f"Refusing unreviewed Frappe revision {actual_sha}; expected {PINNED_FRAPPE_SHA}",
            file=sys.stderr,
        )
        return 2

    changed = False
    for relative, (old, new) in REPLACEMENTS.items():
        path = frappe_root / relative
        if not path.is_file():
            print(f"Missing pinned Frappe template: {path}", file=sys.stderr)
            return 2
        patched = patch_once(path, old, new)
        changed = patched or changed
        print(f"{'patched' if patched else 'already patched'}: {relative}")

    print(
        "Vietnamese navigation labels are ready."
        if changed
        else "Vietnamese navigation patch already applied."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
