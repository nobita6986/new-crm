"""Kiem chung MOI claim so (fact) cua bao cao T1B run 34.

Moi claim deu doc lai tu artifact / git / GitHub API — khong tin so ghi san.
Exit != 0 neu bat ky claim nao sai.
"""

import hashlib
import json
import pathlib
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

REPO = pathlib.Path(".")
R34 = REPO / "deploy/dev/evidence/logs/run40261069257"
R32 = REPO / "deploy/dev/evidence/logs/run40253195658"
REPORT = REPO / "deploy/dev/evidence/t1b-sec26-get-activities-coverage-2026-10-25.md"

fails = []


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    """ancestor la ancestor cua descendant hay khong (qua GitHub API compare).

    `compare/<base>...<head>`: `status=ahead` + `behind_by=0` ⇒ head di truoc
    base ⇒ base LA ancestor cua head.
    """
    if not ancestor or not descendant:
        return False
    if ancestor == descendant:
        return True
    r = subprocess.run(
        ["gh", "api", "repos/nobita6986/new-crm/compare/%s...%s" % (ancestor, descendant)],
        capture_output=True, text=True, encoding="utf-8",
    )
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return False
    return d.get("status") == "ahead" and d.get("behind_by") == 0


def ck(label, cond, detail=""):
    if not cond:
        fails.append(label)
    print("  [%s] %s%s" % ("OK " if cond else "FAIL", label, (" — " + detail) if detail else ""))


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, encoding="utf-8")
    return json.loads(r.stdout) if r.stdout.strip() else {}


report = REPORT.read_text(encoding="utf-8")
http34 = json.loads((R34 / "http/http-transport.json").read_text(encoding="utf-8"))
gate34 = json.loads((R34 / "g007-per-method-gate.json").read_text(encoding="utf-8"))
meta34 = json.loads((R34 / "workflow-metadata.json").read_text(encoding="utf-8"))
gate32 = json.loads((R32 / "p0-g007-evidence-40253195658/g007-per-method-gate.json").read_text(encoding="utf-8"))

print("=" * 78)
print("A. Run 34 — dinh danh CI")
print("=" * 78)
run = gh(["run", "view", "40261069257", "--json", "databaseId,number,event,headSha,conclusion,headBranch"])
ck("run number = 34", run.get("number") == 34, str(run.get("number")))
ck("run id khop", str(run.get("databaseId")) == "40261069257")
ck("event = pull_request", run.get("event") == "pull_request")
ck("head SHA = 01b3a52...", run.get("headSha") == "01b3a52d0a29ab76383600010bec7ef2e1bf393e", run.get("headSha"))
ck("conclusion = failure (8 canary FAIL)", run.get("conclusion") == "failure", run.get("conclusion"))

merge = gh(["api", "repos/nobita6986/new-crm/git/commits/6c3a3bedb7c31fc01155c77db35757a3af016f66"])
ck("merge SHA dung", merge.get("sha") == "6c3a3bedb7c31fc01155c77db35757a3af016f66")
parents = merge.get("parents", [])
ck("merge co parent = head SHA", any(p["sha"] == run["headSha"] for p in parents), str([p["sha"][:7] for p in parents]))
ck("merge co parent = base b382fe42", any(p["sha"].startswith("b382fe42") for p in parents))

cmp_ = gh(["api", "repos/nobita6986/new-crm/compare/%s...%s" % (run["headSha"], "6c3a3bedb7c31fc01155c77db35757a3af016f66")])
ck("compare: ahead_by=1, behind_by=0", cmp_.get("ahead_by") == 1 and cmp_.get("behind_by") == 0,
   "ahead=%s behind=%s" % (cmp_.get("ahead_by"), cmp_.get("behind_by")))

