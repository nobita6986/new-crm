#!/usr/bin/env python3
"""Rebuild `translations/vi.csv` for P2 without clobbering P0/P1 or T1B rows.

1. Preserves every row from the accepted P1 baseline (`1913d2bd5ebfdc894eaa492b46be502dbdf44808`),
   including contextual 3-column P0 rows;
2. Optionally merges rows from a committed T1B P2 backend revision (`--backend-ref`);
3. Merges all P1 + P2 UI translations from `crm_cskh.p1_translations.TRANSLATIONS`
   (which includes `crm_cskh.p2_translations.P2_TRANSLATIONS`);
4. Writes UTF-8 CSV with LF line endings.
"""

import argparse
import csv
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(ROOT, "apps", "crm_cskh", "crm_cskh")
PATH = os.path.join(APP, "translations", "vi.csv")
sys.path.insert(0, os.path.join(ROOT, "apps", "crm_cskh"))

from crm_cskh import p1_translations  # noqa: E402

P1_ACCEPTED_MAIN = "1913d2bd5ebfdc894eaa492b46be502dbdf44808"


def rows_of(revision):
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{revision}:apps/crm_cskh/crm_cskh/translations/vi.csv"],
            cwd=ROOT,
        )
    except Exception:
        return []
    rows = []
    for row in csv.reader(io.StringIO(raw.decode("utf-8"))):
        if not row or not row[0]:
            continue
        if len(row) == 1:
            rows.append([row[0], ""])
        elif len(row) == 2:
            rows.append([row[0], row[1]])
        else:
            rows.append([row[0], row[1], row[2]])
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend-ref", default="", help="Optional committed T1B revision to merge"
    )
    args = parser.parse_args()

    rows = rows_of(P1_ACCEPTED_MAIN)
    two_col_pos = {}
    ctx_keys = set()
    for idx, row in enumerate(rows):
        if len(row) == 2:
            two_col_pos[row[0]] = idx
        elif len(row) >= 3:
            ctx_keys.add((row[0], row[2]))

    added = 0
    updated = 0

    if args.backend_ref:
        for row in rows_of(args.backend_ref):
            source, target = row[0], row[1]
            if not target:
                continue
            if len(row) >= 3 and row[2]:
                key = (source, row[2])
                if key not in ctx_keys:
                    ctx_keys.add(key)
                    rows.append([source, target, row[2]])
                    added += 1
                continue
            if source in two_col_pos:
                pos = two_col_pos[source]
                if rows[pos][1] != target:
                    rows[pos][1] = target
                    updated += 1
            else:
                two_col_pos[source] = len(rows)
                rows.append([source, target])
                added += 1

    for message, target in sorted(p1_translations.TRANSLATIONS.items()):
        if message in two_col_pos:
            pos = two_col_pos[message]
            if rows[pos][1] != target:
                rows[pos][1] = target
                updated += 1
        else:
            two_col_pos[message] = len(rows)
            rows.append([message, target])
            added += 1

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    with open(PATH, "w", encoding="utf-8", newline="") as handle:
        handle.write(buffer.getvalue())
    print({"rows": len(rows), "added": added, "updated": updated})


if __name__ == "__main__":
    main()
