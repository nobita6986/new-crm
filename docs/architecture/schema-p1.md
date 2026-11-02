# Schema P1 — 8 DocType, constraint và index

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/schema-p1.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Đã hiện thực và verify trên DB cô lập** (`p1c.localhost`)
> **Tài liệu liên quan:** [`../root-plan/P1.md`](../root-plan/P1.md) §5, [`api-contract-p1.md`](./api-contract-p1.md), [`adr-p1-01-identity-index.md`](./adr-p1-01-identity-index.md), [`adr-p1-02-active-claim.md`](./adr-p1-02-active-claim.md)

---

## 1. Tổng quan

P1 thêm **8 DocType** mới, tất cả đặt tên `CSKH *`, không đụng core CRM
(ngoại trừ **một** custom field nghiệp vụ trên `CRM Lead`: `cskh_is_placeholder_name`).

| DocType | Mục đích | Autoname | Ghi chú |
|---|---|---|---|
| `CSKH Campaign` | Chiến dịch phân phối | hash | `code` unique |
| `CSKH Assignment Rule` | Trọng số + capacity + version của 1 assignee | hash | unique `(campaign,user,version)` |
| `CSKH Work Item` | 1 lead × 1 campaign = 1 công việc | hash | unique `(campaign,lead)` |
| `CSKH Assignment History` | Audit append-only ownership | hash | `event_id` unique |
| `CSKH Import Batch` | 1 lần import (preview → commit) | hash | `idempotency_key` unique |
| `CSKH Distribution Run` | 1 lần phân phối (preview + state) | hash | `idempotency_key` unique |
| `CSKH Identity Index` | `normalized_phone` → `canonical_lead` | hash | 2 unique index |
| `CSKH Active Claim` | Chống 1 lead active ở 2 campaign | hash | unique `lead`, unique `work_item` |

Tất cả dùng engine **InnoDB** (cần transaction + row lock).

---

## 2. `CSKH Campaign`

| Field | Type | Options | Reqd | Unique | Default |
|---|---|---|---|---|---|
| `code` | Data | — | ✔ | ✔ | — |
| `title` | Data | — | ✔ | | — |
| `description` | Small Text | — | | | — |
| `status` | Select | `Draft`/`Active`/`Paused`/`Closed` | ✔ | | `Draft` |
| `team_scope` | Link | `CRM Sales Hierarchy` | | | — |
| `started_at` | Datetime | — | | | — |
| `ended_at` | Datetime | — | | | — |
| `rule_version` | Int | — | | | `1` |
| `selection_json` | Long Text | — | | | — |

**Index:** `code` (unique), `modified` (mặc định Frappe).

**Validation (`p1_models.Campaign.validate`):**

- `ended_at >= started_at`.
- `rule_version >= 1`.
- `selection_json` phải parse được thành JSON **object**.
- Không cho `status = Closed` khi còn `CSKH Work Item` ở trạng thái `queued`/`assigned`
  ⇒ hệ thống **yêu cầu xử lý rõ ràng**, không tự giải phóng (P1 §7.7, T1-17).

**Ý nghĩa `team_scope`:** tên của một node `CRM Sales Hierarchy`. Campaign không có
`team_scope` ⇒ nhìn thấy bởi mọi leader. Campaign có `team_scope` ⇒ chỉ leader của nhánh
chứa node đó thấy.

**Ý nghĩa `selection_json`:** JSON object, hỗ trợ 3 dạng —
`{"leads": ["CRM-LEAD-0001", …]}` (danh sách tường minh),
`{"source": "Facebook"}` (mọi lead canonical có source đó),
`{}` (toàn bộ kho = các lead canonical trong `CSKH Identity Index`).

---

## 3. `CSKH Assignment Rule`

| Field | Type | Options | Reqd | Default |
|---|---|---|---|---|
| `campaign` | Link | `CSKH Campaign` | ✔ | — |
| `user` | Link | `User` | ✔ | — |
| `team` | Link | `CRM Sales Hierarchy` | | — |
| `weight` | Int | — | ✔ | `1` |
| `capacity_limit` | Int | — | | `0` |
| `enabled` | Check | — | | `1` |
| `version` | Int | — | ✔ | `1` |
| `effective_from` | Datetime | — | | — |
| `effective_to` | Datetime | — | | — |

**DB constraint:** UNIQUE `(campaign, user, version)` — tên `cskh_domain_unique`.

**Validation (`p1_models.AssignmentRule.validate`):**

- `weight > 0`, `capacity_limit >= 0`, `version >= 1`.
- `user` phải **enabled** và có role `Sales User`.
- `effective_to >= effective_from`.
- Trùng `(campaign, user, version)` bị chặn ở app layer (DB là lớp trợ).