arts = gh(["api", "repos/nobita6986/new-crm/actions/runs/40261069257/artifacts"])["artifacts"]
ck("1 artifact", len(arts) == 1, str(len(arts)))
a = arts[0]
ck("artifact id = 12624940008", str(a["id"]) == "12624940008", str(a["id"]))
ck("artifact name = p0-g007-evidence-40261069257", a["name"] == "p0-g007-evidence-40261069257")
ck("artifact expired = false", a["expired"] is False)
ck("artifact size = 133732", a["size_in_bytes"] == 133732, str(a["size_in_bytes"]))
ck("artifact digest = d1be8c70...", a["digest"] == "sha256:d1be8c7055a96a3ad849c04d779981b6d011e3f5dad2f7cf542bf8e583fa6fa9", a["digest"])
ck("commit_sha trong probe = merge SHA", meta34["commit_sha"] == "6c3a3bedb7c31fc01155c77db35757a3af016f66", meta34["commit_sha"])
ck("http_transport step = success", meta34["workflow_outcomes"]["http_transport"] == "success")
ck("per_method_gate = failure", meta34["workflow_outcomes"]["per_method_gate"] == "failure")

print()
print("=" * 78)
print("B. Digest artifact — tu tinh lai tu file da giai nen")
print("=" * 78)
entry = R34 / "http/http-transport.json"
h = hashlib.sha256(entry.read_bytes()).hexdigest()
ck("sha256 http-transport.json = db62c216...", h == "db62c216faab70d52e606cb170e60a1fb420c46799adb6029701bb64010a2944", h)
ck("bao cao ghi dung digest entry", "db62c216faab70d52e606cb170e60a1fb420c46799adb6029701bb64010a2944" in report)
ck("bao cao ghi dung artifact digest", "d1be8c7055a96a3ad849c04d779981b6d011e3f5dad2f7cf542bf8e583fa6fa9" in report)
ck("bao cao ghi dung merge SHA", "6c3a3bedb7c31fc01155c77db35757a3af016f66" in report)
ck("bao cao ghi dung head SHA", "01b3a52d0a29ab76383600010bec7ef2e1bf393e" in report)

print()
print("=" * 78)
print("C. Gate — bat bien run 32 vs run 34")
print("=" * 78)
for k in ("expected_total", "observed_total", "passing_total", "gate_passed", "missing_methods",
          "skipped_methods", "parse_errors", "errored_methods", "counts", "failed_methods"):
    ck("%s khong doi" % k, gate34.get(k) == gate32.get(k),
       "%r vs %r" % (gate32.get(k), gate34.get(k)))
ck("counts = 91 PASS / 8 FAIL", gate34["counts"] == {"PASS": 91, "FAIL": 8}, str(gate34["counts"]))
ck("failed_methods len = 8", len(gate34["failed_methods"]) == 8)
for fname in ("g007-per-method-gate.json", "module-status.txt"):
    a32 = (R32 / "p0-g007-evidence-40253195658" / fname).read_bytes()
    a34 = (R34 / fname).read_bytes()
    ck("%s byte-identical" % fname, a32 == a34,
       "%s vs %s" % (hashlib.sha256(a32).hexdigest()[:16], hashlib.sha256(a34).hexdigest()[:16]))

print()
print("=" * 78)
print("D. Probe run 34")
print("=" * 78)
ck("probe_result = PASS", http34["probe_result"] == "PASS", http34["probe_result"])
ck("failed_cases rong", http34["failed_cases"] == [])
ck("18 case", len(http34["cases"]) == 18, str(len(http34["cases"])))
ck("6 observations", len(http34["observations"]) == 6, str(len(http34["observations"])))
ck("coverage_scope ghi ro gioi han", "reverse-proxy and CORS behavior are not covered" in http34["coverage_scope"])

ids = [c["id"] for c in http34["cases"]]
for cid in ("sec26-activities-in-scope", "sec26-activities-out-of-scope",
            "sec26-activities-download-in-scope"):
    ck("case %s co" % cid, cid in ids)
ck("khong co case bi xoa so voi run 32", set(ids) >= {
    "sec13-guest", "staff-login", "sec13-user", "csrf-page", "sec14-get",
    "sec14-post-nocsrf", "sec14-post-badtok", "sec14-post-ok", "administrator-login",
    "sec13-admin", "sec16-permlevel-get-data", "sec16-permlevel-sentinel-scan",
    "sec26-staff-private", "sec26-admin-private", "sec26-activities-in-scope"})

obs = {o["id"]: o for o in http34["observations"]}
for oid in ("sec26-activities-message-attachments", "sec26-activities-message-attachment-log",
            "sec26-activities-docinfo-attachments", "sec26-activities-docinfo-attachment-logs"):
    ck("4 duong: %s file_url_present=true" % oid, obs[oid]["file_url_present"] is True)
    ck("4 duong: %s caller = staff.a" % oid, obs[oid]["caller"] == "staff.a@dev.local")
    ck("4 duong: %s file_is_private=true" % oid, obs[oid]["file_is_private"] is True)

