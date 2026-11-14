# G18 weighted distribution diagnostic

## Why this diagnostic exists

The approved G18 rule BG-01 is unchanged: 10,000 Work Items must be created within
1,200,000 ms, with zero duplicates, successful reconciliation, and concurrent worklist
reads below 500 ms. A diagnostic run is never an acceptance run and must not be used to
mark G18 PASS.

The latest canonical 4x4 report recorded 4,500 of 10,000 Work Items at the 1,200 second
poll deadline, with the worker still running; a later observation showed 5,500 while
the same worker remained active. An earlier run of the same candidate completed all
10,000 in 1,137,210.98 ms. This variation needs query-level evidence before changing
application code or thresholds.

## Isolated diagnostic mode

Run only on an isolated host with no Owner/production workload. The command requires
an immutable image digest and a successful host headroom check. It creates its own
cskh-p4-bench-* Compose project and removes only that project's containers, volumes,
and networks in finally.

Run this from the repository root:

    python deploy/release/benchmark_p4_isolated.py --provision-and-run-s1 --confirm-isolated-target --distribution-diagnostic-only --require-live-headroom --resource-profile s1_canonical_4x4 --app-image ghcr.io/nobita6986/crm-cskh@sha256:<approved-immutable-digest> --diagnostic-poll-seconds 7200 --out deploy/ops/evidence/g18-distribution-diagnostic.json

This mode uses the ordinary S1 fixture and queue-long worker. It samples worker progress
and MariaDB Performance Schema statement digests every 30 seconds. The report contains
SHA-256 fingerprints of normalized SQL shapes, SQL verb and table names, call counts,
elapsed database time, examined/sent row deltas, and selected global status deltas.
It does not write query text, bind values, credentials, lead identifiers, or worker IDs.
If Performance Schema is unavailable, the report marks that telemetry unavailable and
still records progress.

The report status is always DIAGNOSTIC_ONLY_NOT_ACCEPTANCE; its G18 verdict is
NOT_EVALUATED. After using the diagnostic to identify a fix, rebuild and test the
candidate, then run the unmodified official S1 acceptance command against the exact
candidate digest. Do not adjust the approved SLO thresholds or infer acceptance from
the diagnostic.
