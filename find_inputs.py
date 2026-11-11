import os, sys

sys.stdout.reconfigure(encoding='utf-8')
print("--- SEARCHING P4 WORKTREES ---")

targets = ['live-health-handoff.json', 'restore-fresh.json']

p4_dirs = ['p4-resume-artifacts', 'p4-resume-ops', 'p4-ops', 'p4-01-integration', 'p4-02-ci', 'p4-compose', 'tools', 't1c-scratch']

for p4 in p4_dirs:
    dir_path = os.path.join('D:/HRP-app', p4)
    if os.path.exists(dir_path):
        for root, dirs, files in os.walk(dir_path):
            if '.git' in dirs: dirs.remove('.git')
            if 'node_modules' in dirs: dirs.remove('node_modules')
            if 'apps' in dirs: dirs.remove('apps')
            for f in files:
                if f in targets or 'handoff' in f.lower() or 'restore-fresh' in f.lower() or 'restore' in f.lower() and f.endswith('.json'):
                    print("FOUND:", os.path.join(root, f))
print("--- FINISHED ---")