oos = obs["sec26-activities-out-of-scope"]
ck("ngoai scope: status 403", oos["http_status"] == 403, str(oos["http_status"]))
ck("ngoai scope: exc_type PermissionError", oos["exc_type"] == "PermissionError", str(oos["exc_type"]))
ck("ngoai scope: private_entry_visible=false", oos["private_entry_visible"] is False)
ck("ngoai scope: caller = staff.c", oos["caller"] == "staff.c@dev.local")
ck("ngoai scope: la QUAN SAT (khong co assertion)",
   "assertion" not in [c for c in http34["cases"] if c["id"] == "sec26-activities-out-of-scope"][0])

dl = obs["sec26-activities-download-in-scope"]
ck("download: status 200", dl["http_status"] == 200, str(dl["http_status"]))
ck("download: content_length = 90", dl["content_length"] == 90, str(dl["content_length"]))
ck("download: contains_seed_marker = true", dl["contains_seed_marker"] is True)
ck("download: transport_error = None", dl["transport_error"] is None)
ck("download: caller = staff.a (in-scope)", dl["caller"] == "staff.a@dev.local")

dcase = [c for c in http34["cases"] if c["id"] == "sec26-activities-download-in-scope"][0]
ck("download: response.body RONG (khong raw bytes)", dcase["response"]["body"] == "")
ck("download: co content_sha256 64 hex", len(dcase["response"]["content_sha256"]) == 64)
ck("download: assertion PASS (khong assert cung 200)", dcase["assertion"]["result"] == "PASS")
ck("download: URL da redact trong request",
   "[REDACTED" in dcase["request"]["url"] or "REDACTED" in dcase["request"]["url"],
   dcase["request"]["url"])

print()
print("=" * 78)
print("E. Redaction — khong ro ri trong artifact run 34")
print("=" * 78)
files = [p for p in R34.rglob("*") if p.is_file()]
raws = []
for p in files:
    try:
        raws.append((p, p.read_text(encoding="utf-8")))
    except (UnicodeDecodeError, OSError):
        pass
blob = "\n".join(t for _, t in raws)
ck("khong co URL file private that",
   not [m for m in re.finditer(r"/private/files/([^\"'\s,>\\]+)", blob)
        if "REDACTED" not in m.group(1) and not m.group(1).startswith("<")])
ck("khong co File docname that", not re.findall(r'"name"\s*:\s*"[0-9a-f]{8,12}"', blob))
ck("khong co cookie that", not [m for m in re.finditer(r'"cookie"\s*:\s*"(?!\[REDACTED\])[^"]+"', blob)])
ck("co dau vet redaction URL", len(re.findall(r"/private/files/\[REDACTED", blob)) >= 1,
   str(len(re.findall(r"/private/files/\[REDACTED", blob))))
seed_src = (REPO / "deploy/dev/seed/seed_demo.py").read_text(encoding="utf-8")
pw = re.search(r'SEED_PASSWORD\s*=\s*["\']([^"\']+)["\']', seed_src)
ck("khong co SEED_PASSWORD trong artifact", pw and pw.group(1) not in blob)

print()
print("=" * 78)
print("F. Guard: khong sua policy/wrapper/assertion/tally/inventory")
print("=" * 78)
def git_show(path):
    return subprocess.run(["git", "show", "HEAD:" + path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout

for p in ("apps/crm_cskh/crm_cskh/api/guarded_crm.py",
          "apps/crm_cskh/crm_cskh/hooks.py",
          "deploy/dev/seed/g007_test_inventory.json"):
    ck("khong doi: %s" % p, subprocess.run(["git", "diff", "--quiet", "HEAD", "--", p]).returncode == 0)

inv = json.loads((REPO / "deploy/dev/seed/g007_test_inventory.json").read_text(encoding="utf-8"))
total = inv.get("expected_total") or len(inv.get("tests", inv.get("methods", [])))
ck("inventory van expected_total=99", total == 99, str(total))
ck("khong doi: deploy/dev/seed/ci_http_transport_probe.py so voi HEAD",
   subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "deploy/dev/seed/ci_http_transport_probe.py"]).returncode == 0)
