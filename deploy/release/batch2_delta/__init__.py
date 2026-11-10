"""P4 Batch 2 (Đợt 2) T1C-gap delta verification package.

Operates strictly against the locked Đợt 1 GHCR digest without mutating
`apps/**`, `deploy/images/**`, or `T1A`-owned `deploy/compose/**` / `deploy/ops/**`.
"""

LOCKED_BATCH1_COMMIT_SHA = "427af4834eda48a15ca86c15e88342c3c40d5f2f"
LOCKED_BATCH1_IMAGE_DIGEST = (
    "ghcr.io/nobita6986/crm-cskh@sha256:"
    "b1329ef7c26a9861a07d25d425eb4438150d065d2548c464e7d3395e568a201d"
)
LOCKED_BATCH1_OCI_CONFIG_DIGEST = (
    "sha256:9f295546fc78c061d950322b9c8f60d40312016b802f325b15f834524f500829"
)
BASELINE_MAIN_COMMIT_SHA = "467830195358b2431ac772486530ab935887a518"
GOLDEN_DATASET_EXPECTED_SHA256 = (
    "deb6be25a55a44deee4dcd21f9ee4fdada2ed38dfd2b14800c8cc1d375c49bdf"
)