**Ý nghĩa `capacity_limit = 0`:** không giới hạn. Capacity đếm **work item đang `assigned`**
trong campaign (P1 §1.3), **không** đếm tổng lịch sử.

**Versioning:** `api.campaigns.save_rule` **bump `CSKH Campaign.rule_version`** mỗi lần ⇒
preview cũ bị `RULE_CHANGED` khi execute.

---

## 4. `CSKH Work Item`

| Field | Type | Options | Reqd | Default |
|---|---|---|---|---|
| `campaign` | Link | `CSKH Campaign` | ✔ | — |
| `lead` | Link | `CRM Lead` | ✔ | — |
| `status` | Select | `queued`/`assigned`/`revoked`/`closed` | ✔ | `queued` |
| `assigned_to` | Link | `User` | | — |
| `assigned_at` | Datetime | — | | — |
| `import_batch` | Link | `CSKH Import Batch` | | — |
| `allocation_run` | Link | `CSKH Distribution Run` | | — |
| `last_transfer_at` | Datetime | — | | — |

**DB constraint:** UNIQUE `(campaign, lead)` — tên `cskh_domain_unique`.
⇒ Một lead chỉ có **một** work item trong một campaign.

**Index (lookup):**

| Tên | Cột |
|---|---|
| `cskh_lookup_0` | `(assigned_to, status, assigned_at)` |
| `cskh_lookup_1` | `(campaign, status)` |
| `cskh_lookup_2` | `(lead, status)` |

**State machine (P1 §1.3):** chỉ 4 trạng thái. `in_progress` / `on_hold` **thuộc P2**, P1
không dùng (nếu dùng thì invariant "một work item active" mơ hồ).

| Trạng thái | `assigned_to` | `assigned_at` |
|---|---|---|
| `queued` | phải trống | phải trống |
| `assigned` | bắt buộc | bắt buộc |
| `revoked` | phải trống | phải trống |
| `closed` | phải trống | phải trống |

**Validation (`p1_models.WorkItem.validate`):** bảng trên, fail-closed bằng
`frappe.ValidationError`.

---

## 5. `CSKH Assignment History` (append-only)

| Field | Type | Options | Reqd | Unique |
|---|---|---|---|---|
| `work_item` | Link | `CSKH Work Item` | ✔ | |
| `action` | Select | `assign`/`reassign`/`revoke`/`close` | ✔ | |
| `from_user` | Link | `User` | | |
| `to_user` | Link | `User` | | |
| `actor` | Link | `User` | | |
| `event_at` | Datetime | — | | |
| `reason` | Small Text | — | ✔ | |
| `distribution_run` | Link | `CSKH Distribution Run` | | |
| `ownership_start` | Datetime | — | | |
| `ownership_end` | Datetime | — | | |
| `event_id` | Data | — | ✔ | ✔ |
| `previous_event` | Link | `CSKH Assignment History` | | |

**DB constraint:** UNIQUE `event_id` ⇒ idempotency của retry là **DB-enforced**.

**Index (lookup):**

| Tên | Cột |
|---|---|
| `cskh_lookup_0` | `(work_item, event_at)` |
| `cskh_lookup_1` | `(to_user, ownership_start, ownership_end)` |

**Append-only enforcement (`p1_models.AssignmentHistory`):**

- `before_save`: nếu `not is_new()` ⇒ `frappe.throw` (không sửa được bản ghi cũ).
- `on_trash`: `frappe.throw` (không xoá được).
- Điều này giữ **cả** `Administrator` lẫn `System Manager` không đụng được vào audit trail.
- `action = assign` ⇒ bắt buộc có `to_user`; `action = revoke` ⇒ **không** có `to_user`.
- `event_id` bắt buộc; `actor` mặc định = `frappe.session.user`; `event_at` mặc định = now.

**Vòng đời ownership interval:** mỗi event mở `ownership_start`; event kế tiếp trên cùng
work item đóng `ownership_end` của event trước ⇒ đọc được **3 phiên sở hữu** cho A→B→C
(T1-15).

---

## 6. `CSKH Import Batch`

| Field | Type | Reqd | Unique | Default |
|---|---|---|---|---|
| `source_file` | Link `File` | ✔ | | — |
| `checksum` | Data | | | — |
| `idempotency_key` | Data | | ✔ | — |
| `status` | Select | ✔ | | `DRY_RUN` |
| `mapping_json` | Long Text | | | — |
| `preview_json` | Long Text | | | — |
| `committed_at` | Datetime | | | — |
| `error_artifact` | Link `File` | | | — |
| `total_rows` | Int | | | `0` |
| `valid_rows` | Int | | | `0` |
| `new_rows` | Int | | | `0` |
| `duplicate_rows` | Int | | | `0` |
| `error_rows` | Int | | | `0` |
| `created_rows` | Int | | | `0` |
| `reused_rows` | Int | | | `0` |
| `skipped_rows` | Int | | | `0` |