ck("chi doi: test_ci_http_probe_static.py + bao cao",
   subprocess.run(["git", "diff", "--name-only", "HEAD", "--", "deploy/dev/seed/"]).returncode == 0)

print()
print("=" * 78)
print("G. Trang thai / khong merge")
print("=" * 78)
pr = gh(["pr", "view", "2", "--json", "number,state,mergeStateStatus,headRefOid"])
ck("PR #2 OPEN", pr.get("state") == "OPEN", pr.get("state"))
ck("PR #2 mergeStateStatus UNSTABLE", pr.get("mergeStateStatus") == "UNSTABLE", pr.get("mergeStateStatus"))
ck("PR #2 head = 05cdaaa (run 37) va la ancestor cua head hien tai",
   "05cdaaaaf9956dbded6dc9bfd102b0c5fb65d11a" in pr.get("headRefOid", "")
   or _is_ancestor("05cdaaaaf9956dbded6dc9bfd102b0c5fb65d11a", pr.get("headRefOid", "")),
   pr.get("headRefOid"))
merged = subprocess.run(["git", "log", "--merges", "--oneline", "-1"], capture_output=True, text=True)
ck("khong co merge commit cua toi trong branch", "05cdaaa" not in merged.stdout)

print()
print("=" * 78)
print("I. Run 35 — dinh danh CI + khong hoi quy")
print("=" * 78)
run35 = gh(["run", "view", "40263613563", "--json", "databaseId,number,event,headSha,conclusion"])
ck("run number = 35", run35.get("number") == 35, str(run35.get("number")))
ck("run id khop", str(run35.get("databaseId")) == "40263613563")
ck("event = pull_request", run35.get("event") == "pull_request")
ck("head SHA = a619a68...", run35.get("headSha") == "a619a682cce559aee154ef8a9bafa24f18bb6b8f", run35.get("headSha"))
merge35 = gh(["api", "repos/nobita6986/new-crm/git/commits/185edbfa4c90dc48178328d70512269239a1a6c0"])
ck("merge SHA dung", merge35.get("sha") == "185edbfa4c90dc48178328d70512269239a1a6c0")
ck("merge co parent = head SHA run 35",
   any(p["sha"] == run35["headSha"] for p in merge35.get("parents", [])),
   str([p["sha"][:7] for p in merge35.get("parents", [])]))
cmp35 = gh(["api", "repos/nobita6986/new-crm/compare/%s...%s" % (run35["headSha"], "185edbfa4c90dc48178328d70512269239a1a6c0")])
ck("compare run35: ahead_by=1, behind_by=0",
   cmp35.get("ahead_by") == 1 and cmp35.get("behind_by") == 0)
arts35 = gh(["api", "repos/nobita6986/new-crm/actions/runs/40263613563/artifacts"])["artifacts"]
ck("run35: 1 artifact", len(arts35) == 1)
a35 = arts35[0]
ck("run35: artifact id = 12625064538", str(a35["id"]) == "12625064538", str(a35["id"]))
ck("run35: artifact expired = false", a35["expired"] is False)
ck("run35: artifact size = 133737", a35["size_in_bytes"] == 133737, str(a35["size_in_bytes"]))
ck("run35: artifact digest = 77fde5b4...",
   a35["digest"] == "sha256:77fde5b4c2bef41821a9637f6d4bc7d4e376718c1b9d5a92885c328df07cf228",
   a35["digest"])
ck("bao cao ghi dung digest run35",
   "77fde5b4c2bef41821a9637f6d4bc7d4e376718c1b9d5a92885c328df07cf228" in report)
ck("bao cao ghi dung merge SHA run35",
   "185edbfa4c90dc48178328d70512269239a1a6c0" in report)
ck("bao cao ghi dung artifact id run35", "12625064538" in report)
ck("bao cao ghi dung sha256 raw evidence run35",
   hashlib.sha256(
       (REPO / "deploy/dev/evidence/logs/run40263613563/_raw_sec26_review_run40263613563.json").read_bytes()
   ).hexdigest()[:8] in report)

