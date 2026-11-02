#!/usr/bin/env python3
"""Rebuild `translations/vi.csv` from the authoritative sources.

The merge of the two branches left rows whose source and target were split
by the conflict resolver, and both branches had rows that were written
unquoted (a source with a comma). The file is rebuilt here:

* every row of the **P0 base** (`2cd4f60`) is kept verbatim, including the
  optional context column;
* every row of the **backend branch** (`codex/p1-foundation`) is added when
  it is a well-formed two-column row;
* every message of `crm_cskh.p1_translations.TRANSLATIONS` is added (or
  replaced) with the Vietnamese text of that mapping — it is the mapping the
  UI actually uses;
* the result is written with the CSV writer, so a message that contains a
  comma is quoted and the file stays parseable by Frappe.
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
BACKEND = "codex/p1-foundation"


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
		if not row:
			continue
		# Frappe reads `source,target[,context]`: the context column scopes
		# the translation to one doctype, so it must be preserved.
		if len(row) == 1:
			rows.append([row[0], ""])
		elif len(row) == 2:
			rows.append([row[0], row[1]])
		else:
			rows.append([row[0], row[1], row[2]])
	return rows


def main():
	rows = rows_of(BASE)
	index = {row[0]: row[1] for row in rows}

	added = 0
	for row in rows_of(BACKEND):
		source, target = row[0], row[1]
		if not target:
			continue
		if source not in index:
			rows.append([source, target])
			index[source] = target
			added += 1
		elif index[source] != target and len(source) > 0:
			# The backend branch owns the P1 wording of the error codes.
			index[source] = target

	for message, target in sorted(p1_translations.TRANSLATIONS.items()):
		if index.get(message) == target:
			continue
		if message in index:
			index[message] = target
		else:
			rows.append([message, target])
			index[message] = target
			added += 1

	buffer = io.StringIO()
	writer = csv.writer(buffer, lineterminator="\n")
	writer.writerows(rows)
	with io.open(PATH, "w", encoding="utf-8", newline="") as handle:
		handle.write(buffer.getvalue())
	print({"rows": len(rows), "added": added})


if __name__ == "__main__":
	main()
