#!/usr/bin/env python3
"""Gate per-method cho G0-07: đối chiếu JUnit XML với inventory chuẩn 99 method.

Vì sao cần script này
--------------------
`bench run-tests --module ...` chỉ trả exit code **tổng hợp** cho cả module.
Exit code 0 KHÔNG chứng minh từng method trong 99 method đã thực sự chạy:
- `--failfast` sẽ dừng sớm ⇒ XML thiếu `<testcase>` cho các method chưa chạy.
- `loadTestsFromModule` chỉ nạp những gì unittest tìm thấy — một method bị
    đổi tên / xoá trong source sẽ **biến mất khỏi XML** mà exit code vẫn 0.
- Một `@unittest.skip` sẽ khiến method xuất hiện trong XML với `<skipped/>` mà
    exit code vẫn 0 ⇒ 99/99 "chạy" nhưng 0 method nào thực sự kiểm chứng.

§3.6 của `t1b-g007-ci-plan-review-2026-10-22.md` yêu cầu: đọc **5 file
`.junit.xml` thô**, đối chiếu *tập method thấy trong XML == tập 99 method*
(canonical inventory `g007_test_inventory.json`), và method vắng mặt ở mọi XML
⇒ **NOT RUN** (không được suy từ tổng `OK` của bất kỳ lần chạy nào).

Nguyên tắc fail-closed (T1C lần 17)
-----------------------------------
Mọi tình huống mơ hồ đều là FAIL, không phải PASS:
  1. Bất kỳ testcase `SKIP` nào ⇒ gate fail (SKIP không phải bằng chứng).
  2. XML hỏng / không parse được ⇒ gate fail (không được bỏ qua `parse_errors`).
  3. Testcase trong XML không khớp inventory ⇒ `unexpected_cases` và gate fail,
     trừ khi nằm trong allowlist giải thích được (xem `--allow-unexpected`).
  4. Định danh theo **(module, class, method)** — không phải `(class, method)`.
     XML của module A **không** thể làm thoả inventory của module B.
  5. Mỗi module trong inventory phải có đúng một file XML gắn đúng với lệnh
     chạy; thiếu XML / sai module / thiếu method / duplicate không hợp lệ /
     mismatch source-vs-inventory ⇒ gate fail.
  6. `subTest` được quy về method gốc, nhưng giữ kết quả **nặng nhất**:
     ERROR > FAIL > SKIP > PASS.

Script này **chỉ đọc** artifact thô và **không** suy diễn: mỗi dòng kết quả
được gắn với đúng một assertion của inventory.

Cú pháp
-------
    python3 ci_junit_gate.py \
        --inventory /src/deploy/dev/seed/g007_test_inventory.json \
        --xml-dir /tmp/p0-g007-evidence \
        --out /tmp/p0-g007-evidence/g007-per-method-gate.json

Exit code 0 khi mọi method trong inventory có mặt với kết quả PASS và không có
bất kỳ tín hiệu bất thường nào; 1 ngược lại.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Trạng thái per-method.
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_ERROR = "ERROR"
STATUS_SKIP = "SKIP"
STATUS_NOT_RUN = "NOT_RUN"

# Thứ tự nghiêm ngặt khi một method xuất hiện nhiều lần (duplicate XML, subTest,
# re-run): giữ lại kết quả NẶNG nhất để không che failure bằng một lần PASS.
SEVERITY = {STATUS_PASS: 0, STATUS_SKIP: 1, STATUS_FAIL: 2, STATUS_ERROR: 3}

# Suy ra module từ `classname` mà runner ghi ra, ví dụ
# `crm_cskh.tests.test_permissions.TestPermissionBoundaries`.
def module_of_classname(classname: str) -> str:
	"""Tách phần module khỏi `classname` của JUnit (`<module>.<ClassName>`)."""
	return classname.rpartition(".")[0]


# `unittest-xml-reporting` ghi `classname="crm_cskh.tests.test_x.ClassName"`.
# Một số bản chỉ ghi `ClassName` ⇒ khi đó không có module trong tên; module được
# suy ra từ file XML (xem `bind_xml_to_module`) chứ không đoán mù.


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="G0-07 per-method JUnit gate")
	parser.add_argument("--inventory", required=True, help="Canonical inventory JSON")
	parser.add_argument("--xml-dir", required=True, help="Thư mục chứa *.junit.xml")
	parser.add_argument("--out", required=True, help="Nơi ghi báo cáo JSON")
	parser.add_argument(
		"--allow-unexpected",
		action="append",
		default=[],
		metavar="MODULE::CLASS::METHOD",
		help=(
			"Cho phép một testcase ngoài inventory; có thể lặp lại nhiều lần. "
			"Mỗi entry phải được giải thích trong PR — gate fail nếu còn case "
			"không nằm trong allowlist."
		),
	)
	parser.add_argument(
		"--allow-incomplete",
		action="store_true",
		help="Không fail khi thiếu method (chỉ dùng để thu bằng chứng chẩn đoán)",
	)
	return parser.parse_args(argv)


def load_inventory(path: Path) -> dict[str, list[str]]:
	payload = json.loads(path.read_text(encoding="utf-8"))
	modules = payload.get("modules") or {}
	if not modules:
		raise SystemExit(f"Inventory không có 'modules': {path}")
	inventory = {str(module): [str(m) for m in methods] for module, methods in modules.items()}
	# Fail-closed: inventory trùng method (cùng class+method ở 2 module) làm
	# định danh mơ hồ ⇒ từ chối chạy thay vì "chọn bừa một module".
	seen: dict[tuple[str, str], str] = {}
	for module, methods in inventory.items():
		for dotted in methods:
			class_name, _, method_name = dotted.partition(".")
			key = (class_name, method_name)
			if key in seen and seen[key] != module:
				raise SystemExit(
					f"Inventory mơ hồ: {class_name}.{method_name} nằm ở cả "
					f"'{seen[key]}' và '{module}' — định danh per-method không hợp lệ."
				)
			seen[key] = module
	return inventory


def _status_of(case: ET.Element) -> str:
	"""Suy ra trạng thái của một `<testcase>` theo thứ tự ưu tiên."""
	if case.find("error") is not None:
		return STATUS_ERROR
	if case.find("failure") is not None:
		return STATUS_FAIL
	if case.find("skipped") is not None:
		return STATUS_SKIP
	return STATUS_PASS


def _base_method_name(raw_name: str) -> str:
	"""Chuẩn hoá tên `<testcase name=...>` về tên method thuần (fallback).

	`unittest-xml-reporting` 3.2.0 sinh tên khác nhau tuỳ tình huống:
	  - test thường      → `test_method`
	  - subTest **fail**  → `test_method [msg] (params...)`
	    (stdlib `unittest.case._SubTest.id()` = `"<test.id()> [msg] (params)"`,
	     `xmlrunner._test_method_name()` ghép lại bằng ' ')

	Vì 15 chỗ trong 4 file test của G0-07 dùng `self.subTest(...)`, một method có
	subTest fail sẽ xuất hiện với hậu tố. Nếu không chuẩn hoá, gate sẽ **không**
	tìm thấy method đó trong inventory ⇒ quy thành `NOT RUN` ⇒ **che mất failure
	thật**.

	Normalize ở đây chỉ là *fallback*; nguồn chuẩn là `_resolve_method_name()`
	(khớp theo inventory, chọn khớp dài nhất) vì hậu tố subTest có thể chứa
	ký tự lạ (ví dụ `'` trong `repr` của dict params).
	"""
	name = raw_name.strip()
	# Bỏ phần ` (params)` và ` [msg]` ở cuối, nếu có.
	for separator in (" (", " ["):
		index = name.find(separator)
		if index > 0:
			name = name[:index].strip()
	return name


def _resolve_method_name(raw_name: str, known: set[str]) -> tuple[str | None, str]:
	"""Ánh xạ tên thô trong XML → tên method chuẩn của inventory.

	`known` = {method_name, ...} của **class** đang xét (đã lọc theo module).

	Thứ tự ưu tiên:
	  1. Khớp chính xác (test thường).
	  2. Khớp theo tiền tố dài nhất (subTest) — `test_x` khớp `test_x [m] (p)`.
	     Chọn **dài nhất** để tránh nhập ngữ khi có method lồng tên
	     (ví dụ `test_a` và `test_a_b`).

	Trả về `(method_name | None, reason)`.
	"""
	name = raw_name.strip()
	if not name:
		return None, "empty"

	if name in known:
		return name, "exact"

	candidates = [method for method in known if name.startswith(method)]
	if candidates:
		# Chọn khớp dài nhất; bảo đảm phần còn lại bắt đầu bằng dấu phân cách
		# hợp lệ (space hoặc `(`) để không nhập `test_a` vào `test_abc`.
		candidates.sort(key=len, reverse=True)
		for method in candidates:
			remainder = name[len(method) :]
			if remainder[:1] in (" ", "("):
				return method, "prefix"

	fallback = _base_method_name(name)
	if fallback in known:
		return fallback, "fallback"
	return None, "unmatched"


def bind_xml_to_module(xml_path: Path, inventory: dict[str, list[str]]) -> tuple[str | None, str]:
	"""Suy ra module mà 1 file XML phải thuộc về, theo **tên file**.

	CI đặt tên file theo slug module: `test_security_remediation_v2.junit.xml`
	⇒ module `crm_cskh.tests.test_security_remediation_v2`. Ràng buộc này là
	điều kiện cần để "XML của module A không thể thoả inventory module B": nếu
	không gắn file→module từ bên ngoài thì chỉ còn class+method và mọi thứ
	nhập nhằng.
	"""
	slug = xml_path.name[: -len(".junit.xml")] if xml_path.name.endswith(".junit.xml") else xml_path.stem
	# Runner có thể sinh tên kiểu `test_permissions (rerun).junit.xml` khi thợ
	# chạy lại để thu bằng chứng chẩn đoán. Giữ phần slug chính để file đó vẫn
	# bind về đúng module ⇒ phát hiện được duplicate thay vì bỏ sót.
	base_slug = slug.split(" (")[0].strip()
	matches = [module for module in inventory if module.rpartition(".")[2] == base_slug]
	if not matches:
		return None, f"tên file '{xml_path.name}' không khớp module nào trong inventory"
	if len(matches) > 1:
		return None, f"tên file '{xml_path.name}' khớp nhiều module: {sorted(matches)}"
	return matches[0], "filename"


def collect_from_xml(xml_path: Path) -> tuple[list[dict[str, str]], list[str]]:
	"""Trả về `(cases, problems)` đọc từ 1 file JUnit.

	`problems` là lỗi cấu trúc (XML hỏng, thiếu thuộc tính). Gate coi mọi problem
	là fail-closed — không được âm thầm bỏ qua rồi vẫn tính PASS cho phần còn lại.
	"""
	try:
		root = ET.parse(xml_path).getroot()
	except ET.ParseError as error:
		return [], [f"{xml_path.name}: JUnit XML không parse được: {error}"]
	except OSError as error:
		return [], [f"{xml_path.name}: không đọc được file XML: {error}"]

	# `unittest-xml-reporting` xuất ra `<testsuites>` bọc `<testsuite>`; một số
	# runner xuất trực tiếp `<testsuite>`.
	if root.tag == "testsuite":
		testsuites = [root]
	elif root.tag == "testsuites":
		testsuites = list(root.findall("testsuite"))
	else:
		return [], [f"{xml_path.name}: root tag không hợp lệ cho JUnit: <{root.tag}>"]

	problems: list[str] = []
	cases: list[dict[str, str]] = []
	for suite in testsuites:
		for case in suite.findall("testcase"):
			raw_name = (case.get("name") or "").strip()
			classname = (case.get("classname") or "").strip()
			if not raw_name:
				problems.append(f"{xml_path.name}: <testcase> thiếu thuộc tính name")
				continue
			if not classname:
				problems.append(f"{xml_path.name}: <testcase name={raw_name!r}> thiếu thuộc tính classname")
				continue
			cases.append(
				{
					"classname": classname,
					"name": _base_method_name(raw_name),
					"raw_name": raw_name,
					"status": _status_of(case),
					"source": xml_path.name,
				}
			)
	if not testsuites:
		problems.append(f"{xml_path.name}: không tìm thấy <testsuite> nào")
	return cases, problems


class Inventory:
	"""Index tra cứu của inventory: theo module và theo (module, class)."""

	def __init__(self, modules: dict[str, list[str]]) -> None:
		self.modules = modules
		self.by_class: dict[tuple[str, str], set[str]] = {}
		self.by_module_class: dict[tuple[str, str], str] = {}
		for module, methods in modules.items():
			for dotted in methods:
				class_name, _, method_name = dotted.partition(".")
				self.by_class.setdefault((module, class_name), set()).add(method_name)
				self.by_module_class[(module, class_name, method_name)] = dotted

	def known_methods(self, module: str, class_name: str) -> set[str]:
		return self.by_class.get((module, class_name), set())

	def contains(self, module: str, class_name: str, method_name: str) -> bool:
		return (module, class_name, method_name) in self.by_module_class


def evaluate(
	inventory_modules: dict[str, list[str]],
	xml_dir: Path,
	allow_unexpected: set[tuple[str, str, str]],
) -> dict[str, object]:
	"""Đánh giá toàn bộ XML trong `xml_dir` so với inventory. Trả về report dict."""
	inventory = Inventory(inventory_modules)
	structural_problems: list[str] = []
	unexpected: list[dict[str, str]] = []
	observed: dict[tuple[str, str, str], dict[str, str]] = {}
	duplicates: list[str] = []
	xml_files = sorted(xml_dir.glob("*.junit.xml"))
	bound_modules: dict[str, str] = {}
	empty_xml: list[str] = []

	if not xml_files:
		structural_problems.append(f"Không tìm thấy file *.junit.xml nào trong {xml_dir}")

	for xml_path in xml_files:
		module, reason = bind_xml_to_module(xml_path, inventory_modules)
		if module is None:
			structural_problems.append(f"{xml_path.name}: {reason}")
			continue
		bound_modules[xml_path.name] = module
		# Một module phải đúng 1 file XML. Hai file cho cùng module (re-run để lấy
		# bằng chứng chẩn đoán) không được âm thầm gộp thành "nhiều PASS".
		for other_name, other_module in bound_modules.items():
			if other_name != xml_path.name and other_module == module:
				structural_problems.append(
					f"{xml_path.name}: module '{module}' đã có file '{other_name}' — duplicate XML không hợp lệ"
				)

		cases, problems = collect_from_xml(xml_path)
		structural_problems.extend(problems)
		if not problems and not cases:
			empty_xml.append(xml_path.name)

		for case in cases:
			classname = case["classname"]
			declared_module = module_of_classname(classname)
			# `classname` thường đã chứa module (`mod.Class`); nếu không thì
			# `declared_module` rỗng và ta dùng module từ tên file.
			effective_module = declared_module or module
			class_name = classname.rpartition(".")[2]

			if declared_module and declared_module != module:
				structural_problems.append(
					f"{xml_path.name}: <testcase classname={classname!r}> thuộc module "
					f"'{declared_module}' nhưng file này được gắn cho module '{module}'"
				)
				continue

			known = inventory.known_methods(effective_module, class_name)
			method_name, reason = _resolve_method_name(case["raw_name"], known)
			if method_name is None:
				unexpected.append(
					{
						"xml": xml_path.name,
						"module": effective_module,
						"class": class_name,
						"raw_name": case["raw_name"],
						"reason": reason,
						"allowed": "",
					}
				)
				continue

			# Case khớp inventory nhưng module không nằm trong inventory ⇒
			# không thể nằm trong `expected_keys`, coi như unexpected.
			if not inventory.contains(effective_module, class_name, method_name):
				unexpected.append(
					{
						"xml": xml_path.name,
						"module": effective_module,
						"class": class_name,
						"raw_name": case["raw_name"],
						"reason": "matched-naming-but-not-in-inventory",
						"allowed": "",
					}
				)
				continue

			case["name"] = method_name
			case["module"] = effective_module
			case["class"] = class_name
			case["match_reason"] = reason
			key = (effective_module, class_name, method_name)
			previous = observed.get(key)
			if previous is not None:
				duplicates.append(f"{effective_module}::{class_name}::{method_name}")
			# Giữ lần trạng thái "nặng" nhất — không được che failure bằng lần PASS.
			if previous is None or SEVERITY[case["status"]] > SEVERITY[previous["status"]]:
				observed[key] = case

	# `unexpected_cases` phải phản ánh case không khớp và làm gate fail, trừ khi
	# có allowlist cụ thể được giải thích.
	unexpected_final: list[dict[str, str]] = []
	for item in unexpected:
		allow_key = (item["module"], item["class"], _base_method_name(item["raw_name"]))
		allowed = allow_key in allow_unexpected
		entry = dict(item)
		entry["allowed"] = "yes" if allowed else "no"
		unexpected_final.append(entry)
		if not allowed:
			structural_problems.append(
				f"{item['xml']}: testcase ngoài inventory: {item['module']}::{item['class']}::"
				f"{item['raw_name']} ({item['reason']})"
			)

	results: list[dict[str, object]] = []
	missing: list[str] = []
	failed: list[str] = []
	errored: list[str] = []
	skipped: list[str] = []

	for module, methods in inventory_modules.items():
		for dotted in methods:
			class_name, _, method_name = dotted.partition(".")
			case = observed.get((module, class_name, method_name))
			if case is None:
				status = STATUS_NOT_RUN
				missing.append(f"{module}::{dotted}")
			else:
				status = case["status"]
				if status == STATUS_FAIL:
					failed.append(f"{module}::{dotted}")
				elif status == STATUS_ERROR:
					errored.append(f"{module}::{dotted}")
				elif status == STATUS_SKIP:
					skipped.append(f"{module}::{dotted}")
			results.append(
				{
					"module": module,
					"class": class_name,
					"method": method_name,
					"status": status,
					"source_xml": case["source"] if case else None,
					# Tên thô trong XML — giữ lại để đối chiếu khi method có subTest
					# (`test_x [msg] (params)`) vì inventory chỉ ghi tên method thuần.
					"raw_name": case["raw_name"] if case else None,
					# Cách ánh xạ tên thô → tên method chuẩn: exact | prefix | fallback.
					"match_reason": case.get("match_reason") if case else None,
				}
			)

	total = len(results)
	passing = [row for row in results if row["status"] == STATUS_PASS]
	counts: dict[str, int] = {}
	for row in results:
		counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1

	# Module nào trong inventory không có file XML nào ⇒ toàn bộ method của nó
	# là NOT_RUN (đã phản ánh ở `missing`), nhưng ghi thêm để báo cáo rõ nguyên nhân.
	missing_xml_modules = sorted(
		module
		for module in inventory_modules
		if module not in set(bound_modules.values())
	)
	for module in missing_xml_modules:
		structural_problems.append(f"Không có file XML nào cho module '{module}'")

	# Gate chỉ PASS khi: đủ method, tất cả PASS, XML parse sạch, không case lạ.
	gate_passed = (
		total > 0
		and not missing
		and not failed
		and not errored
		and not skipped
		and not structural_problems
		and not empty_xml
	)

	report: dict[str, object] = {
		"schema_version": 2,
		"gate": "g007-per-method-junit",
		"gate_passed": gate_passed,
		"expected_total": total,
		"observed_total": len(observed),
		"passing_total": len(passing),
		"counts": counts,
		"xml_files": [p.name for p in xml_files],
		"xml_module_binding": bound_modules,
		"missing_modules": missing_xml_modules,
		"empty_xml_files": empty_xml,
		"missing_methods": missing,
		"failed_methods": failed,
		"errored_methods": errored,
		"skipped_methods": skipped,
		"duplicate_methods": sorted(set(duplicates)),
		"unexpected_cases": unexpected_final,
		"parse_errors": structural_problems,
		"results": results,
	}
	return report


def main(argv: list[str] | None = None) -> int:
	args = parse_args(argv)
	inventory = load_inventory(Path(args.inventory))
	allow_unexpected: set[tuple[str, str, str]] = set()
	for entry in args.allow_unexpected:
		parts = entry.split("::")
		if len(parts) != 3 or not all(parts):
			raise SystemExit(f"--allow-unexpected phải có dạng MODULE::CLASS::METHOD, nhận: {entry!r}")
		allow_unexpected.add((parts[0], parts[1], parts[2]))

	report = evaluate(inventory, Path(args.xml_dir), allow_unexpected)
	gate_passed = bool(report["gate_passed"])

	out_path = Path(args.out)
	out_path.parent.mkdir(parents=True, exist_ok=True)
	out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

	print(f"g007-per-method-gate: {'PASS' if gate_passed else 'FAIL'}")
	print(f"  expected_total : {report['expected_total']}")
	print(f"  observed_total : {report['observed_total']}")
	print(f"  passing_total  : {report['passing_total']}")
	counts = report["counts"]
	assert isinstance(counts, dict)
	for status in sorted(counts):
		print(f"  {status:<8}: {counts[status]}")

	for label, key in (
		("NOT RUN", "missing_methods"),
		("FAIL", "failed_methods"),
		("ERROR", "errored_methods"),
		("SKIP", "skipped_methods"),
		("DUPLICATE", "duplicate_methods"),
		("EMPTY XML", "empty_xml_files"),
		("UNEXPECTED", "unexpected_cases"),
		("PARSE/STRUCTURE", "parse_errors"),
	):
		items = report[key]
		if not items:
			continue
		print(f"  {label} ({len(items)}):")  # type: ignore[arg-type]
		for item in items:  # type: ignore[union-attr]
			if isinstance(item, dict):
				print(
					f"    - {item['module']}::{item['class']}::{item['raw_name']} "
					f"[{item['reason']}, allowed={item['allowed']}]"
				)
			else:
				print(f"    - {item}")

	if not gate_passed and args.allow_incomplete:
		print("  (--allow-incomplete: gate fail nhưng exit 0 để thu bằng chứng chẩn đoán)")
		return 0
	return 0 if gate_passed else 1


if __name__ == "__main__":
	try:
		# Console CI có thể dùng encoding cp1252 ⇒ ép UTF-8 để in được tiếng Việt.
		if hasattr(sys.stdout, "reconfigure"):
			sys.stdout.reconfigure(encoding="utf-8", errors="replace")
			sys.stderr.reconfigure(encoding="utf-8", errors="replace")
		sys.exit(main())
	except SystemExit:
		raise
	except Exception as error:  # gate phải luôn ghi được artifact chẩn đoán
		print(f"g007-per-method-gate: loi khong luong truoc: {type(error).__name__}: {error}")
		sys.exit(1)