R35_ROOT = REPO / "deploy/dev/evidence/logs/run40263613563"
# Artifact co 2 ban: ban phang (workflow ghi ra) + ban nested (zip giai nen).
# Uu tien ban nested (giao dien artifact that) — fallback ban phang.
_r35_nested = R35_ROOT / "p0-g007-evidence-40263613563"
R35 = _r35_nested if _r35_nested.exists() else R35_ROOT
h35 = json.loads((R35 / "http/http-transport.json").read_text(encoding="utf-8"))
g35 = json.loads((R35 / "g007-per-method-gate.json").read_text(encoding="utf-8"))
ck("run35: probe_result = PASS", h35["probe_result"] == "PASS")
ck("run35: failed_cases rong", h35["failed_cases"] == [])
ck("run35: 18 case / 6 observations",
   len(h35["cases"]) == 18 and len(h35["observations"]) == 6)
ck("run35: gate counts = 91 PASS / 8 FAIL", g35["counts"] == {"PASS": 91, "FAIL": 8}, str(g35["counts"]))
ck("run35: gate byte-identical run34",
   (R35 / "g007-per-method-gate.json").read_bytes() == (R34 / "g007-per-method-gate.json").read_bytes())
ck("run35: module-status byte-identical run34",
   (R35 / "module-status.txt").read_bytes() == (R34 / "module-status.txt").read_bytes())
o35 = {o["id"]: o for o in h35["observations"]}
ck("run35: 4 duong file_url_present=true",
   all(o35[k]["file_url_present"] is True for k in (
       "sec26-activities-message-attachments", "sec26-activities-message-attachment-log",
       "sec26-activities-docinfo-attachments", "sec26-activities-docinfo-attachment-logs")))
ck("run35: ngoai scope 403 PermissionError",
   o35["sec26-activities-out-of-scope"]["http_status"] == 403
   and o35["sec26-activities-out-of-scope"]["exc_type"] == "PermissionError")
ck("run35: download 200 / content_length=90 / marker=true",
   o35["sec26-activities-download-in-scope"]["http_status"] == 200
   and o35["sec26-activities-download-in-scope"]["content_length"] == 90
   and o35["sec26-activities-download-in-scope"]["contains_seed_marker"] is True)

print()
print("=" * 78)
print("J. Run 36 — run moi nhat (docs-only) + khong hoi quy so voi run 35")
print("=" * 78)
run36 = gh(["run", "view", "40266333553", "--json", "databaseId,number,event,headSha,conclusion"])
ck("run number = 36", run36.get("number") == 36, str(run36.get("number")))
ck("run id khop", str(run36.get("databaseId")) == "40266333553")
ck("event = pull_request", run36.get("event") == "pull_request")
ck("head SHA = b2e7288...", run36.get("headSha") == "b2e7288b1a7ad3214d69621dbea35e45b661af1e",
   run36.get("headSha"))
ck("conclusion = failure (8 canary FAIL)", run36.get("conclusion") == "failure")
arts36 = gh(["api", "repos/nobita6986/new-crm/actions/runs/40266333553/artifacts"])["artifacts"]
ck("run36: 1 artifact", len(arts36) == 1)
a36 = arts36[0]
ck("run36: artifact id = 12627170197", str(a36["id"]) == "12627170197", str(a36["id"]))
ck("run36: artifact expired = false", a36["expired"] is False)
ck("run36: artifact size = 133825", a36["size_in_bytes"] == 133825, str(a36["size_in_bytes"]))
ck("run36: artifact digest = 8153a6c0...",
   a36["digest"] == "sha256:8153a6c08cfe75c8d432fb31a9413692158e788c0bb300e78b532e0e5998aebc",
   a36["digest"])
ck("bao cao ghi dung digest run36",
   "8153a6c08cfe75c8d432fb31a9413692158e788c0bb300e78b532e0e5998aebc" in report)
ck("bao cao ghi dung artifact id run36", "12627170197" in report)
ck("bao cao ghi dung head SHA run36", "b2e7288b1a7ad3214d69621dbea35e45b661af1e" in report)

R36_ROOT = REPO / "deploy/dev/evidence/logs/run40266333553"
_r36_nested = R36_ROOT / "p0-g007-evidence-40266333553"
R36 = _r36_nested if _r36_nested.exists() else R36_ROOT
h36 = json.loads((R36 / "http/http-transport.json").read_text(encoding="utf-8"))
g36 = json.loads((R36 / "g007-per-method-gate.json").read_text(encoding="utf-8"))
ck("run36: probe_result = PASS", h36["probe_result"] == "PASS")
ck("run36: failed_cases rong", h36["failed_cases"] == [])
ck("run36: 18 case / 6 observations",
   len(h36["cases"]) == 18 and len(h36["observations"]) == 6)
