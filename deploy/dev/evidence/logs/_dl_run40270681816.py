"""Download + verify digest cho CI run 40270681816 (run_number 37).

Run 37 = commit 05cdaaa (docs-only: them run 36 vao bao cao).
Muc dich: xac nhan **khong hoi quy** — probe khong doi, gate/probe phai
giong het run 36.

Usage: python _dl_run40270681816.py
"""

import hashlib
import io
import json
import pathlib
import subprocess
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8")

RUN = "40270681816"
OUT = pathlib.Path("deploy/dev/evidence/logs/run%s" % RUN)

arts = json.loads(
    subprocess.run(
        ["gh", "api", "repos/nobita6986/new-crm/actions/runs/%s/artifacts" % RUN],
        capture_output=True,
    ).stdout
).get("artifacts", [])
print("artifacts found:", len(arts))
a = arts[0]
ART_ID = a["id"]

blob = subprocess.run(
    ["gh", "api", "repos/nobita6986/new-crm/actions/artifacts/%s/zip" % ART_ID],
    capture_output=True,
).stdout
local_sha = hashlib.sha256(blob).hexdigest()
meta = json.loads(
    subprocess.run(
        ["gh", "api", "repos/nobita6986/new-crm/actions/artifacts/%s" % ART_ID],
        capture_output=True,
    ).stdout
)
print("run id            :", RUN)
print("artifact id/name  :", meta.get("id"), meta.get("name"))
print("bytes             :", len(blob))
print("local sha256      :", local_sha)
print("github digest     :", meta.get("digest"))
print("digest match      :", meta.get("digest") == "sha256:" + local_sha)
print("size match        :", meta.get("size_in_bytes") == len(blob))
print("expired           :", meta.get("expired"))

OUT.mkdir(parents=True, exist_ok=True)
zdir = OUT / ("p0-g007-evidence-%s" % RUN)
zdir.mkdir(parents=True, exist_ok=True)
zf = zipfile.ZipFile(io.BytesIO(blob))
zf.extractall(zdir)
print("extracted ->", zdir)
print()
for n in sorted(zf.namelist()):
    if n.endswith("/"):
        continue
    print("  %-46s %s" % (n, hashlib.sha256(zf.read(n)).hexdigest()))
