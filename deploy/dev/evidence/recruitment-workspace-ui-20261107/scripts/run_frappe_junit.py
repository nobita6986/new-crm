#!/usr/bin/env python3
"""Zero-dependency Frappe unittest runner that emits standard JUnit XML + verbose text output.

Runs inside `/home/frappe/frappe-bench/env/bin/python` without requiring internet/PyPI `xmlrunner`.
Usage:
    /home/frappe/frappe-bench/env/bin/python /tmp/run_frappe_junit.py <site> <dotted_module> <xml_out_path>
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import importlib
import inspect
import os
from pathlib import Path
import sys
import time
import unittest
import xml.dom.minidom
import xml.etree.ElementTree as ET

import frappe


class RecordingTextTestResult(unittest.TextTestResult):
	def __init__(self, stream, descriptions, verbosity):
		super().__init__(stream, descriptions, verbosity)
		self._starts: dict[str, float] = {}
		self.records: list[dict] = []

	def _key(self, test: unittest.TestCase) -> str:
		return test.id()

	def startTest(self, test: unittest.TestCase):
		self._starts[self._key(test)] = time.monotonic()
		super().startTest(test)

	def _record(self, test: unittest.TestCase, status: str, detail: str = "") -> None:
		elapsed = max(0.0, time.monotonic() - self._starts.get(self._key(test), time.monotonic()))
		cls = test.__class__
		classname = f"{cls.__module__}.{cls.__qualname__}"
		method_name = getattr(test, "_testMethodName", test.id().rsplit(".", 1)[-1])
		line_no = 1
		file_path = inspect.getsourcefile(cls) or ""
		fn = getattr(cls, method_name, None)
		if fn is not None:
			try:
				_, line_no = inspect.getsourcelines(fn)
			except OSError:
				line_no = 1
		self.records.append(
			{
				"classname": classname,
				"name": method_name,
				"time": round(elapsed, 4),
				"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
				"file": file_path,
				"line": line_no,
				"status": status,
				"detail": detail,
				"doc": (test.shortDescription() or "").strip(),
			}
		)

	def addSuccess(self, test: unittest.TestCase):
		super().addSuccess(test)
		self._record(test, "PASS")

	def addFailure(self, test: unittest.TestCase, err):
		super().addFailure(test, err)
		self._record(test, "FAIL", self._exc_info_to_string(err, test))

	def addError(self, test: unittest.TestCase, err):
		super().addError(test, err)
		self._record(test, "ERROR", self._exc_info_to_string(err, test))

	def addSkip(self, test: unittest.TestCase, reason: str):
		super().addSkip(test, reason)
		self._record(test, "SKIP", reason)

	def addSubTest(self, test: unittest.TestCase, subtest: unittest.TestCase, err):
		super().addSubTest(test, subtest, err)
		if err is not None:
			status = "FAIL" if issubclass(err[0], test.failureException) else "ERROR"
			cls = test.__class__
			sub_suffix = subtest.id()[len(test.id()) :]
			method_name = f"{getattr(test, '_testMethodName', test.id().rsplit('.', 1)[-1])}{sub_suffix}"
			self.records.append(
				{
					"classname": f"{cls.__module__}.{cls.__qualname__}",
					"name": method_name,
					"time": round(max(0.0, time.monotonic() - self._starts.get(self._key(test), time.monotonic())), 4),
					"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
					"file": inspect.getsourcefile(cls) or "",
					"line": 1,
					"status": status,
					"detail": self._exc_info_to_string(err, subtest),
					"doc": (test.shortDescription() or "").strip(),
				}
			)


def write_junit_xml(records: list[dict], out_path: Path) -> None:
	grouped: OrderedDict[str, list[dict]] = OrderedDict()
	for rec in records:
		grouped.setdefault(rec["classname"], []).append(rec)

	root = ET.Element("testsuites")
	for classname, items in grouped.items():
		total_time = sum(r["time"] for r in items)
		failures = sum(1 for r in items if r["status"] == "FAIL")
		errors = sum(1 for r in items if r["status"] == "ERROR")
		skipped = sum(1 for r in items if r["status"] == "SKIP")
		ts_el = ET.SubElement(
			root,
			"testsuite",
			{
				"name": classname,
				"tests": str(len(items)),
				"file": items[0]["file"],
				"time": f"{total_time:.3f}",
				"timestamp": items[0]["timestamp"],
				"failures": str(failures),
				"errors": str(errors),
				"skipped": str(skipped),
			},
		)
		for r in items:
			tc_el = ET.SubElement(
				ts_el,
				"testcase",
				{
					"classname": r["classname"],
					"name": r["name"],
					"time": f"{r['time']:.3f}",
					"timestamp": r["timestamp"],
					"file": r["file"],
					"line": str(r["line"]),
				},
			)
			if r["doc"]:
				tc_el.append(ET.Comment(r["doc"].replace("--", "- -")))
			if r["status"] == "FAIL":
				f_el = ET.SubElement(tc_el, "failure", {"type": "AssertionError", "message": r["detail"].splitlines()[-1] if r["detail"] else "AssertionError"})
				f_el.text = r["detail"]
			elif r["status"] == "ERROR":
				e_el = ET.SubElement(tc_el, "error", {"type": "Exception", "message": r["detail"].splitlines()[-1] if r["detail"] else "Exception"})
				e_el.text = r["detail"]
			elif r["status"] == "SKIP":
				ET.SubElement(tc_el, "skipped", {"message": r["detail"]})

	raw_bytes = ET.tostring(root, encoding="utf-8")
	pretty = xml.dom.minidom.parseString(raw_bytes).toprettyxml(indent="\t", encoding="UTF-8").decode("utf-8")
	out_path.parent.mkdir(parents=True, exist_ok=True)
	out_path.write_bytes(pretty.replace("\r\n", "\n").encode("utf-8"))


def main() -> int:
	if len(sys.argv) != 4:
		print("Usage: run_frappe_junit.py <site> <dotted_module> <xml_out_path>", file=sys.stderr)
		return 2

	site, module_name, xml_out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
	os.chdir("/home/frappe/frappe-bench/sites")
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.flags.in_test = True
	frappe.clear_cache()

	try:
		mod = importlib.import_module(module_name)
		suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
		runner = unittest.TextTestRunner(
			stream=sys.stdout,
			verbosity=2,
			resultclass=RecordingTextTestResult,
		)
		result: RecordingTextTestResult = runner.run(suite)  # type: ignore[assignment]
		write_junit_xml(result.records, xml_out)
		return 0 if result.wasSuccessful() else 1
	finally:
		frappe.destroy()


if __name__ == "__main__":
	sys.exit(main())