**DB constraint:** UNIQUE `idempotency_key`.

**State machine (`IMPORT_STATUS_TRANSITIONS`):**

```
DRY_RUN → CONFIRMED | FAILED
CONFIRMED → QUEUED | FAILED
QUEUED → RUNNING | FAILED
RUNNING → COMPLETED | COMPLETED_WITH_ERRORS | FAILED
COMPLETED → ()
COMPLETED_WITH_ERRORS → QUEUED      (retry)
FAILED → QUEUED                      (retry)
```

**Validation (`p1_models.ImportBatch.validate`):**

- `source_file` phải có `is_private` ⇒ **fail-closed** nếu file không private (SEC-08).
- Các counter không được âm.
- `mapping_json` phải là JSON object.
- Chuyển trạng thái không hợp lệ ⇒ `frappe.ValidationError`.

**`error_artifact`:** tên `File` **private** (`is_private=1`) chứa CSV dòng lỗi đã sanitize
(công thức Excel bị vô hiệu hoá bằng `'`; HTML bị gỡ). Không attach vào lead nào.

---

## 7. `CSKH Distribution Run`

| Field | Type | Reqd | Unique | Default |
|---|---|---|---|---|
| `campaign` | Link `CSKH Campaign` | ✔ | | — |
| `idempotency_key` | Data | ✔ | ✔ | — |
| `rule_version` | Int | ✔ | | `1` |
| `selection_fingerprint` | Data | | | — |
| `status` | Select | ✔ | | `planned` |
| `preview_json` | Long Text | | | — |
| `actual_json` | Long Text | | | — |
| `state_json` | Long Text | | | — |
| `expires_at` | Datetime | | | — |
| `started_at` | Datetime | | | — |
| `finished_at` | Datetime | | | — |
| `error` | Small Text | | | — |

**DB constraint:** UNIQUE `idempotency_key`.

**Index (lookup):** `cskh_lookup_0` `(campaign, status)`.

**`state_json`** là **state của allocator đã persist** (ADR-P1-04):

```json
{
  "epoch": 3,
  "total": 1000,
  "counts": { "staff.a@dev.local": 500, "staff.b@dev.local": 300, "staff.c@dev.local": 200 },
  "loads":  { "staff.a@dev.local": 500, "staff.b@dev.local": 300, "staff.c@dev.local": 200 },
  "allocated_total": 1000,
  "allocated_counts": { "staff.a@dev.local": 500, "staff.b@dev.local": 300, "staff.c@dev.local": 200 },
  "signature": [["staff.a@dev.local", 50], ["staff.b@dev.local", 30], ["staff.c@dev.local", 20]]
}
```

- `epoch` tăng khi tập user/weight eligible **đổi** ⇒ reset bộ đếm công bằng cho tập mới.
- `counts` / `total` là số đã giao **trong epoch** (không phải tổng lịch sử).
- `allocated_counts` / `allocated_total` là **sổ cái tích lũy**: số Work Item thực sự đã giao
  cho từng user, **không bao giờ reset** kể cả khi user rời tập eligible (ADR-P1-04 §2.5).
- `signature` là fingerprint của tập user/weight ⇒ phát hiện đổi rule.
- `loads` là số work item `assigned` hiện tại (đếm từ DB, là nguồn sự thật cho capacity).

**State machine (`RUN_STATUS_TRANSITIONS`):**

```
planned → running | failed
running → completed | completed_with_errors | failed
completed → ()
completed_with_errors → planned     (retry)
failed → planned                    (retry)
```

**Validation (`p1_models.DistributionRun.validate`):** `rule_version >= 1`,
`selection_fingerprint` bắt buộc, `preview_json`/`actual_json`/`state_json` phải là JSON hợp lệ
(`state_json` phải là **object**).

---

## 8. `CSKH Identity Index`

| Field | Type | Reqd | Unique |
|---|---|---|---|
| `normalized_phone` | Data | ✔ | ✔ |
| `canonical_lead` | Link `CRM Lead` | ✔ | ✔ |

**DB constraint:** UNIQUE `normalized_phone` và UNIQUE `canonical_lead` ⇒

- Một số điện thoại chuẩn hoá chỉ có **một** canonical lead.
- Một lead chỉ có **một** canonical phone (không có hai số khác nhau cho cùng một lead).

**Validation (`p1_models.IdentityIndex.validate`):**

- `normalized_phone` phải là E.164 VN hợp lệ (`+84` + 9 chữ số, đầu 3/5/7/8/9).
- `canonical_lead` phải tồn tại và có `mobile_no`.
- `normalize_vn_phone(mobile_no)` phải **bằng** `normalized_phone` ⇒ index không trỏ sai lead.

**Đây là lớp invariant tuyệt đối của P1 (ADR-P1-01).** Không có đường ghi nào ngoài
`crm_cskh.services.identity` được phép insert/xoá dòng index này.

