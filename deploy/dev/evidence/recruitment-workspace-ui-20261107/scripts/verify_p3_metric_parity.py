#!/usr/bin/env python3
"""Static parity check across P3 backend `METRIC_LABELS_VI`, UI `p3-api.js`, and `translations/vi.csv`."""

from __future__ import annotations

import ast
import csv
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
KPI_PY = REPO / "apps" / "crm_cskh" / "crm_cskh" / "services" / "kpi_calculator.py"
SCORE_COMP_RE = re.compile(r"SCORE_COMPONENT_LABELS_VI:\s*dict\[str,\s*str\]\s*=\s*(\{[^}]+\})", re.S)
METRIC_RE = re.compile(r"METRIC_LABELS_VI:\s*dict\[str,\s*str\]\s*=\s*(\{[^}]+\})", re.S)
VI_CSV = REPO / "apps" / "crm_cskh" / "crm_cskh" / "translations" / "vi.csv"
P3_API_JS = REPO / "apps" / "crm_cskh" / "crm_cskh" / "public" / "cskh" / "p3-api.js"


def main() -> int:
	kpi_src = KPI_PY.read_text(encoding="utf-8")
	m_metric = METRIC_RE.search(kpi_src)
	m_comp = SCORE_COMP_RE.search(kpi_src)
	if not m_metric or not m_comp:
		raise RuntimeError("Could not parse METRIC_LABELS_VI / SCORE_COMPONENT_LABELS_VI from kpi_calculator.py")

	metric_labels_vi: dict[str, str] = ast.literal_eval(m_metric.group(1))
	comp_labels_vi: dict[str, str] = ast.literal_eval(m_comp.group(1))

	vi_map: dict[str, str] = {}
	with VI_CSV.open(encoding="utf-8", newline="") as handle:
		for row in csv.reader(handle):
			if len(row) >= 2 and row[0] not in vi_map:
				vi_map[row[0]] = row[1]

	node_script = (
		"const fs = require('fs');\n"
		"const vm = require('vm');\n"
		"const ctx = { window: { cskh: { t: s => s } } };\n"
		"ctx.window = ctx;\n"
		"vm.createContext(ctx);\n"
		f"vm.runInContext(fs.readFileSync({json.dumps(str(P3_API_JS))}, 'utf8'), ctx);\n"
		"const out = {};\n"
		f"for (const k of Object.keys({json.dumps(metric_labels_vi)})) {{\n"
		"  const norm = ctx.cskhP3Api.normalizeMetric(k, { value: 1 }, {});\n"
		"  out[k] = norm.label;\n"
		"}\n"
		"console.log(JSON.stringify(out));\n"
	)
	catalog_labels: dict[str, str] = json.loads(
		subprocess.check_output(["node", "-e", node_script], encoding="utf-8")
	)

	mismatches: list[tuple[str, str | None, str | None, str]] = []
	for code, expected_vi in metric_labels_vi.items():
		en_key = catalog_labels.get(code)
		actual_vi = vi_map.get(en_key or "")
		if actual_vi != expected_vi:
			mismatches.append((code, en_key, actual_vi, expected_vi))

	print(
		f"P3 Metric Parity Audit: checked {len(metric_labels_vi)} metrics & {len(comp_labels_vi)} score components "
		f"| mismatches={len(mismatches)}"
	)
	for code, en_key, actual_vi, expected_vi in mismatches:
		print(f"  MISMATCH {code}: en={en_key!r} vi.csv={actual_vi!r} != backend={expected_vi!r}")

	if mismatches:
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