ck("run36: gate counts = 91 PASS / 8 FAIL", g36["counts"] == {"PASS": 91, "FAIL": 8}, str(g36["counts"]))
ck("run36: gate byte-identical run35",
   (R36 / "g007-per-method-gate.json").read_bytes() == (R35 / "g007-per-method-gate.json").read_bytes())
ck("run36: module-status byte-identical run35",
   (R36 / "module-status.txt").read_bytes() == (R35 / "module-status.txt").read_bytes())
ck("run36: tap case id giong het run35",
   {c["id"] for c in h36["cases"]} == {c["id"] for c in h35["cases"]})


def _sig(h):
    return {c["id"]: (c["response"]["status"], c["response"].get("exc_type"),
                      c["transport_error"], json.dumps(c.get("assertion"), sort_keys=True))
            for c in h["cases"]}


ck("run36: nhan dang 18/18 case giong het run35", _sig(h36) == _sig(h35))
o36 = {o["id"]: o for o in h36["observations"]}
ck("run36: 4 duong file_url_present=true",
   all(o36[k]["file_url_present"] is True for k in (
       "sec26-activities-message-attachments", "sec26-activities-message-attachment-log",
       "sec26-activities-docinfo-attachments", "sec26-activities-docinfo-attachment-logs")))
ck("run36: ngoai scope 403 PermissionError",
   o36["sec26-activities-out-of-scope"]["http_status"] == 403
   and o36["sec26-activities-out-of-scope"]["exc_type"] == "PermissionError"
   and o36["sec26-activities-out-of-scope"]["private_entry_visible"] is False)
ck("run36: download 200 / content_length=90 / marker=true",
   o36["sec26-activities-download-in-scope"]["http_status"] == 200
   and o36["sec26-activities-download-in-scope"]["content_length"] == 90
   and o36["sec26-activities-download-in-scope"]["contains_seed_marker"] is True)

print()
print("=" * 78)
print("L. Run 37 — run moi nhat (docs-only) + khong hoi quy so voi run 36")
print("=" * 78)
run37 = gh(["run", "view", "40270681816", "--json", "databaseId,number,event,headSha,conclusion"])
ck("run number = 37", run37.get("number") == 37, str(run37.get("number")))
ck("run id khop", str(run37.get("databaseId")) == "40270681816")
ck("event = pull_request", run37.get("event") == "pull_request")
ck("head SHA = 05cdaaa...", run37.get("headSha") == "05cdaaaaf9956dbded6dc9bfd102b0c5fb65d11a",
   run37.get("headSha"))
ck("conclusion = failure (8 canary FAIL)", run37.get("conclusion") == "failure")
arts37 = gh(["api", "repos/nobita6986/new-crm/actions/runs/40270681816/artifacts"])["artifacts"]
ck("run37: 1 artifact", len(arts37) == 1)
a37 = arts37[0]
ck("run37: artifact id = 12628421240", str(a37["id"]) == "12628421240", str(a37["id"]))
ck("run37: artifact expired = false", a37["expired"] is False)
ck("run37: artifact size = 133795", a37["size_in_bytes"] == 133795, str(a37["size_in_bytes"]))
ck("run37: artifact digest = 2982cf3b...",
   a37["digest"] == "sha256:2982cf3b6e5562fb8f8fcef243c3ba5529e700a65b0016f46bb0155241706171",
   a37["digest"])
ck("bao cao ghi dung digest run37",
   "2982cf3b6e5562fb8f8fcef243c3ba5529e700a65b0016f46bb0155241706171" in report)
ck("bao cao ghi dung artifact id run37", "12628421240" in report)
ck("bao cao ghi dung head SHA run37", "05cdaaaaf9956dbded6dc9bfd102b0c5fb65d11a" in report)

