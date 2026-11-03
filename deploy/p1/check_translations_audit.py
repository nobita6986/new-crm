"""Audit: every user-facing string of the P1 pages must be in the catalogue.

Checks four things:
  1. every `cskh.t(...)` / `t(...)` / `_(...)` of the pages is declared in
     `crm_cskh/p1_strings.py`;
  2. every declared string has a Vietnamese row in `translations/vi.csv`;
  3. no Vietnamese row of the P1 catalogue is an untranslated English copy;
  4. zero orphan declarations missing from `translations/vi.csv`.
"""

import csv
import importlib.util
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
APP = os.path.join(ROOT, "apps", "crm_cskh", "crm_cskh")

PAGE_DIRS = [
	os.path.join(APP, "public", "cskh"),
	os.path.join(APP, "www", "cskh"),
	os.path.join(APP, "templates", "cskh"),
]

PATTERNS = (
	re.compile(r"\bcskh\.t\(\s*(['\"])(.+?)\1"),
	re.compile(r"(?<![.\w])t\(\s*(['\"])(.+?)\1"),
	re.compile(r"_\(\s*(['\"])(.+?)\1"),
)


def used_strings():
	found = set()
	for folder in PAGE_DIRS:
		if not os.path.isdir(folder):
			continue
		for name in os.listdir(folder):
			if not name.endswith((".js", ".html")):
				continue
			with open(os.path.join(folder, name), encoding="utf-8") as handle:
				content = handle.read()
			for pat in PATTERNS:
				for match in pat.finditer(content):
					found.add(match.group(2))
	return found


def declared_strings():
	"""Import the catalogue module without a Frappe runtime."""
	stub = types.ModuleType("frappe")
	stub.local = types.SimpleNamespace(lang="vi")
	stub._ = lambda message, lang=None: message
	sys.modules.setdefault("frappe", stub)

	spec = importlib.util.spec_from_file_location(
		"crm_cskh_p1_strings", os.path.join(APP, "p1_strings.py")
	)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return set(module.ALL_STRINGS) | set(module.ERROR_EXPLANATIONS)


def translation_rows():
	path = os.path.join(APP, "translations", "vi.csv")
	rows = {}
	if not os.path.exists(path):
		return rows
	with open(path, encoding="utf-8", newline="") as handle:
		for row in csv.reader(handle):
			if len(row) == 2:
				rows[row[0]] = row[1]
			elif len(row) >= 3 and row[0] not in rows:
				rows[row[0]] = row[1]
	return rows


def main():
	used = used_strings()
	declared = declared_strings()
	rows = translation_rows()

	missing_declaration = sorted(s for s in used if s not in declared)
	missing_translation = sorted(s for s in used if s not in rows)
	orphan_declaration = sorted(s for s in declared if s not in rows)

	english_copy = sorted(
		source
		for source, target in rows.items()
		if source in declared and target == source and not source.isupper()
	)

	print("used strings        :", len(used))
	print("declared strings    :", len(declared))
	print("vi.csv rows         :", len(rows))
	print("missing declaration :", len(missing_declaration))
	for item in missing_declaration:
		print("   -", item)
	print("missing translation :", len(missing_translation))
	for item in missing_translation:
		print("   -", item)
	print("untranslated (EN)   :", len(english_copy))
	for item in english_copy:
		print("   -", item)
	print("orphan declaration  :", len(orphan_declaration))
	for item in orphan_declaration:
		print("   -", item)

	return (
		1
		if (missing_declaration or missing_translation or english_copy or orphan_declaration)
		else 0
	)


if __name__ == "__main__":
	sys.exit(main())
