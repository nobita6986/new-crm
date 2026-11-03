#!/usr/bin/env python3
"""Rebuild `translations/vi.csv` from the authoritative sources.

* every row of the **P0 base** (`2cd4f60`) is kept verbatim, including the
  optional 3rd `context` column;
* every well-formed row of the **backend baseline**
  (`3025495f58bc0391c3f8d092af500b0566a5f038`) is merged without overwriting
  contextual P0 rows;
* every message of `crm_cskh.p1_translations.TRANSLATIONS` is added (or
  updated in-place on its 2-column row);
* the result is written with standard CSV quoting + LF line endings so messages
  containing commas stay parseable by Frappe.
"""

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

BASE = "2cd4f60"
BACKEND = "3025495f58bc0391c3f8d092af500b0566a5f038"


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
	rows = rows_of(BASE)
	# Track index of 2-column (non-contextual) rows in `rows` so updates mutate `rows` in-place.
	two_col_pos = {}
	for idx, row in enumerate(rows):
		if len(row) == 2:
			two_col_pos[row[0]] = idx

	added = 0
	updated = 0

	for row in rows_of(BACKEND):
		source, target = row[0], row[1]
		if not target:
			continue
		# Skip the unquoted comma split ("Customer pool, imports, ...") from 3025495.
		if source == "Customer pool" and target.startswith(" "):
			continue
		if len(row) >= 3 and row[2]:
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