---

## 9. `CSKH Active Claim`

| Field | Type | Reqd | Unique |
|---|---|---|---|
| `lead` | Link `CRM Lead` | ✔ | ✔ |
| `campaign` | Link `CSKH Campaign` | ✔ | |
| `work_item` | Link `CSKH Work Item` | ✔ | ✔ |

**DB constraint:** UNIQUE `lead` và UNIQUE `work_item` ⇒

- Một lead **không** được active ở hai campaign cùng lúc (ADR-P1-02).
- Một work item **không** được giữ hai claim.

**Validation (`p1_models.ActiveClaim.validate`):** claim phải trùng `lead` + `campaign` với
work item của nó, và work item phải ở trạng thái active (`queued`/`assigned`).

**Vòng đời:** tạo **cùng transaction** với work item trong `distribution._create_work_item`;
bị xoá cùng transaction trong `ownership.revoke` (lead trở về kho ⇒ được phép vào campaign
khác).

---

## 10. Custom field trên `CRM Lead`

| Field | Type | Default | Ghi chú |
|---|---|---|---|
| `cskh_is_placeholder_name` | Check | `0` | Đánh dấu lead tạo từ import chỉ có SĐT |

Đây là **custom field duy nhất** P1 thêm vào core `CRM Lead` (ADR-006 P0). Nó cho phép UI
phân biệt "khách chưa rõ tên" mà không sửa `first_name` của lead có sẵn.

---

## 11. Migration

| Vị trí | Nội dung |
|---|---|
| `apps/crm_cskh/crm_cskh/patches.txt` | 2 patch P1 dưới `[post_model_sync]` |
| `crm_cskh.patches.v1_0.p1_schema_constraints` | Tạo lại composite unique + lookup index (idempotent) |
| `crm_cskh.patches.v1_0.p1_placeholder_name_field` | Tạo custom field `cskh_is_placeholder_name` |
| `crm_cskh.p1_models.ensure_indexes` | **Cùng định nghĩa** constraint/index cho đường `on_doctype_update` (fresh install) |

> **Vì sao không chỉ dựa vào DocType JSON:** `unique: 1` trên DocField tạo unique index
> đơn cột. Các ràng buộc **composite** của P1 §5.6 cần `frappe.db.add_unique` /
> `frappe.db.add_index` ⇒ phải nằm trong patch. `ensure_indexes` và patch **dùng chung một
> định nghĩa** ⇒ không thể trôi lệch.

**Tính idempotent:** `frappe.db.add_unique` / `add_index` kiểm tra tên constraint trước khi
tạo ⇒ `bench migrate` chạy **hai lần** không lỗi (P1 §5.6). Đã verify trên site cô lập.

**Rollback (P1 §13.1):** patch chỉ **tạo thêm** index/constraint và custom field ⇒ rollback
an toàn bằng cách rollback code; dữ liệu `CSKH *` phải được xoá có kiểm soát (không tự
`DROP TABLE` trong bản vá).

---

## 12. Verify constraint trên DB (đã đo)

`SHOW INDEX FROM ...` trên site cô lập `p1c.localhost`:

| Bảng | Index unique | Index lookup |
|---|---|---|
| `tabCSKH Work Item` | `cskh_domain_unique (campaign, lead)` | `cskh_lookup_0/1/2` |
| `tabCSKH Assignment Rule` | `cskh_domain_unique (campaign, user, version)` | — |
| `tabCSKH Identity Index` | `normalized_phone`, `canonical_lead` | — |
| `tabCSKH Active Claim` | `lead`, `work_item` | — |
| `tabCSKH Assignment History` | `event_id` | `cskh_lookup_0/1` |
| `tabCSKH Import Batch` | `idempotency_key` | — |
| `tabCSKH Distribution Run` | `idempotency_key` | `cskh_lookup_0` |
| `tabCSKH Campaign` | `code` | — |

---

## 13. Nguồn

- P1 §5.1, §5.2, §5.3, §5.4, §5.6, §7.3, §13.1, §16
- [`adr-p1-01-identity-index.md`](./adr-p1-01-identity-index.md),
  [`adr-p1-02-active-claim.md`](./adr-p1-02-active-claim.md),
  [`adr-p1-04-weighted-fair.md`](./adr-p1-04-weighted-fair.md),
  [`adr-p1-05-ownership-sync.md`](./adr-p1-05-ownership-sync.md),
  [`adr-p1-08-import-limits.md`](./adr-p1-08-import-limits.md)
- Source: `apps/crm_cskh/crm_cskh/crm_cskh/doctype/cskh_*/`,
  `apps/crm_cskh/crm_cskh/p1_models.py`,
  `apps/crm_cskh/crm_cskh/patches/v1_0/`
