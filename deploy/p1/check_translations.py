#!/usr/bin/env python3
"""Check and repair the P1 Vietnamese translation CSV.

The CSV is the contract of the translation layer: two columns, `source` and
`target`, every row quoted when it contains a comma. The merge of the two
branches left a few rows that were written unquoted, so the file is parsed
and rewritten here.
"""

import csv
import io
import sys


def check(path):
	with io.open(path, encoding="utf-8", newline="") as handle:
		rows = list(csv.reader(handle))
	# Frappe reads `source,target[,context]`: the context column scopes the
	# translation to one doctype, so a three-column row is valid.
	bad = [row for row in rows if len(row) not in (2, 3)]
	return rows, bad


def rewrite(path):
	with io.open(path, encoding="utf-8", newline="") as handle:
		rows = list(csv.reader(handle))
	fixed = []
	for row in rows:
		if len(row) == 2:
			fixed.append(row)
			continue
		if not row:
			continue
		# A row that was written unquoted: the source ends at the first comma
		# that is followed by the *English* continuation. The translation is
		# the last column.
		source = row[0]
		target = row[-1]
		fixed.append([source, target])
	buffer = io.StringIO()
	writer = csv.writer(buffer, lineterminator="\n")
	writer.writerows(fixed)
	with io.open(path, "w", encoding="utf-8", newline="") as handle:
		handle.write(buffer.getvalue())
	return len(fixed)


if __name__ == "__main__":
	target = sys.argv[1]
	rows, bad = check(target)
	print("rows", len(rows), "malformed", len(bad))
	for row in bad[:20]:
		print("  ", row)
