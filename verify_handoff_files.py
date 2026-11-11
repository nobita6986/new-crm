import json, hashlib, os, sys

sys.stdout.reconfigure(encoding='utf-8')
manifest_path = r'D:\HRP-app\p4-resume-artifacts\vps-handoff-20261109\handoff-manifest.json'
with open(manifest_path, 'r', encoding='utf-8') as fp:
    manifest = json.load(fp)

base_dir = os.path.dirname(manifest_path)
print("=== VERIFYING HANDOFF MANIFEST HASHES ===")
print("VPS Source:", manifest.get('vps_source'))
print("Backup Source:", manifest.get('backup_source'))
all_pass = True

for item in manifest['files']:
    file_path = os.path.join(base_dir, item['name'])
    with open(file_path, 'rb') as fp:
        actual_bytes = fp.read()
    actual_sha = hashlib.sha256(actual_bytes).hexdigest()
    actual_len = len(actual_bytes)
    match_sha = (actual_sha == item['sha256'])
    match_len = (actual_len == item['bytes'])
    if not (match_sha and match_len):
        all_pass = False
    print(f"File: {item['name']}")
    print(f"  Bytes: {actual_len} (expected: {item['bytes']}) - Match: {match_len}")
    print(f"  SHA256: {actual_sha} (expected: {item['sha256']}) - Match: {match_sha}")

print("Overall Hash Verification:", "ALL 5/5 MATCH 100% PASS" if all_pass else "MISMATCH FAIL")