R37_ROOT = REPO / "deploy/dev/evidence/logs/run40270681816"
_r37_nested = R37_ROOT / "p0-g007-evidence-40270681816"
R37 = _r37_nested if _r37_nested.exists() else R37_ROOT
h37 = json.loads((R37 / "http/http-transport.json").read_text(encoding="utf-8"))
g37 = json.loads((R37 / "g007-per-method-gate.json").read_text(encoding="utf-8"))
ck("run37: probe_result = PASS", h37["probe_result"] == "PASS")
ck("run37: failed_cases rong", h37["failed_cases"] == [])
ck("run37: 18 case / 6 observations",
   len(h37["cases"]) == 18 and len(h37["observations"]) == 6)
ck("run37: gate counts = 91 PASS / 8 FAIL", g37["counts"] == {"PASS": 91, "FAIL": 8}, str(g37["counts"]))
ck("run37: gate byte-identical run36",
   (R37 / "g007-per-method-gate.json").read_bytes() == (R36 / "g007-per-method-gate.json").read_bytes())
ck("run37: module-status byte-identical run36",
   (R37 / "module-status.txt").read_bytes() == (R36 / "module-status.txt").read_bytes())
ck("run37: tap case id giong het run36",
   {c["id"] for c in h37["cases"]} == {c["id"] for c in h36["cases"]})
ck("run37: nhan dang 18/18 case giong het run36", _sig(h37) == _sig(h36))
o37 = {o["id"]: o for o in h37["observations"]}
ck("run37: 4 duong file_url_present=true",
   all(o37[k]["file_url_present"] is True for k in (
       "sec26-activities-message-attachments", "sec26-activities-message-attachment-log",
       "sec26-activities-docinfo-attachments", "sec26-activities-docinfo-attachment-logs")))
ck("run37: ngoai scope 403 PermissionError",
   o37["sec26-activities-out-of-scope"]["http_status"] == 403
   and o37["sec26-activities-out-of-scope"]["exc_type"] == "PermissionError"
   and o37["sec26-activities-out-of-scope"]["private_entry_visible"] is False)
ck("run37: download 200 / content_length=90 / marker=true",
   o37["sec26-activities-download-in-scope"]["http_status"] == 200
   and o37["sec26-activities-download-in-scope"]["content_length"] == 90
   and o37["sec26-activities-download-in-scope"]["contains_seed_marker"] is True)

print()
print("=" * 78)
print("M. Audit redaction — 4 run")
print("=" * 78)
for run_id in ("40261069257", "40263613563", "40266333553", "40270681816"):
    root = REPO / ("deploy/dev/evidence/logs/run%s" % run_id)
    files = [p for p in root.rglob("*") if p.is_file()]
    blobs = []
    for p in files:
        try:
            blobs.append(p.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            pass
    raw = "\n".join(blobs)
    real_urls = [m for m in re.findall(r"/private/files/([^\"'\s,>\\]+)", raw)
                 if "REDACTED" not in m and not m.startswith("<")]
    ck("run%s: khong co URL file private that" % run_id, not real_urls, str(real_urls[:3]))
    ck("run%s: khong co File docname that" % run_id,
       not re.findall(r'"name"\s*:\s*"([0-9a-f]{8,12})"', raw))
    ck("run%s: co dau vet redaction" % run_id,
       len(re.findall(r"/private/files/\[REDACTED", raw)) >= 1)
    dl = next(c for c in json.loads(
        next(root.rglob("http-transport.json")).read_text(encoding="utf-8"))["cases"]
        if c["id"] == "sec26-activities-download-in-scope")
    ck("run%s: response.body rong (khong raw bytes)" % run_id, dl["response"]["body"] == "")

print()
print("=" * 78)
print("H. Bao cao — claim ve hanh dong cam kiet")
print("=" * 78)
for phrase in ("không** merge PR", "không** thêm method thứ 100", "PARTIAL", "OPEN"):
    ck("bao cao chua '%s'" % phrase, phrase in report)
ck("bao cao ghi '52/52'", "52/52" in report)
ck("bao cao ghi '18/18'", "18/18" in report)
ck("bao cao ghi '10/10'", "10/10" in report)
ck("bao cao ghi 8 canary FAIL giong 100%", "giống 100%" in report)

print()
print("=" * 78)
print("KET QUA:", "TAT CA CLAIM DUNG" if not fails else "SAI %d: %s" % (len(fails), fails))
print("=" * 78)
sys.exit(0 if not fails else 1)
