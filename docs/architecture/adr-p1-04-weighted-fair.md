# ADR-P1-04 — Weighted fair: cumulative deficit với state persisted theo epoch, biến thiên

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/adr-p1-04-weighted-fair.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Accepted** (P1 §7.3, §16)
> **Work package:** P1-WP06

---

## 1. Vấn đề

P1 §7.3 đề xuất **cumulative deficit weighted fair**:

```python
next_total = total_assignments_in_epoch + 1
winner = max(eligible_users, key=lambda user: (
    next_total * user.weight / sum_active_weights - user.cumulative_assignments,
    -user.stable_tiebreak_order,
))
```

Nhưng P1 §7.3 cảnh báo rõ: *"công thức trên là nguyên lý công bằng tích lũy trong một epoch có
cùng tập trọng số. Khi có người hết quota hoặc rule thay đổi, phải tạo **epoch/version mới**
hoặc khởi tạo lại mục tiêu/counter thích hợp cho tập user mới; nếu không có thể có những cú
lệch lớn do dùng mẫu số cũ."*

P1 §7.4 yêu cầu **bằng chứng**: 1.000 khách với 50/30/20 ⇒ **500/300/200**; 100 batch mỗi
batch 1 khách ⇒ tổng vẫn **50/30/20**.
---

## 2. Quyết định

**Thuật toán: cumulative deficit (biến thể smooth weighted round robin), với state persisted
trong `CSKH Distribution Run.state_json` theo epoch, và epoch được tính lại khi tập user/trọng số
thay đổi.**

### 2.1. Vì sao cumulative deficit thay vì "fractional remainder" (largest remainder)

Largest-remainder (chia theo phần dư) **không** đạt yêu cầu "100 batch × 1 khách ⇒ 50/30/20":
mỗi batch 1 khách sẽ luôn cho người có phần dư lớn nhất, và với batch đơn lẻ thì phần dư bị
làm tròn về 0 ⇒ phân phối lệch theo thứ tự ưu tiên. Cumulative deficit giữ **mục tiêu tích lũy**
(`next_total * weight / sum_weights`) và so với với **số đã giao thực tế** ⇒ sau 100 lần lặp
tổng bám sát tỷ lệ.

### 2.2. Công thức thực thi (thuần nguyên, không float)

Bản nháp `services/weighted_allocator.py` dùng **số nguyên** để tránh trôi float:

```python
score(user) = next_total * weight(user) - assigned_count(user) * sum_active_weights
```

`score` tăng ⇒ user đó càng "nợ" hơn ⇒ được chọn. Nhân `sum_active_weights` vào cả hai vế
giữ đúng thứ tự so sánh với công thức phân số trong P1 §7.3, và vẫn dùng số nguyên.

### 2.3. Tie-break tất định

Khi `score` bằng nhau, chọn user có **`stable_tiebreak_order` nhỏ hơn**. Thứ tự này là
`sorted(user)` — tức thứ tự chữ cái ổn định, **không** phụ thuộc thứ tự truy vấn DB, không phụ
thuộc dict ordering ⇒ cùng input ⇒ cùng output (P1 §1.2 #6: *"deterministic/idempotent"*).

### 2.4. State persisted ở đâu

`CSKH Distribution Run.state_json`:

```json
{
    "epoch": 3,
  "signature": [["a@example.test", 50], ["b@example.test", 30], ["c@example.test", 20]],
  "total": 120,
  "counts": {"a@example.test": 60, "b@example.test": 36, "c@example.test": 24},
  "loads": {"a@example.test": 60, "b@example.test": 36, "c@example.test": 24},
  "allocated_total": 120,
  "allocated_counts": {"a@example.test": 60, "b@example.test": 36, "c@example.test": 24}
}
```

| Trường | Ý nghĩa |
|---|---|
| `epoch` | Tăng mỗi khi `signature` đổi ⇒ một "kỳ công bằng" mới |
| `signature` | Tập `(user, weight)` đang áp dụng, đã sort |
| `total` | Tổng số lượt phân bổ **trong epoch hiện tại** |
| `counts` | Số lượt đã giao **trong epoch** cho từng user |
| `loads` | Số Work Item active hiện tại của từng user (capacity accounting) |
| `allocated_total` | Tổng số lượt phân bổ **của mọi epoch** (sổ cái tích lũy) |
| `allocated_counts` | Số Work Item **thực sự** đã giao cho từng user, mọi epoch |

**Vì sao `counts` theo epoch chứ không phải toàn hệ thống:** P1 §7.3 yêu cầu khi rule/quota
đổi thì phải có epoch mới, nếu không sẽ lệch lớn do mẫu số cũ. Với `counts` reset theo epoch,
sau khi đổi rule, tỷ lệ được tính lại từ đầu trên tập user mới ⇒ không kế thừa "nợ" của kỳ cũ.

**Vì sao cần `allocated_counts` (sổ cái tích lũy) riêng:** `counts` bị reset theo epoch, nên
**không** đủ để trả lời "user A đã nhận được bao nhiêu work item". Một user rời tập eligible
(vì chạm capacity, bị disable, hoặc đổi team) sẽ biến mất khỏi `counts` — báo cáo sẽ nói user
đó nhận 0 dù database có 2. `allocated_counts` **không bao giờ reset**, nên nó là câu trả lời
trung thực và là thứ dashboard/reconciliation đọc. (`distribution._state_cursor` gồm cả hai
nhóm ⇒ một preview cũ bị từ chối sau khi run khác đã commit.)

> **Defect đã phát hiện & sửa trong P1 (2026-11-02):** bản nháp đầu tiên reset `counts` cùng
> epoch và **không** có sổ cái tích lũy. Test `test_capacity_exhaustion_stops_the_allocation`
> bắt được: sau khi `a` chạm capacity, `state["counts"]` không còn khoá `"a"`. Đã tách
> `counts`/`total` (epoch) khỏi `allocated_counts`/`allocated_total` (tích lũy).

**`loads` là số Work Item active thật, không phải counter epoch** — P1 §1.3: *"`capacity` = số
**Work Item active đang được giao** cho user trong phạm vi do rule quy định, không phải tổng số
lead người đó từng nhận."* Vì vậy `loads` **không** reset theo epoch; nó phản ánh DB.

### 2.5. Khi nào epoch tăng

`signature` = danh sách `[(user, weight)]` của các user **còn đủ điều kiện** (enabled, đúng team,
weight > 0, còn capacity), đã sort. Khi `signature` khác `signature` đang lưu ⇒ `epoch += 1`,
`total = 0`, `counts` khởi tạo lại.

Hệ quả mong muốn:
- capacity đầy một user ⇒ user đó rời `signature` ⇒ **epoch mới** ⇒ các user còn lại chia lại
  theo tỷ lệ mới ⇒ không "ép vượt capacity", không dùng mẫu số cũ (P1 §7.3, §7.4).
- rule đổi weight ⇒ `signature` đổi ⇒ epoch mới.

> **Caveat đã biết và được chấp nhận:** nếu user **tạm thời* hết capacity rồi lại có capacity
> trở lại (ví dụ revoke), `signature` có thể trở lại giống cũ nhưng `epoch` đã tăng ⇒ mục tiêu
> được tính lại từ đầu cho epoch mới ⇒ phân phối epoch mới bám tỷ lệ từ đầu. Điều này **đúng**
> với nguyên tắc "epoch/version mới", và được ghi vào `distribution-algorithm.md` + test.

### 2.6. Capacity = 0 nghĩa là gì

`CSKH Assignment Rule.capacity_limit = 0` ⇒ **không giới hạn** (ghi rõ trong
`description` của field). Draft `p1_models.py` đã dùng quy ước này; giữ nguyên.

---

## 3. Concurrency & locking

P1 §7.5: *"**Bắt buộc thiết kế khóa DB, không chỉ lock Python trong bộ nhớ.**"*

Cơ chế:

1. `CSKH Distribution Run` có `idempotency_key` UNIQUE ⇒ một run = một `preview_id`.
2. Chuyển trạng thái `planned → running` bằng **conditional update** (compare-and-swap):
   `UPDATE ... SET status='running' WHERE name=? AND status='planned'` ⇒ chỉ một executor thắng.
3. `state_json` được cập nhật trong **cùng transaction** với Work Item/Claim/History ⇒ counter
   epoch không bao giờ lệch với DB thật.
4. Row-level lock trên `CSKH Work Item`/`CSKH Active Claim` theo thứ tự ổn định
   (campaign/rule-state → Lead/claim → Work Item) ⇒ giảm deadlock.
5. `CSKH Active Claim.lead` UNIQUE là lớp bảo vệ cuối ⇒ **không bao giờ** có 2 active claim
   cho 1 Lead kể cả khi logic ứng dụng có lỗi (ADR-P1-02).

Retry một dòng đã commit ⇒ trả về trạng thái có sẵn (idempotency theo `event_id`/
`request_id`), không nhân đôi Work Item/History/counter.

---

## 4. Preview fingerprint

`execute` phải nhận `preview_id` + `expected_rule_version` + `idempotency_key`. Điều kiện
(P1 §10.1, §7.4):

- `selection_fingerprint` = SHA-256 của **tập Lead được chọn** (sắp xếp, canonical) ⇒ nếu tập
  chọn đổi ⇒ `RULE_CHANGED`/`CONFLICT`, yêu cầu preview lại.
- `rule_version` = version của bộ rule tại lúc preview ⇒ đổi ⇒ `RULE_CHANGED`.
- `expires_at` ⇒ hết hạn ⇒ `CONFLICT`.
- Tại lúc **execute**, điều kiện eligibility được **kiểm lại** (P1 §7.1 #5), không chỉ tin preview.

Audit ghi `eligible_at_preview`, `eligible_at_execute`, `skipped_reason` cho từng dòng bị loại
(P1 §7.4).

---

## 5. Điều kiện chấp nhận

| Test | Kỳ vọng |
|---|---|
| T1-07 | Preview 1.000 lead 50/30/20 ⇒ dự kiến 500/300/200 (capacity đủ) |
| T1-08 | Execute 1.000 lead 50/30/20 ⇒ **actual 500/300/200** |
| T1-09 | 100 batch × 1 lead ⇒ tổng **50/30/20** |
| T1-10 | A hết quota giữa batch ⇒ không vượt quota, phần còn lại chia lại, có lý do |
| T1-11 | Staff disabled / sang team khác ⇒ không còn eligible |
| T1-12 | 2 run cùng chọn 1 Lead ⇒ 1 active claim, 1 owner |
| T1-13 | Retry job đã commit một phần ⇒ không double assignment/history/counter |
| T1-14 | Rule/selection đổi sau preview ⇒ execute bị từ chối |
| §7.4 | Với N nhỏ và weights cố định: `abs(actual_i - N*w_i/W) <= 1` |

---

## 6. Nguồn

- P1 §1.2 #5/#6, §1.3, §7.1, §7.2, §7.3, §7.4, §7.5, §7.6, §10.1, §10.2, §12.2, §12.4, §16
- `docs/architecture/adr-p1-02-active-claim.md`
