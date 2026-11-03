# T1C — Báo cáo kiểm định độc lập P1 (Lịch sử `3025495` / `9ec881f` / `64b7486` / `d0842cc` / `a75c093` / `a9094b13` & Nghiệm thu Tích hợp Cuối PR #5 `65dd639` — Code SHA `7bd7185`, Backend `6c457b3`)

> **Vai trò:** T1C — Independent Security, Quality & Runtime Verification
> **Ngày cập nhật nghiệm thu cuối:** 2026-11-03
> **Kế hoạch gốc:** [P1.md](file:///D:/HRP-app/new-crm/docs/root-plan/P1.md)
> **Baseline đối chiếu:** [t1c-p1-verification-baseline-2026-11-02.md](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md)
> **Bản giao tích hợp cuối hiện hành (PR #5 `HEAD`):** `65dd639c0a63011dee54bc2b973595dd69d7560a` — Code SHA `7bd7185a35680d4f716cfe0b0dc1d6b7bfb46cb9` (`0` diff so với `HEAD` trên `apps/`, `.github/`, `deploy/dev/`), Backend ancestor PR #4 `6c457b327e59eead47cc9d0982c4ee1df4fe9e64`, Synthetic merge `d9cfd2c81d401f4278139fa556b71a9decb2a575`:
> - **CI P1 `#41613509233`:** **`209/209` backend JUnit + `12/12` UI JUnit + `170/170` HTTP PASS**, Artifact `13220970780`, SHA-256 `82516cdfcc32674fc3d3bb4ead204e0b89304490a04a1ea9940ed6bce5ff3dcd`.
> - **CI P0 `#41613509298`:** **`99/99` P0 core + `8/8` transport + `18/18` HTTP + `9/9` file-oracle + `74/74` remaining-gaps + `backup_restore` PASS**, Artifact `13220756000`, SHA-256 `a85025b7d27498e813c15128f0d95ad79e175593433a464de462733cefa14ac2`.
> **Target lịch sử đã bảo toàn theo đúng revision:** PR #5 `a9094b13bcefacccb274f45a01337ef943ccf59c` (Code SHA `3f07b9b3f2e68f9bf3eea005c634c7a11393ec8b`, ancestor `c997df76`); PR #5 `a75c093c79c38c80b9fd3c21758a5bbad09cb3a1` (Code SHA `63510483a0aa1ed4238b630ba384c5a362659450`, ancestor `c49f7f8`); PR #5 trước async `d0842cc1c822c1b138ed4d030f2c7daaa706e586` (Code SHA `0d3f2bcbbf9715a4c0c783903ce750de8646583d`, ancestor `64b7486`); PR #4 `64b7486f29ad712106d167dfaea50e3b060a6845`; PR #4 baseline `3025495f58bc0391c3f8d092af500b0566a5f038` (Synthetic merge `5a4ea0f6b2ad59ead72fe2b7dbee03ec6d8dac11`); UI sơ bộ `9ec881f72d4305a607e0868a85ef6361bb095e97`
> **Kết luận nghiệm thu kỹ thuật của T1C trình T0/Owner:** **ĐẠT ĐỦ `14/14` GATE (`G1-01 → G1-14` = `PASS`), `20/20` `T1-01..T1-20` = `PASS`, `10/10` `SEC-01..SEC-10` = `PASS`, `0` BLOCKER CÒN MỞ (`L-01 → L-29` ĐỀU ĐÃ ĐÓNG) — ĐỦ ĐIỀU KIỆN KỸ THUẬT BÀN GIAO T0/OWNER QUYẾT ĐỊNH `GO P2`** *(T1C tuân thủ ranh giới độc lập: không merge PR, không tự ký thay quyết định Owner; ghi nhận 2 lưu ý nhỏ không chặn `O-01`, `O-02` tại Mục 5.4)*.

---

## 0. Tóm tắt Điều hành Theo Đúng Từng Revision (Executive Verdict)

Thực hiện hai chỉ đạo nghiệm thu cuối của T0 trên bản tích hợp **PR #5 `65dd639c0a63011dee54bc2b973595dd69d7560a` (Code SHA `7bd7185a35680d4f716cfe0b0dc1d6b7bfb46cb9`, chứa trọn vẹn Backend PR #4 `6c457b327e59eead47cc9d0982c4ee1df4fe9e64` — `2235757` + `77e07da` + `6c457b3`)**, T1C bảo toàn toàn bộ kết luận lịch sử theo đúng revision (`3025495`, `64b7486`, `d0842cc`, `a75c093`, `a9094b13`), tải & băm độc lập 2 gói CI (`#41613509233` & `#41613509298`), đối chiếu `30/30` file trong `p1-runtime-20261102/SHA256SUMS.txt`, và thực chạy kiểm định delta trên worktree cô lập sạch `D:\HRP-app\t1c-verify` (`65dd639`, `0` dirty) cùng stack Docker cô lập `crm-cskh-t1c-*` (`t1c.localhost`, đối chiếu `134/134` tệp nguồn mount khớp 100% SHA-256 LF Git blob `7bd7185`, tuyệt đối không chạm `D:\HRP-app\p1-ui`, `D:\HRP-app\p1-foundation`, `crm-cskh-p1-*` hay `crm-cskh-p1-dev-*`).

> [!IMPORTANT]
> **Tóm tắt Nghiệm thu Tích hợp Cuối trên PR #5 `65dd639` (Code SHA `7bd7185` + Backend `6c457b3`):**
> 1. **`L-23` (`CLOSED / PASS` trên cả 3 API Kho & mọi chuyển dịch `A → B`):**
>    - [pool_ui.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/pool_ui.py) và [services/reporting.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/reporting.py#L412-L937) (`pool_list` & `customer_pool`) đều áp chung mệnh đề ủy quyền SQL `_team_lead_authorization_clauses(scope, "l")` **ngay trước `LIMIT`/`OFFSET`**.
>    - Replay thực tế 6 chuyển dịch `a1..a6` từ Đội A sang Đội B (gồm cả `a2`: `revoke(return_to_pool=True)` để lại `Work Item` `status='queued'` ở Đội A rồi chuyển sang Đội B kiểm ở cả **Stage 1 đang `queued` tại Đội B** lẫn **Stage 2 đã `assigned` cho `staff_c`**, và `a6`: `reassign` trực tiếp từ `staff_a` sang `staff_c` ngay trong chiến dịch chung): **`Leader A` thấy `0` bản ghi/SĐT/tên/owner/campaign của `a1..a6`** trên cả `pool_ui.list`, `customers.customer_pool` và `customers.pool_list`; bộ đếm trước phân trang (`page_size=1`) thống nhất tuyệt đối (`4/4/4` trên `pool_ui`/`customer_pool`, `2/2/2` trên `pool_list`); positive control `Leader B` thấy đủ (`7` ở Stage 1 gồm `a2`, `4` trong `batch_l23`); `BOD` `0` PII (`?search=` trả `403 FORBIDDEN`); mọi filter `search` (`+84`/`0...`/tên), `source`, `batch`, `campaign`, `state` không rò oracle.
> 2. **`L-29` (`CLOSED / PASS` đa tầng & Xác nhận lại `G1-05` / `G1-11` không bảo toàn máy móc):**
>    - Kết hợp [p1_permissions.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/p1_permissions.py#L265-L526) (scope cấp tài liệu + `0` quyền ghi trên mọi business role), `permlevel = 1` trên `7` cột nhạy cảm (`CSKH Campaign.selection_json` và `CSKH Distribution Run.{preview_json, actual_json, state_json, progress, error, lease_expires_at}`), `tabCustom DocPerm = 0`, [guarded_crm.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/guarded_crm.py#L315-L468) và `auth_hooks = enforce_p1_filter_field_permissions`:
>    - Replay trực tiếp trên **REST `/api/resource/*`**, **`frappe.client.*`** (`get`, `get_list`, `get_value`, `get_count`, `validate_link`, `set_value`, `insert`, `delete`) và **Desk** (`form.load.getdoc`, `reportview.get`/`get_list`/`get_count`, `search_link`, `search_widget`): `Leader A` bị chặn **`403`** trên mọi doc riêng Đội B; trên chiến dịch/run chung, tường minh `fields=["name","selection_json"]` / `["name","preview_json","actual_json","state_json","progress","error","lease_expires_at"]` đều bị tước sạch `100%` cho `Leader A`, `Leader B`, `BOD` (`CRM-ADMIN` vẫn đọc đủ); mọi filter-oracle trên cột `permlevel = 1` (`filters` & `or_filters`, cả GET query lẫn POST JSON) đều bị chặn **`403`**; mọi thao tác ghi generic REST/`frappe.client` đều bị chặn **`403`** kể cả với `CRM-ADMIN`.
>    - Chạy lại trọn luồng API kiểm soát (`campaigns.list`, `campaigns.rules`, `run_history.runs`, `distribution.get_run`) và phân phối nền RQ thật (`camp_rq`: `6` lead -> `3/3` `staff_a`/`staff_c`) xác nhận **`G1-05` và `G1-11` hoạt động trơn tru, không vỡ bất kỳ luồng hợp lệ nào của `Admin` / `BOD` / `Leader` / `Staff`**.
> 3. **Direct `POST` Mutation Eligibility & `L-26` (`CLOSED / PASS`):**
>    - `POST` trực tiếp (cả JSON lẫn `application/x-www-form-urlencoded`) vào `ownership.assign` & `ownership.reassign` ([services/ownership.py#L118-L164](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/ownership.py#L118-L164)): chặn đủ sai đội (`400`), `User.enabled=0` (`400`), thiếu `Sales User` (`outside`/`BOD` -> `400`), rule phiên bản mới nhất `enabled=0` (`400`), `weight=0` (`400`), lệch `rule.team` (`400`), chạm trần `capacity_limit=1` (`409 CAPACITY_EXCEEDED` trên cả `assign` lẫn `reassign`), `capacity_limit=0` không giới hạn (`200` liên tiếp 3 lần), chiến dịch `Closed` (`409 CONFLICT` trên cả `assign`, `reassign`, `distribution.preview` và `distribution.execute` sau khi preview lúc Active rồi đóng chiến dịch), và xung đột `CSKH Active Claim` chéo chiến dịch (`409 ALREADY_ASSIGNED`).
>    - `L-26`: đủ **`8/8` endpoint danh sách/lịch sử** (`4` UI + `4` Backend: `customers.my_list`, `customers.pool_list`, `customers.customer_pool`, `ownership.history`) cùng `campaigns.save_rule` (`version=True`) và `distribution.execute` (`expected_rule_version="abc"|True`) trả đúng **`400 INVALID_REQUEST` (`16/16` probe sai kiểu/biên, `0` HTTP `500`)**.
> 4. **CI, Fixture (`L-27`), Hồ sơ (`L-28`) & Bảo toàn `L-25` (`CLOSED / PASS`):**
>    - Bảo toàn `L-25` (`CSKH Import Batch Lead`, `migrate × 2` idempotent); CI `#41613509233` xanh đủ `209/209` P1 (`90/90` `test_p1_regressions` thực chạy lại trên `t1c.localhost`) + `12/12` UI + `170/170` HTTP; Audit dịch Việt `210` dùng / `409` khai báo / `613` dòng dịch (`0` lỗi); `P1-Exit-Report.md` đã gắn nhãn lịch sử đầy đủ cho §7.1–§7.10, giữ nguyên UTF-8 §7.9 + erratum `0d3f2bcbbf9715a4c0c783903ce750de8646583d`, và bổ sung bảng tổng hợp 4 đợt tại §7.11.

| Trục đánh giá | PR #5 Lịch sử `a9094b13` (`3f07b9b3`) | **Bản Nghiệm thu Cuối PR #5 `65dd639` (Code `7bd7185`, Backend `6c457b3`)** | Trạng thái ký T1C |
|---|---|---|---|
| **1. Bảo mật & Phân quyền (`SEC-01..SEC-10`, `L-23`, `L-24`, `L-29`)** | `FAIL` (`L-23` trên `customers.*`; `L-29` trên `/api/resource/*`) | **PASS (`10/10` `SEC-01..SEC-10`):** Đóng sạch `L-23` trên cả 3 API Kho (mọi chuyển dịch `a1..a6`, SQL trước phân trang) & đóng sạch `L-29` trên REST, `frappe.client.*`, Desk, `permlevel=1` filter-oracle | **T1C KÝ MỚI `G1-11` = `PASS` & TÁI KÝ `G1-05` = `PASS` trên `65dd639`** |
| **2. Schema, Index, `migrate × 2`, `CSKH Import Batch Lead` (`L-25`)** | **PASS** | **PASS** (`9` bảng `tabCSKH *`, `cskh_domain_unique` + lookup indexes, `tabCustom DocPerm = 0`, `7` cột `permlevel=1`, `migrate × 2` sạch trên CI `#41613509233` & `t1c.localhost`) | **T1C TÁI KÝ `G1-01`, `G1-02` & BẢO TOÀN `G1-03`, `G1-04` = `PASS`** |
| **3. Direct `POST` Ownership Mutation & Validation `8/8` Endpoint (`L-26`)** | `PARTIAL` (`4` API Backend `500`; thiếu check mutation trực tiếp) | **PASS:** Direct `POST` `assign`/`reassign` chặn đủ team/rule/weight/capacity/disabled/`Closed`/Active Claim (`400`/`409`); `capacity=0` không giới hạn (`200`); `16/16` probe `L-26` trả `400 INVALID_REQUEST` (`0` HTTP `500`) | **T1C KÝ MỚI `G1-08`, `G1-09` = `PASS` & TÁI KÝ `G1-10` = `PASS` trên `65dd639`** |
| **4. Phân phối nền RQ, Fairness, Race & Chaos (`G1-06`, `G1-07`)** | **PASS** | **PASS** (Bảo toàn fairness/race/chaos + chạy lại phân phối nền RQ thật `camp_rq` sau khóa quyền `L-29` về đích `completed` `3/3`, `BOD` & `Leader A` đọc đúng scope) | **BẢO TOÀN `G1-06`, `G1-07` = `PASS`** |
| **5. UI Tiếng Việt (`G1-12`), CI Coverage (`L-27`) & Hồ sơ Exit (`L-28`)** | `G1-12` PASS; `L-27, L-28` PARTIAL | **PASS:** `vi.csv` `613` dòng dịch (`0` lỗi); CI `#41613509233` `209/209` P1 + `12/12` UI + `170/170` HTTP PASS; `P1-Exit-Report.md` gắn nhãn lịch sử §7.1–§7.10 + bảng 4 đợt §7.11 *(kèm 2 ghi chú nhỏ không chặn `O-01`, `O-02`)* | **T1C TÁI KÝ `G1-12` & KÝ MỚI `G1-13`, `G1-14` = `PASS` trên `65dd639`** |

---

## 1. Đề bài gốc nguyên văn từ T0 / User

### 1.1. Chỉ đạo kiểm định Baseline P1 (Áp dụng cho `3025495` & `5a4ea0f6`)

```text
T1C, kiểm định lại P1 theo:
D:/HRP-app/new-crm/docs/root-plan/P1.md
và baseline:
D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md

Bắt đầu rà backend ngay, không cần chờ UI hoàn tất:
- PR #4 HEAD 3025495f58bc0391c3f8d092af500b0566a5f038.
- CI P1 #41423954824, artifact 13131556129:
  SHA-256 1418d61006f7fb1e5632a220583bc8e7ebbd6d4a9e5284c817da9e7ccc268158.
- CI P0 #41423954877, artifact 13132310090:
  SHA-256 3eb0ebd134dbd0e61cbf8573c523a2697538c83874fb14c45ec2f0b5122bb595.
- Synthetic merge:
  5a4ea0f6b2ad59ead72fe2b7dbee03ec6d8dac11,
  parents main 6db1f3b + head 3025495.
- T0 đã xác minh P1 174/174, P0 99/99 và các probe P0 đều PASS.

1. Rà độc lập bản sửa
- Xác nhận từng finding baseline L-01…L-15 và lỗi bổ sung L-16
  bằng testcase/artifact phù hợp.
- Rà assertion để tránh PASS do bỏ kiểm tra, dữ liệu rỗng,
  thiếu positive control hoặc dùng sai actor.
- Ưu tiên dashboard scope, history permission, import,
  File authorization và lỗi HTTP.
- Đánh giá scope trên mọi card/list/total/filter, không chỉ một chỉ số.

2. Kiểm định runtime còn thiếu
- HTTP actor thật: CRM-ADMIN, Leader A/B, Staff A/B, BOD và Guest.
- Đối chiếu số liệu với DB theo actor/campaign.
- Kiểm tra race import/distribution 2 và 5 process.
- Rà artifact crash/rollback/retry và benchmark mới của T1B.
- Kiểm tra unique/index, migrate hai lần và backup/restore.
- Khi T1A bàn giao, kiểm định UI/i18n/E2E trên revision tích hợp cụ thể.

3. Áp tiêu chí đúng phạm vi
- Không dùng 174 test xanh để suy ra mọi gate P1 PASS.
- on_doctype_update có version control là cách tạo index được plan cho phép;
  đánh giá bằng evidence migration, không yêu cầu patches.txt chỉ vì hình thức.
- frappe.get_all nội bộ không tự động là vulnerability;
  kiểm tra authorization và output thực tế.
- Capacity=0 là không giới hạn; kiểm tra tính nhất quán contract/code/UI.
- Phân biệt lỗi chức năng, vulnerability tái hiện được và coverage chưa đo.

4. Bàn giao nghiệm thu
- Cập nhật T1-01…T1-20, SEC-01…SEC-10, G1-01…G1-14.
- Mỗi finding/gate ghi verdict, SHA, actor, artifact và giới hạn.
- Tách evidence backend khỏi UI; ghi rõ phần nào đã ký và phần nào chờ.
- Không mở round/changelog chỉ để lặp lại cùng kết quả PASS.
- Khi đủ tiêu chí, bàn giao T0/Owner quyết định GO P2;
  nếu còn blocker hoặc evidence bắt buộc thiếu, giữ NO-GO.

Không sửa code T1A/T1B, không nới assertion, không merge,
không gọi agent khác và không mở lại P0 khi chưa có regression thực tế.
```

### 1.2. Chỉ đạo kiểm định Delta Backend (Áp dụng cho `64b7486` — PR #4)

```text
T1C, tiếp tục kiểm định delta, không làm lại toàn bộ baseline.

Báo cáo hiện tại của bạn áp dụng cho 3025495 và 9ec881f.
Backend mới đã bàn giao:
64b7486f29ad712106d167dfaea50e3b060a6845 — PR #4.

T0 đã tải/hash/parse độc lập:
- CI P1 #41567123312: 184/184 JUnit + 52/52 HTTP PASS.
- CI P0 #41567123348: 99/99 core và các probe P0 PASS.
- Digest hai artifact khớp GitHub.

Nhiệm vụ:

1. Giữ kết luận lịch sử đúng revision; không áp nguyên verdict
   3025495 sang 64b7486 hoặc revision mới hơn.

2. Hòa giải L-11:
   Bộ chaos/benchmark đã được commit tại:
   D:/HRP-app/p1-foundation/deploy/dev/evidence/p1-runtime-20261102/
   Kiểm tra manifest, provenance mã thực chạy và tính đầy đủ evidence.
   Phân biệt “đã commit” với “T1C đã xác minh và ký”.

3. Rerun delta L-19/L-20:
   Import qua create_preview → confirm lần đầu → worker thật.
   Dùng ít nhất 250 dòng, đặt cả dòng hợp lệ và lỗi sau dòng 200.
   Đối chiếu toàn bộ Lead/Identity, totals và error artifact;
   kiểm tra không mất dòng, không cộng lỗi hai lần.
   Không bypass state machine bằng db.set_value.

4. Theo dõi bản sửa T1B cho L-16/L-17/L-18/L-21/L-22.
   Kiểm tra HTTP đúng actor, positive control khác 0,
   JSON/form-urlencoded và dashboard đối chiếu SQL.

5. Khi có phân phối nền:
   Kiểm tra enqueue, run_id, polling, tiến độ bền vững,
   double-submit, retry/crash và recheck quyền/rule/capacity.
   Không dùng benchmark service chạy xong để thay evidence luồng HTTP.

6. Chỉ chạy lại phần chịu ảnh hưởng hoặc phần thiếu evidence.
   Bảo toàn các kết luận PASS không bị thay đổi.
   Các gate mới ký phải ghi SHA, actor, criterion và artifact.
   Tách verdict backend khỏi nghiệm thu UI cuối của T1A.

Lưu ý manifest: file benchmark 10k-fresh trong checkout Windows
có CRLF; Git blob 64b7486 khớp digest manifest.
Không coi khác line-ending là thay đổi nội dung benchmark.

Không sửa code T1A/T1B, không merge, không gọi agent khác.
Giữ GO P2 = NO-GO khi còn blocker; không mở lại P0 nếu chưa có regression.
```

### 1.3. Chỉ đạo kiểm định UI & API Mới (Áp dụng cho `d0842cc` — PR #5)

```text
T1C, bắt đầu rà UI ngay trên PR #5:
d0842cc1c822c1b138ed4d030f2c7daaa706e586,
backend ancestor 64b7486.

CI:
- P0 #41572306623: success.
- P1 #41572306826: success.

Nguồn evidence:
D:/HRP-app/p1-ui/deploy/p1/evidence/http-e2e-report.json
D:/HRP-app/p1-ui/deploy/p1/evidence/browser-manifest.json

1. Rà độc lập UI/i18n/E2E và các API mới:
   import_files, import_history, ownership_ui, pool_ui, run_history.
   “Không đổi permissions.py” không thay thế kiểm định quyền API mới.
2. Kiểm tra đúng actor, scope, BOD không PII, File raw/error,
   search/filter/pagination, CSRF và unauthorized mutation.
3. Đối chiếu 6 test UI endpoint với phạm vi thực sự được CI chạy;
   phân biệt 297 test local với 184 test backend trong CI.
4. Giữ verdict từng phần trên đúng revision.
5. Khi T1B/T1A bàn giao phân phối nền và sửa lỗi cuối,
   chỉ kiểm định lại delta bị ảnh hưởng trên một SHA tích hợp.
6. Bàn giao đủ 14 gate cùng blocker thực tế cho T0/Owner.

Không sửa code T1A/T1B, không merge, không gọi agent khác.
GO P2 giữ NO-GO đến khi đủ tiêu chí.
```

### 1.4. Chỉ đạo kiểm định Delta Tích hợp Lịch sử (`a75c093`, Code SHA `6351048`)

```text
T1C, baseline tích hợp mới là a75c093, code SHA 6351048.

T0 đã tự tải/hash/parse CI P1 #41583684962:
199/199 backend + 6/6 UI + 75/75 HTTP PASS.
Artifact 13206997162, SHA-256:
66be3962c092d29dd6b11ba5251076cc31b7b4cee09e1f77102c226f0fd625f9.

Nhiệm vụ:
1. Kiểm định độc lập delta async, polling/reload và L-20/L-21/L-22.
2. Khi có bản sửa, tái chạy nguyên fixture L-23/L-24;
   rà raw response và các đường list/run_id/polling.
3. Kiểm định mapping/batch L-25 và validation/eligibility L-26.
4. L-27: phần chạy UI tests đã bổ sung; đánh giá tiếp chất lượng
   fixture và coverage, không chỉ số method.
5. Xác nhận đính chính provenance, cập nhật các gate bị ảnh hưởng
   trên một SHA tích hợp cuối; bảo toàn PASS còn đủ căn cứ.

Không mở lại toàn bộ baseline, không sửa code T1A/T1B,
không merge và không gọi agent khác.
GO P2 giữ NO-GO đến khi hết blocker.
```

### 1.5. Chỉ đạo kiểm định Delta Lịch sử trên PR #5 (`a9094b13`, Code SHA `3f07b9b3`, Backend Ancestor `c997df76`)

```text
T1C, bắt đầu kiểm định delta ngay trên PR #5:

HEAD: a9094b13bcefacccb274f45a01337ef943ccf59c
Code SHA: 3f07b9b3f2e68f9bf3eea005c634c7a11393ec8b
Backend ancestor: c997df76b9a29a9f8d8be4a4ffd0875035762672

T0 xác nhận phần app/workflow giữa Code SHA và HEAD không đổi.
Dùng worktree/site cô lập; bảo toàn Owner stack.

Thực hiện checklist L-23…L-28 bạn đã chuẩn bị.
Có thể chạy kiểm định runtime trong khi CI hoàn tất;
chưa ký gate phụ thuộc CI khi chưa đọc kết quả/artifact.

Ưu tiên đặc biệt:
1. L-23 phải kiểm trên cả pool_ui.list, customers.customer_pool
   và customers.pool_list. Không suy từ việc wrapper UI đã sửa.
2. Service reporting.customer_pool tại HEAD này vẫn có predicate
   dựa trên Work Item lịch sử; tái hiện bằng fixture A→B đã dùng.
3. L-26 kiểm đủ các endpoint, gồm customers.my_list,
   customers.customer_pool và ownership.history.
   Source vẫn còn int(...) trực tiếp ở các đường backend.
4. Kiểm định DocType Import Batch Lead mới bằng quyền CRUD,
   unique/index và migrate thực tế; xác nhận mapping/batch nhiều đợt.
5. Parse JUnit UI theo inventory 11 method và kiểm tra fixture
   không rỗng, không nới assertion.
6. Kiểm tra L-28, đính chính provenance và số liệu hiện hành;
   giữ nguyên lịch sử đã gắn revision.

Bàn giao PASS/FAIL từng finding và gate, kèm SHA/actor/artifact.
Không tạo vòng báo cáo chỉ để nhắc lại PASS.
Không sửa code T1A/T1B, không merge, không gọi agent khác.
GO P2 giữ NO-GO khi còn blocker.
```

### 1.6. Chỉ đạo Chuẩn bị Kiểm định Delta Sau `a9094b13` (`2026-11-03T14:30:29+07:00`)

```text
T1C, bảo toàn L-25 và các PASS còn đủ căn cứ.

Trên SHA tích hợp chứa 2235757 và bản sửa L-29:
1. Replay L-23 trên cả ba API Kho.
2. Kiểm tra đủ validation backend/UI của L-26.
3. Replay L-29 qua REST, client và Desk:
   document riêng, campaign chung, list fields JSON tường minh.
4. Xác nhận API scoped vẫn hoạt động với đúng actor.
5. Rà lại các gate phụ thuộc quyền Campaign/Rule/Run,
   gồm G1-05 và G1-11; không bảo toàn PASS máy móc khi có tác động.
6. Đối chiếu coverage CI L-27 và tài liệu L-28 trên bản cuối.

Chỉ chạy phần delta cần thiết, ghi đúng SHA/actor/artifact.
Không sửa code T1A/T1B, không merge, không gọi agent khác.
GO P2 giữ NO-GO đến khi hết blocker.
```

### 1.7. Chỉ đạo Nghiệm thu Bản Tích hợp Cuối chứa Backend `6c457b3` (`2026-11-03T16:41:29+07:00`)

```text
T1C, bản backend cần nghiệm thu là:
6c457b327e59eead47cc9d0982c4ee1df4fe9e64.

Khi T1A bàn giao SHA PR #5 chứa commit này:
1. Replay L-23 trên cả ba API Kho và mọi chuyển dịch A→B;
   kiểm tra rows/totals trước phân trang, positive control B,
   BOD 0 PII và các filter/search.
2. Replay L-29 trên REST, client và Desk;
   kiểm tra document riêng/chung, raw JSON và đúng actor.
3. Kiểm tra mutation eligibility qua POST trực tiếp,
   gồm team/rule/capacity, disabled user và campaign closed.
4. Đối chiếu CI, fixture, metadata và tài liệu trên cùng SHA.
5. Bảo toàn PASS không bị ảnh hưởng; bàn giao verdict các gate còn lại.

Dùng checkout/site cô lập sạch.
Không sửa code, không merge, không gọi agent khác.
```

---

## 2. Khóa Provenance, CI Artifacts & Danh mục Bằng chứng Theo Revision

### 2.1. Ma trận Revision & Đối chiếu Độc lập trên Bản Tích hợp Cuối `65dd639` (`7bd7185`)

| Đối tượng | Giá trị đã khóa & đối chiếu độc lập | Trạng thái |
|---|---|---|
| `main` baseline (P0 GO) | `6db1f3b55afece6fba21c71200fbd55df1200e29` (`d:\HRP-app\new-crm`) | Nguyên vẹn (`0` sửa đổi code sản phẩm) |
| **PR #4 lịch sử (`3025495`, `64b7486`, `c49f7f8`, `c997df76`)** | `3025495` -> `64b7486` -> `c49f7f8` -> `c997df76b9a29a9f8d8be4a4ffd0875035762672` (`L-25` + `CSKH Import Batch Lead`) | Bảo toàn lịch sử |
| **PR #4 Backend Nghiệm thu Cuối (`6c457b3`)** | `6c457b327e59eead47cc9d0982c4ee1df4fe9e64` (hợp nhất `2235757b` + `77e07dac` + `6c457b32`: SQL `L-23` trước phân trang trên cả `pool_list` & `customer_pool`, `L-26` backend, Direct `POST` mutation eligibility, `L-29` `p1_permissions.py` + `permlevel=1` + `guarded_crm.py` + `auth_hooks`) | `git merge-base --is-ancestor 6c457b32 65dd639c` = **`True`** (`0` diff backend quyền/service) |
| **PR #5 lịch sử (`d0842cc`, `a75c093`, `a9094b13`)** | `d0842cc` (Code `0d3f2bcbbf9715a4c0c783903ce750de8646583d`) -> `a75c093` (Code `6351048`) -> `a9094b13` / `5c37bf3` (Code `3f07b9b3`, CI `#41591527537`) | Bảo toàn lịch sử |
| **PR #5 Nghiệm thu Cuối (`65dd639` / Code `7bd7185`)** | - `HEAD`: `65dd639c0a63011dee54bc2b973595dd69d7560a` — [PR #5](https://github.com/nobita6986/new-crm/pull/5)<br>- Code SHA (`HEAD~1`): `7bd7185a35680d4f716cfe0b0dc1d6b7bfb46cb9` (`git diff 7bd7185..65dd639` chỉ chạm 4 tệp docs/evidence, **`0` dòng thay đổi trên `apps/`, `.github/`, `deploy/dev/`**).<br>- Synthetic merge (`refs/pull/5/merge`): `d9cfd2c81d401f4278139fa556b71a9decb2a575` (parents `6db1f3b` + `65dd639`).<br>- **CI P1 `#41613509233`**: **`209/209` backend JUnit + `12/12` UI JUnit + `170/170` HTTP PASS**, Artifact `13220970780`, SHA-256 **`82516cdfcc32674fc3d3bb4ead204e0b89304490a04a1ea9940ed6bce5ff3dcd`** (T1C tải `.zip`, băm SHA-256 khớp GitHub `digest` 100%, parse trực tiếp JSON/JUnit).<br>- **CI P0 `#41613509298`**: **`99/99` P0 core + `8/8` transport + `18/18` HTTP + `9/9` file-oracle + `74/74` remaining-gaps + `backup_restore` PASS**, Artifact `13220756000`, SHA-256 **`a85025b7d27498e813c15128f0d95ad79e175593433a464de462733cefa14ac2`** (T1C tải `.zip` & băm khớp 100%).<br>- **Manifest Runtime `p1-runtime-20261102/SHA256SUMS.txt`:** **`30/30` tệp khớp SHA-256 LF** trên `65dd639` (`0` lệch, `0` SĐT/token rò rỉ trên cả `deploy/dev/evidence/` và `deploy/p1/evidence/`).<br>- **Runtime cô lập của T1C (`t1c.localhost`):** `134/134` tệp nguồn `crm_cskh` mount thực chạy khớp 100% SHA-256 LF Git blob `7bd7185` ([t1c-delta-65dd639.json#L8-L31](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L8-L31)). | **ĐÃ XÁC MINH ĐỘC LẬP** |

### 2.2. Danh mục Artifact Kiểm định Độc lập của T1C (đã redact 100% PII/token)

Toàn bộ bằng chứng lưu tại [deploy/dev/evidence/t1c-p1-round2-20261102](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102):

| Artifact | SHA mục tiêu | Nội dung đo độc lập |
|---|---|---|
| **[t1c-delta-65dd639.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json)** | **`65dd639` / Code `7bd7185` / Backend `6c457b3` (PR #5 Nghiệm thu Cuối)** | Kiểm định delta trực tiếp trên `t1c.localhost` (sau `migrate × 2` + `90/90` `test_p1_regressions` + `12/12` `test_ui_p1_endpoints` + `8/8` `test_error_response_normalization` + audit i18n `613` dòng) với HTTP thật + Redis RQ worker thật (`0` SĐT/token thô):<br>- **Schema & DocPerm (`PASS`):** `tabCustom DocPerm = 0`, đủ `7` trường `permlevel = 1` (`selection_json`, `preview_json`, `actual_json`, `state_json`, `progress`, `error`, `lease_expires_at`), `18` dòng `DocPerm` chuẩn.<br>- **Mục 1 — `L-23` trên cả 3 API Kho & 6 chuyển dịch `a1..a6` (`PASS`):** Replay `a1..a6` ở cả Stage 1 (`a2` đang `queued` tại Đội B sau `revoke(return_to_pool=True)` ở Đội A) và Stage 2 (đã `assigned` Đội B) trên `pool_ui.list`, `customers.customer_pool`, `customers.pool_list`; đếm trước phân trang (`page_size=1`: `4/4/4` và `2/2/2`); positive control `Leader B` (`7` Stage 1, `4` `batch_l23`); `BOD` `0` PII (`search=403`); mọi filter/search `PASS`.<br>- **Mục 2 — `L-29` trên REST, `frappe.client.*`, Desk + Scoped APIs & RQ `G1-05`/`G1-11` (`PASS`):** Chặn `403` doc riêng Đội B; tước sạch `permlevel=1` trên doc chung & tường minh `fields=[...]`; chặn `403` mọi filter-oracle `permlevel=1` (GET & POST) và mọi thao tác ghi generic; xác nhận `campaigns.*`, `run_history.runs`, `distribution.get_run` và chạy RQ thật `camp_rq` (`6 -> 3/3`).<br>- **Mục 3 — Direct `POST` Mutation & `L-26` (`PASS`):** `ownership.assign`/`reassign` (JSON & form) chặn đủ wrong team, disabled user, non-`Sales User`, disabled `v2` rule, `weight=0`, lệch `rule.team`, `capacity=1` (`409 CAPACITY_EXCEEDED`), `capacity=0` không giới hạn (`200`), `Closed` campaign (`409 CONFLICT` trên `assign`/`reassign`/`preview`/`execute`), Active Claim chéo (`409 ALREADY_ASSIGNED`); `16/16` probe `L-26` trên đủ `8/8` endpoint list/history + `save_rule` + `execute` trả `400 INVALID_REQUEST` (`0` HTTP `500`). |
| [t1c-delta-a9094b1.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json) | `a9094b13` / Code `3f07b9b3` *(bảo toàn `L-25` + lịch sử phát hiện `L-29`)* | Đóng trọn vẹn **`L-25`** (`CSKH Import Batch Lead`, 3 index MariaDB, RBAC CRUD HTTP × 5 actor, import 2 batch `created` + `reused`, sanitize `'=SUM` & HTML tag, filter `batch=` trước & sau khi có `Work Item`); bằng chứng gốc phát hiện `L-29` và phần còn sót của `L-23`/`L-26` backend trên `3f07b9b3` |
| [t1c-delta-a75c093.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a75c093.json) | `a75c093` / Code `6351048` *(bảo toàn)* | Import 255 dòng (`L-18..20`), Async RQ `120` lead (`72/48`), recheck `RULE_CHANGED`, worker `exit 137` + lease recovery (`10/10 = 5/5`), đóng `L-16, L-17, L-21, L-22` khớp SQL |
| [t1c-ui-api-d0842cc.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-ui-api-d0842cc.json) | `d0842cc` (PR #5 lịch sử) | Ma trận 6 route HTML × 9 actor, 7 static asset, CSRF/upload/10MB/format, ma trận tải file lỗi & `/private/files/...` × 9 actor, bằng chứng gốc phát hiện `L-23..L-27` |
| [t1c-manifest-audit-64b7486.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-manifest-audit-64b7486.json), [t1c-delta-64b7486.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-64b7486.json) | `64b7486` (PR #4 lịch sử) | Manifest `30/30`, `migrate × 2`, `test_p1_chaos` + `p1_chaos_harness`, bằng chứng lịch sử trên `64b7486` |
| [t1c-delta-3025495.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-3025495.json), [t1c-race-3025495.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-race-3025495.json), [t1c-security-http-3025495.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-security-http-3025495.json), [t1c-sec-05-08-10-3025495.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-sec-05-08-10-3025495.json), [t1c-scale-probe.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-scale-probe.json) | `3025495` / `9ec881f` *(bảo toàn)* | `backup/restore` khớp SHA-256 `8/8` bảng, race `2` & `5` OS process (`0` trùng), fairness `1.000 = 500/300/200` & `100 × 1 = 50/30/20`, ma trận bảo mật nền `SEC-01..SEC-10` |

---

## 3. Lịch sử Đã Bảo toàn — Delta Phân phối Nền RQ, Polling/Reload & Đóng `L-16…L-22` trên `a75c093` (`6351048`)

### 3.1. Xác minh 7 Chốt Phân phối Nền RQ, Polling & Reload Recovery (Lịch sử `a75c093`)

Đối chiếu mã nguồn ([api/distribution.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/distribution.py), [services/distribution.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/distribution.py#L318-L760), [jobs/distribution_worker.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/jobs/distribution_worker.py), [public/cskh/campaigns.js](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/public/cskh/campaigns.js#L61-L265)) và thực chạy HTTP + Redis RQ worker thật trên `t1c.localhost` ([t1c-delta-a75c093.json#L81-L308](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a75c093.json#L81-L308)):

1. **Enqueue bất đồng bộ & không báo hoàn tất giả:** `POST crm_cskh.api.distribution.execute` cho chiến dịch `120` khách trả ngay HTTP `200` với `run_id="h4kjn78m2o"`, `status="queued"`, `progress=0`, `total=120`, `created=0` (`0` Work Item được tạo trong request web; job đẩy vào hàng đợi RQ `long`). Trên UI `campaigns.js`, trạng thái `queued`/`running` chỉ hiện hộp thông tin tiến độ (`.cskh-alert.is-info`), tuyệt đối không báo `"Đã phân phối xong."` trước khi job kết thúc.
2. **Chống Double-Submit ở cả 3 pha (`queued`, `running`, `completed`):** Gọi lặp `POST distribution.execute` với cùng `preview_id` khi đang `queued` (`#L102-L104`), khi worker đang giữ lease active `running` (`#L270`), và sau khi đã `completed` (`#L171`) đều trả lại đúng `run_id` hiện hữu mà không đẩy job RQ trùng, giữ nguyên `created=120` (`0` Work Item thừa).
3. **Tiến độ bền vững đa chunk (`> CHUNK_SIZE=100`) & Đối chiếu `reconcile()` / SQL:** Khi RQ worker khởi chạy trên lô `120` khách với trọng số `60/40`, `GET distribution.get_run` ghi nhận đầy đủ chuỗi chuyển trạng thái `["queued", "running", "completed"]` (`#L145-L169`), kết thúc với `assigned = {"staff_a": 72, "staff_b": 48}`, khớp 100% cả `reconcile()` lẫn truy vấn SQL trực tiếp (`120` `CSKH Work Item`, `120` `CSKH Active Claim`, `120` `ToDo` Open, `120` `DocShare`, `120` `CRM Notification` — `#L172-L193`).
4. **Phân quyền & Chống lộ PII trên endpoint Polling `distribution.get_run(run_id)`:** `CRM-ADMIN` HTTP `200`; `BOD` HTTP `200` (`has_raw_phone = false` ở cả pha `queued` lẫn `completed` — `#L110-L114`, `#L170`, `#L488`); `Leader A`, `Leader B`, `Staff A`, `Guest` bị chặn **`403 FORBIDDEN`** (`#L115-L129`).
5. **Phục hồi theo dõi sau F5 / Reload (`recoverActiveRun`):** `campaigns.js` lưu run đang chạy vào `sessionStorage` (`cskh_active_dist_run`) đồng bộ với query `?run_id=<id>`, đồng thời tự động gắn lại từ `run_history.runs` trang 1 nếu còn đợt chạy `queued`/`running`; single-flight timer `1000ms` tự nối lại cho tới khi hoàn tất và dịch đầy đủ lỗi `[RULE_CHANGED]` sang tiếng Việt (`Quy tắc phân công đã thay đổi sau khi xem trước.`, xác minh tại [async-distribution-delta-e2e.json](file:///D:/HRP-app/t1c-verify/deploy/p1/evidence/async-distribution-delta-e2e.json)).
6. **Recheck `rule_version` & Quyền tại thời điểm Worker thực thi:** Thay đổi rule (`save_rule` làm tăng `rule_version` lên `2`) trong lúc run `h91tqmbg9c` đang nằm ở `queued` khiến RQ worker từ chối cấp phát ngay khi nhận job, chuyển run sang `status="failed"`, `created=0`, `error="RULE_CHANGED: The assignment rules changed after the preview."` (`#L196-L214`).
7. **Khôi phục sau Hard-Kill `os._exit(137)` giữa Batch nhờ `lease_expires_at`:** Tiêm lỗi tiến trình con bị giết cứng `exit 137` ngay sau khách thứ `6/10` của run `hg5ajo5jo9` (`#L247-L307`): DB bền vững ở `status="running", progress=6, created=6 (3/3)`; gọi `execute` khi lease còn hiệu lực không bị chạy chồng (`status="running"`); ngay khi `lease_expires_at` hết hạn, gọi `POST distribution.execute` chuyển run về `queued` để RQ worker thật chạy nốt `4` khách còn lại, về đích `status="completed", progress=10, created=10` với tỷ lệ công bằng chính xác **`5/5` (`0` trùng, `0` mồ côi)**. *(Ghi chú UX `retryRun()` khi `completed_with_errors` nêu ở mốc `a75c093` nay cũng đã được T1A bổ sung fallback `preview -> execute` trên `7bd7185` — xem Mục 5.4).*

### 3.2. Ma trận Đóng Trọn vẹn 7 Finding `L-16 → L-22` trên `a75c093` (`6351048`)

| ID | Mô tả lỗi | PR #4 `64b7486` | PR #5 `d0842cc` | **Tích hợp `a75c093` (`6351048`)** | Bằng chứng độc lập trên `t1c.localhost` ([t1c-delta-a75c093.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a75c093.json)) |
|---|---|---|---|---|---|
| **L-16** | Chuẩn hóa mã lỗi JSON `p1_code` trên HTTP `4xx` (kể cả `404 NotFound`) | PARTIAL | PARTIAL (`404` `p1_code=null`) | **CLOSED (PASS 100%)** | [p1_errors.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/p1_errors.py) gắn `_cskh_p1_error_payload` vào `frappe.local` trước khi hook P0 biến đổi ngoại lệ: `GET distribution.get_run(run_id="RUN-DOES-NOT-EXIST-999")` trả HTTP **`404`** với `code="NOT_FOUND", p1_code="NOT_FOUND"`, đồng thời probe File-oracle P0 (`/api/resource/File/...` tồn tại vs không tồn tại bởi `Staff A`) vẫn giữ nguyên **`404` byte-identical** (`#L309-L324`). |
| **L-17** | `CRM-ADMIN` (không có `System Manager`) bị `scope="self"` | OPEN | FIXED | **CLOSED (PASS)** | `CRM-ADMIN` (`crm_admin_has_system_manager = false`) nhận `scope="system"` khớp 100% `BOD` (`scope="all"`) và SQL: `unique=136, assigned=128, unassigned=3, revoked=5, closed=1, active_claims=128, active_campaigns=6, failed_runs=1` (`#L24-L26`, `#L496-L547`). |
| **L-18** | `imports.confirm()` đặt `CONFIRMED` làm nổ RQ worker ở lần confirm đầu | OPEN | FIXED | **CLOSED (PASS)** | Batch `h1u3rf3485` (CSV **255 dòng**): gọi `POST imports.confirm` **đúng 1 lần duy nhất** trả `first_confirm_status="QUEUED"` -> RQ worker `long` chạy thẳng sang `COMPLETED_WITH_ERRORS` (`#L29-L43`). |
| **L-19** | Worker cắt `[:200]` dòng làm mất dữ liệu hợp lệ sau dòng 200 | FIXED | FIXED | **CLOSED (PASS)** | Batch 255 dòng (`250` hợp lệ + `5` lỗi): dù `preview` cắt `200` dòng (`preview_truncated=true`), 1 lần `confirm` tạo đủ **`250/250` `CRM Lead` và `250/250` `CSKH Identity Index`** (`#L31-L53`). |
| **L-20** | Thiếu dòng lỗi `>200` trong `error_artifact` & cộng đúp `error_rows` | FIXED | FIXED | **CLOSED (PASS)** | 5 dòng lỗi đặt ở các dòng **`[10, 150, 215, 240, 256]`** (3 dòng `> 200`): `final_totals.error_rows = 5` (gọi lại `confirm` sau khi xong vẫn giữ nguyên `5`, không cộng đúp); `download_error_file` trả `200` cho `Admin` (`403` cho `Staff A` & `BOD`) chứa chính xác đủ 5 dòng `[10, 150, 215, 240, 256]` (`#L44-L65`). |
| **L-21** | `ownership.revoke` ép `"false"` form-urlencoded thành `"queued"` | OPEN | OPEN | **CLOSED (PASS)** | [api/_params.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/_params.py) + [api/ownership.py#L68](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/ownership.py#L68): toàn bộ **`8/8` biến thể** (`False`, `"false"`, `0`, `"0"` -> `"revoked"`; `True`, `"true"`, `"1"` -> `"queued"`) qua cả JSON lẫn `application/x-www-form-urlencoded` đều PASS (`200`), và truyền giá trị rác `"maybe"` trả đúng **`400 INVALID_REQUEST`** trên cả JSON lẫn form (`#L325-L391`). |
| **L-22** | Dashboard `Leader` hụt `revoked`/`closed`/`active_claims` hàng `queued` + lệch `unique_customers` | OPEN | OPEN | **CLOSED (PASS)** | [services/reporting.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/reporting.py) dùng chung predicate `_work_item_scope_clause(scope, alias="wi")` cho mọi trạng thái: đối chiếu HTTP vs SQL khớp 100% trên cả 4 vai trò (`CRM-ADMIN`: `136/128/3/5/1/128`; `BOD`: `136/128/3/5/1/128`; **`Leader A`: `unique=132, assigned=124, unassigned=3, revoked=4, closed=1, claims=124`**; **`Leader B`: `unique=5, assigned=4, unassigned=0, revoked=1, closed=0, claims=4`**; `Staff A`: `403 FORBIDDEN` — `#L494-L604`). |

---

## 4. Lịch sử Đã Bảo toàn — Kết quả Kiểm định Delta `L-23…L-29` trên PR #5 `a9094b13` (Code SHA `3f07b9b3`, Backend Ancestor `c997df76`)

> *Mục 4 giữ nguyên trạng kết luận lịch sử tại mốc `a9094b13` (`3f07b9b3`), nơi **`L-25` và `L-24` (whitelisted API) được đóng trọn vẹn** còn `L-23` backend, `L-26` backend và `L-29` được phát hiện để sửa dứt điểm ở Mục 5 (`65dd639` / `7bd7185` / `6c457b3`).*

### 4.1. `L-25` & DocType `CSKH Import Batch Lead` (`CLOSED / PASS` từ mốc `a9094b13` và được bảo toàn trên `65dd639`)

Kiểm chứng trực tiếp trên `t1c.localhost` sau `bench migrate × 2` ([t1c-delta-a9094b1.json#L32-L246](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json#L32-L246)):
- **Schema, Index & Migration:** Bảng `tabCSKH Import Batch Lead` tạo đủ `cskh_domain_unique` (`Non_unique=0` trên `(import_batch, lead)`), `cskh_lookup_0 (lead, import_batch)`, `cskh_lookup_1 (import_batch, outcome)`; patch `p1_import_batch_lead_constraints` ghi nhận trong `Patch Log` và chạy `migrate × 2` hoàn toàn idempotent; chèn trùng `(import_batch, lead)` bị từ chối ngay (`unique_constraint_db_duplicate_rejected = true`).
- **Quyền CRUD HTTP `/api/resource/CSKH Import Batch Lead` × 5 actor (`#L203-L244`):** `CRM-ADMIN` chỉ được đọc (`list=200, get_doc=200`, còn `POST/PUT/DELETE` đều **`403`**); `BOD`, `Leader A`, `Staff A`, `Guest` đều bị chặn **`403`** trên mọi thao tác `LIST/GET/POST/PUT/DELETE`.
- **Mapping, Sanitize & Membership qua 2 đợt Import thật (`batch_1 = jd41qv253n`, `batch_2 = je47lb3bme`):**
  - Đổi mapping trên cùng `idempotency_key` bị chặn **`409 CONFLICT`** (`#L71-L74`).
  - Lead mới nhận đúng `first_name`/`lead_name`/`source` (`cskh_is_placeholder_name=0`); công thức `=SUM(1;2)` / `@Tiktok` bị khóa thành `'=SUM(1;2)` / `'@Tiktok`; thẻ `<script>` và `<img onerror>` bị tước sạch; dòng trống tên nhận `"Chưa rõ tên"` (`cskh_is_placeholder_name=1`, vẫn giữ `source="Hotline"`).
  - Batch 2 import đè 2 SĐT cũ (`p1`, `p6`) + 3 SĐT mới (`p7..p9`): ghi đúng `created_rows=3, reused_rows=2`, **không ghi đè** `first_name`/`source`/`cskh_is_placeholder_name` của `p1` và `p6`, tạo đủ `5` liên kết (`2 reused + 3 created`), và filter `batch=` khớp chính xác cả trước và sau khi tạo `Work Item`.

### 4.2. Ghi nhận Lịch sử Các Điểm Tồn tại Tại Mốc `a9094b13` (`3f07b9b3`) Trước Bản Sửa `6c457b3` / `7bd7185`

- **`L-23` trên `a9094b13` (`PARTIAL`):** `pool_ui.list` đã `PASS`, nhưng `customers.customer_pool` và `customers.pool_list` ở `c997df76` vẫn lộ khách Đội B sang `Leader A` ([t1c-delta-a9094b1.json#L280-L391](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json#L280-L391)). -> **Đã đóng hoàn toàn trên `65dd639` (xem Mục 5.1).**
- **`L-24` (`PASS`) & `L-29` trên `a9094b13` (`OPEN`):** `run_history.runs` (`list` & `?run_id=`) và `distribution.get_run` đã `PASS` ([t1c-delta-a9094b1.json#L393-L597](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json#L393-L597)), nhưng generic Frappe REST `/api/resource/CSKH Distribution Run|Campaign|Assignment Rule` tại `a9094b13` còn cho `Leader A` đọc doc riêng Đội B và raw JSON chiến dịch chung (`#L598-L610`). -> **Đã đóng hoàn toàn trên `65dd639` (xem Mục 5.2).**
- **`L-26`, `L-27`, `L-28` trên `a9094b13` (`PARTIAL`):** 4 API UI đã trả `400`, nhưng 4 API Backend (`customers.my_list`, `pool_list`, `customer_pool`, `ownership.history`) tại `a9094b13` còn nổ `500 ValueError` (`#L663-L722`), thiếu test hồi quy cho `customers.*`/`L-29`, và §7.1–§7.8 `P1-Exit-Report.md` chưa gắn nhãn lịch sử. -> **Đã đóng hoàn toàn trên `65dd639` (xem Mục 5.3 & 5.4).**

---

## 5. Kiểm định Nghiệm thu Cuối trên PR #5 `65dd639` (Code SHA `7bd7185`, Backend PR #4 `6c457b3`, Synthetic Merge `d9cfd2c8`)

### 5.1. Nhiệm vụ 1 — Replay `L-23` trên Cả 3 API Kho & Mọi Chuyển dịch `A → B` (`CLOSED / PASS`)

T1C kiểm tra mã nguồn ([api/pool_ui.py#L46-L56](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/pool_ui.py#L46-L56), [services/reporting.py#L412-L937](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/reporting.py#L412-L937)) và chạy thực tế qua HTTP thật trên `t1c.localhost` ([t1c-delta-65dd639.json#L74-L132](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L74-L132)) với đầy đủ **6 kịch bản chuyển dịch `A → B`** cùng positive controls Đội A & Đội B:
- `a1`: `assigned` Đội A (`camp_a_pool`) -> `revoke(return_to_pool=False)` (`status='revoked'`) -> `assigned` cho `staff_c` trong `camp_b_pool`.
- `a2` *(trọng tâm bản sửa `6c457b3`)*: `assigned` Đội A -> **`revoke(return_to_pool=True)`** (để lại `wi_a2_a` mang `status='queued', assigned_to=NULL` ngay trong `camp_a_pool`!) -> chuyển sang `camp_b_pool`:
  - **Stage 1:** `a2` đang **`queued` + giữ `CSKH Active Claim` trong `camp_b_pool`** (chưa gán nhân viên, trong khi `wi_a2_a` vẫn `queued` ở `camp_a_pool`).
  - **Stage 2:** `a2` được **`assign` cho `staff_c` trong `camp_b_pool`**.
- `a3`: `closed` ở Đội A -> `assigned` cho `staff_c` trong `camp_b_pool`.
- `a4`: `assigned` Đội A -> `revoke(return_to_pool=False)` -> `queued` trong chiến dịch riêng `camp_b_pool` (ngay cả khi chưa có `Active Claim`).
- `a5`: `assigned` cho `staff_a` trong chiến dịch chung `camp_sh_pool` (`team_scope = NULL`) -> `revoke(return_to_pool=False)` -> `assigned` cho `staff_c` trong `camp_b_pool`.
- `a6`: `assigned` cho `staff_a` trong chiến dịch chung `camp_sh_pool` -> **`reassign` trực tiếp sang `staff_c` (Đội B)** ngay trong cùng `camp_sh_pool`.
- **Positive controls:** Đội A có `stay_a_q1`, `stay_a_q2` (`queued` trong `camp_a_pool`), `stay_a_rev` (`revoked`), `stay_a_asg` (`assigned` `staff_a`); Đội B có `pure_b_q` (`queued` `camp_b_pool`) và `pure_b_asg` (`assigned` `staff_c`); lô `batch_l23` gắn `[a1, a2, a3, stay_a_q1, pure_b_asg]`.

| API Kho khách | Stage 1 (`a2` đang `queued` tại Đội B, `page_size=1` trên `camp_a_pool`) | Stage 2 (cả `a1..a6` ở Đội B, `batch=batch_l23` & không lọc) | Positive Control `Leader B` & Bảo vệ `BOD` / `Staff A` |
|---|---|---|---|
| `crm_cskh.api.pool_ui.list` ([pool_ui.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/pool_ui.py)) | **PASS:** `Leader A` nhận `len(items)=1`, **`total=4, unique_customers=4, work_items=4`** (đúng 4 khách Đội A, ẩn sạch `a2` dù `wi_a2_a` còn `queued` ở `camp_a_pool`); `0` rò rỉ `a1..a6` | **PASS:** `Leader A` không lọc thấy `11` khách trong scope, lọc `batch=batch_l23` thấy đúng **`total=1`** (`stay_a_q1`), `?campaign=camp_b_pool` trả `0`, mọi `?search=` (SĐT `+84`/`0...`/tên của `a1..a6`) & `?source=` trả `0` | `Leader B` thấy `7` khách ở Stage 1 (`a2_visible=true`) và thấy đúng **`4` khách** trong `batch_l23` (`a1, a2, a3, pure_b_asg`); `BOD` `scope="all"`, **`bod_zero_pii=true`**, `?search=` -> **`403 FORBIDDEN`**; `Staff A` -> **`403`** |
| `crm_cskh.api.customers.customer_pool` ([reporting.py#L684-L937](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/reporting.py#L684-L937)) | **PASS:** `Leader A` nhận `len(items)=1`, **`total=4, unique_customers=4, work_items=4`**, ẩn sạch `a1..a6` trước `LIMIT`/`OFFSET` | **PASS:** Đồng nhất 100% với `pool_ui.list` (`Leader A` không lọc `11`, `batch=batch_l23` `total=1`, `?campaign=camp_b_pool` `0`, mọi `?search=` & `?source=` của `a1..a6` trả `0`) | `Leader B` thấy `7` khách Stage 1 (`a2_visible=true`) & `4` khách `batch_l23`; `BOD` **`bod_zero_pii=true`**, `?search=` -> **`403 FORBIDDEN`**; `Staff A` -> **`403`** |
| `crm_cskh.api.customers.pool_list` ([reporting.py#L530-L628](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/reporting.py#L530-L628)) | **PASS:** Dù DB có 3 hàng `queued` trong `camp_a_pool` (`stay_a_q1, stay_a_q2, wi_a2_a`), `Leader A` với `page_size=1` nhận **`total=2, unique_customers=2, work_items=2`** (chặn `wi_a2_a` ngay trong SQL trước phân trang!) | **PASS:** `Leader A` lọc `batch=batch_l23` nhận đúng **`total=1`** (`stay_a_q1`), `0` xuất hiện `a1..a6`, `staff_c` hay `camp_b_pool` trong raw JSON | `Leader B` ở Stage 1 thấy đủ **`3` hàng `queued`** (`a2, a4, pure_b_q`, `a2_visible=true`), sang Stage 2 sau khi `assign` `a2` thì `batch_l23` còn `0` hàng `queued`; `BOD` **`bod_zero_pii=true`**; `Staff A` -> **`403`** |

### 5.2. Nhiệm vụ 2 — Replay `L-29` trên REST, `frappe.client.*` & Desk + Xác nhận Scoped APIs & Rà Lại `G1-05` / `G1-11` (`CLOSED / PASS`)

T1C kiểm chứng cả cấu trúc phân quyền MariaDB (`tabCustom DocPerm = 0`, đủ `7` cột `permlevel = 1`, `18` dòng `tabDocPerm` chuẩn — [t1c-delta-65dd639.json#L32-L72](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L32-L72)) lẫn replay trực tiếp qua HTTP với đúng từng actor (`CRM-ADMIN`, `BOD`, `Leader A`, `Leader B`, `Staff A`, `Guest` — `#L133-L150`):

1. **Frappe REST (`/api/resource/CSKH Campaign`, `CSKH Assignment Rule`, `CSKH Distribution Run`):**
   - **Document riêng Đội B (`camp_b_l29`, `rule_b_l29`, `run_b_l29`) & Rule nhân sự Đội B trong chiến dịch chung (`rule_sh_b_l29`):** `GET /api/resource/<DocType>/<name>` bởi `Leader A`, `Staff A`, `Guest` đều bị chặn cứng **`403`** (`401`/`403` với `Guest`), `0` rò marker bí mật Đội B.
   - **Document chiến dịch chung (`camp_sh_l29`, `run_sh_l29`) & list `fields` JSON tường minh:**
     - `GET /api/resource/CSKH Campaign/<camp_sh_l29>` và `GET /api/resource/CSKH Campaign?fields=["name","code","title","selection_json"]`: `Leader A`, `Leader B`, `BOD` nhận `200` cho metadata cơ bản nhưng **`selection_json` bị tước sạch 100%** (`"T1C_L29_SEL_SH_SECRET"` = `0` xuất hiện trong raw HTTP body); positive control `CRM-ADMIN` (`permlevel=1` `read=1`) đọc đủ `selection_json`.
     - `GET /api/resource/CSKH Distribution Run/<run_sh_l29>` và `GET /api/resource/CSKH Distribution Run?fields=["name","campaign","status","preview_json","actual_json","state_json","progress","error","lease_expires_at"]`: với `Leader A`, `Leader B`, `BOD`, **cả `6/6` cột `permlevel = 1` đều vắng mặt hoàn toàn khỏi payload** (`0` rò `staff_c`, mã Lead Đội B hay các chuỗi `T1C_L29_RAW_*`); positive control `CRM-ADMIN` đọc đủ cả 6 cột.
   - **Khóa cứng ghi/sửa/xóa generic (`POST`, `PUT`, `DELETE /api/resource/...`):** Mọi business role **kể cả `CRM-ADMIN`**, `BOD`, `Leader A`, `Staff A` đều nhận **`403`** (bắt buộc mọi thay đổi phải đi qua API nghiệp vụ có kiểm soát `campaigns.save`, `campaigns.save_rule`, `distribution.preview`/`execute`).
2. **`frappe.client.*` (`get`, `get_list`, `get_value`, `get_count`, `validate_link`, `set_value`, `insert`, `delete`):**
   - `Leader A` gọi `frappe.client.get` / `get_value` / `validate_link` vào `camp_b_l29`, `rule_b_l29`, `run_b_l29`, `rule_sh_b_l29` đều bị chặn **`403`**; `get_list` & `get_count` chỉ đếm/trả đúng các bản ghi thuộc Đội A + chiến dịch chung (với `CSKH Assignment Rule` chỉ trả `rule_a_l29` + `rule_sh_a_l29`, ẩn sạch `rule_sh_b_l29` của `staff_c`).
   - Khi `Leader A` hoặc `BOD` gọi `frappe.client.get` / `get_value` / `get_list(fields=["name","preview_json","actual_json","state_json","progress","error","lease_expires_at"])` trên `run_sh_l29` (hoặc `selection_json` trên `camp_sh_l29`), toàn bộ trường `permlevel = 1` bị tước sạch (`get_value` trả `{}`).
   - **Chặn triệt để Filter-Oracle trên cột `permlevel = 1` ([p1_permissions.py#L502-L526](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/p1_permissions.py#L502-L526) + [guarded_crm.py#L315-L468](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/guarded_crm.py#L315-L468)):** Mọi truy vấn `filters` hoặc `or_filters` chạm vào `actual_json`, `preview_json`, `state_json`, `progress`, `error`, `lease_expires_at`, `selection_json` qua REST `/api/resource/*`, `frappe.client.get_list` (cả GET query lẫn POST JSON) và `frappe.client.get_count` bởi `Leader A` hoặc `BOD` đều bị chặn **`403`** (`CRM-ADMIN` vẫn lọc được bình thường `200`).
   - `frappe.client.set_value` / `insert` / `delete` bởi `CRM-ADMIN` và `Leader A` đều bị chặn **`403`**.
3. **Desk (`frappe.desk.form.load.getdoc`, `frappe.desk.reportview.get`/`get_list`/`get_count`, `frappe.desk.search.search_link`/`search_widget`):**
   - `getdoc` bởi `Leader A` trên `camp_b_l29` & `run_b_l29` trả **`403`**; trên `camp_sh_l29` & `run_sh_l29` trả `200` nhưng `docs[0]` đã bị tước sạch `selection_json` và 6 cột `permlevel = 1` của Run (`0` marker `T1C_L29_*`).
   - `reportview.get` với tường minh `fields=["name","preview_json","actual_json","state_json","progress","error"]` trả `values` chỉ gồm `["name"]`, và khi truyền `filters=[["CSKH Distribution Run","actual_json","like","%staff_c%"]]` bị chặn ngay **`403`**.
   - `search_link` & `search_widget` trên `CSKH Campaign`, `CSKH Assignment Rule`, `CSKH Distribution Run` bởi `Leader A` trả `0` kết quả Đội B (`Staff A` trả `403`/rỗng).
4. **Xác nhận Scoped APIs & Chạy lại Thực tế `G1-05` / `G1-11` sau khóa quyền `L-29` (`#L140-L149`):**
   - `campaigns.list`: `Leader A` thấy đúng chiến dịch Đội A + chiến dịch chung (`0` chiến dịch riêng Đội B).
   - `campaigns.rules`: `Leader A` bị chặn **`403 FORBIDDEN`** trên `camp_b_l29`; trên `camp_sh_l29`, `Leader A` chỉ nhận rule của `[staff_a]` (`Leader B` chỉ nhận `[staff_c]`, `BOD` nhận đủ `[staff_a, staff_c]`).
   - `run_history.runs`: trên `run_sh_l29`, `Leader A` nhận đúng `assigned={"staff_a": 1}, created=1, total=1, skipped=[]` (`0` xuất hiện `staff_c` hay Lead Đội B trong raw JSON).
   - **Phân phối nền RQ thật (`camp_rq`, `6` khách, trọng số `50/50` giữa `staff_a` & `staff_c`):** `CRM-ADMIN` gọi `POST distribution.preview` (`eligible_count=6`) -> `POST distribution.execute` (`status="queued"`) -> Redis RQ worker thật hoàn tất `status="completed", assigned={"staff_a": 3, "staff_c": 3}`; `BOD` gọi `distribution.get_run` nhận `200` (`3/3`, `0` PII); `Leader A` gọi `distribution.get_run` nhận `403 FORBIDDEN` và gọi `run_history.runs(run_id=...)` nhận `200` với đúng `assigned={"staff_a": 3}` (`0` rò `staff_c`). -> **Chứng minh trực tiếp bằng runtime rằng `L-29` đóng kín mặt phẳng REST/Desk mà hoàn toàn không làm suy giảm `G1-05` hay `G1-11`.**

### 5.3. Nhiệm vụ 3 — Kiểm định Direct `POST` Mutation Eligibility & `L-26` Toàn diện (`CLOSED / PASS`)

Kiểm chứng trực tiếp qua HTTP trên `t1c.localhost` ([t1c-delta-65dd639.json#L151-L175](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L151-L175)):

1. **Direct `POST` Mutation Eligibility (`crm_cskh.api.ownership.assign` & `crm_cskh.api.ownership.reassign`, cả JSON lẫn `application/x-www-form-urlencoded`):**
   - **Phân quyền vai trò:** `Leader A`, `BOD`, `Staff A` nhận `403 FORBIDDEN`; `Guest` nhận `401`/`403`.
   - **Team scope, tài khoản & vai trò:** `POST` trực tiếp gán/chuyển cho `staff_c` (Đội B ngoài `camp_mut_a.team_scope`), tài khoản `User.enabled = 0` (`disabled_email`), tài khoản ngoài cây không phù hợp (`outside`), hoặc `BOD` (thiếu `Sales User`) đều bị từ chối **`400 INVALID_REQUEST`** trên cả JSON lẫn form-urlencoded.
   - **Quy tắc phân công phiên bản mới nhất (`latest_version = 2`) & Sức chứa (`capacity_limit`):**
     - `staff_b` (`v1 enabled=1`, **`v2 enabled=0`**): cả `assign` lẫn `reassign` đều bị chặn **`400 INVALID_REQUEST`**.
     - `leader_a` khi `weight = 0` hoặc `rule.team = team_b` (lệch cây `team_a`): cả `assign` lẫn `reassign` đều bị chặn **`400 INVALID_REQUEST`**.
     - `staff_a` (`v1 capacity_limit=5`, **`v2 capacity_limit=1`**): lần `assign` thứ 1 thành công (**`200 OK`**), lần `assign` thứ 2 bị chặn **`409 CAPACITY_EXCEEDED`**; tương tự sau khi bật `staff_b` với `capacity_limit=1` và lấp đầy `1/1`, gọi `POST ownership.reassign` sang `staff_b` bị chặn **`409 CAPACITY_EXCEEDED`**.
     - **Positive control `capacity_limit = 0` (không giới hạn):** khôi phục rule của `leader_a` (`weight=10, team=team_a, capacity_limit=0`), thực hiện liên tiếp 2 lần `assign` + 1 lần `reassign` sang `leader_a` -> **cả `3/3` lần đều `200 OK`**.
   - **Chiến dịch `Closed` & Xung đột `CSKH Active Claim` chéo chiến dịch:**
     - Trên chiến dịch `Closed`, gọi trực tiếp `POST ownership.assign`, `POST ownership.reassign`, `POST distribution.preview` đều bị chặn **`409 CONFLICT`**.
     - Tạo `preview` khi chiến dịch đang `Active`, sau đó chuyển chiến dịch sang `Closed` qua `POST campaigns.save(status="Closed")` rồi gọi `POST distribution.execute(preview_id)` -> bị chặn chính xác **`409 CONFLICT`** ([services/distribution.py#L299-L300](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/distribution.py#L299-L300)).
     - Work Item hàng đợi ở `camp_mut_a` của khách đang giữ `CSKH Active Claim` ở `camp_mut_b`: gọi `POST ownership.assign` bị chặn **`409 ALREADY_ASSIGNED`**.
2. **`L-26` Validation trên đủ `8/8` Endpoint List/History + Endpoint Mutation:**
   - Toàn bộ **`16/16` probe tham số sai kiểu/vượt biên/boolean** trên đủ 4 API UI (`pool_ui.list`, `ownership_ui.assigned_list`, `import_history.history`, `run_history.runs`), **4 API Backend** (`customers.my_list`, `customers.pool_list`, `customers.customer_pool`, `ownership.history`), cùng `campaigns.save_rule(version=True)` và `distribution.execute(expected_rule_version="abc" | True)` đều trả đúng **HTTP `400` với `code="INVALID_REQUEST", p1_code="INVALID_REQUEST"` (`0` HTTP `500`)**.

### 5.4. Nhiệm vụ 4 — Đối chiếu CI, Fixture (`L-27`), Metadata & Tài liệu (`L-28`) trên Cùng SHA `65dd639` (`CLOSED / PASS` + 2 Ghi chú Nhỏ Không Chặn `O-01`, `O-02`)

1. **Đối chiếu CI & Fixture (`L-27` — `CLOSED / PASS`):**
   - **CI P1 `#41613509233`** (trên synthetic merge `d9cfd2c81d401f4278139fa556b71a9decb2a575` của `65dd639`): Artifact `13220970780` (SHA-256 `82516cdfcc32674fc3d3bb4ead204e0b89304490a04a1ea9940ed6bce5ff3dcd`) đạt **`209/209` P1 backend JUnit** (tăng từ `203` lên `209` với các test hồi quy mới trong [test_p1_regressions.py#L2888-L4021](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/tests/test_p1_regressions.py#L2888-L4021): `L-23` pre-pagination trên cả `pool_list`/`customer_pool`/`pool_ui`, `L-26` backend, `L-29` generic API/`permlevel=1`, direct `POST` mutation eligibility), **`12/12` UI JUnit** ([test_ui_p1_endpoints.py#L789-L1020](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/tests/test_ui_p1_endpoints.py#L789-L1020)), và **`170/170` HTTP transport cases PASS** (`failed_cases: []`).
   - **CI P0 `#41613509298`**: Artifact `13220756000` (SHA-256 `a85025b7d27498e813c15128f0d95ad79e175593433a464de462733cefa14ac2`) đạt **`99/99` P0 core + `8/8` error normalization + `18/18` HTTP + `9/9` file-oracle + `74/74` remaining-gaps + `backup_restore = success`**.
   - Thực chạy độc lập trên `t1c.localhost`: `test_p1_regressions` (`90/90` PASS trong `52.08s`), `test_ui_p1_endpoints` (`12/12` PASS), `test_error_response_normalization` (`8/8` PASS); rà mã nguồn xác nhận **fixture có dữ liệu thật hai đội + positive controls rõ ràng, `0` skipped, `0` nới assertion**.
2. **Đối chiếu Tài liệu, Metadata & i18n (`L-28` — `CLOSED / PASS`):**
   - [P1-Exit-Report.md](file:///D:/HRP-app/t1c-verify/docs/architecture/P1-Exit-Report.md) (`772` dòng): §7.0–§7.8 gắn nhãn rõ mốc lịch sử Đợt 1 (`d0842cc` / `0d3f2bcbbf97...`), §7.9 giữ nguyên văn tiếng Việt UTF-8 sạch (`0` lỗi `?`) của Đợt 2 (`a75c093` / `6351048`), §7.10 ghi rõ mốc Đợt 3 (`5c37bf3` / `3f07b9b3`), và §7.11 tổng hợp bảng 4 đợt khớp SHA code `7bd7185a35680d4f716cfe0b0dc1d6b7bfb46cb9` cùng hai bằng chứng mới [round4-final-integration-e2e.json](file:///D:/HRP-app/t1c-verify/deploy/p1/evidence/round4-final-integration-e2e.json) (`4/4` nhóm PASS) và [round4-http-e2e-report.json](file:///D:/HRP-app/t1c-verify/deploy/p1/evidence/round4-http-e2e-report.json) (`6/6` nhóm PASS); bản đính chính provenance `0d3f2bcbbf9715a4c0c783903ce750de8646583d` trong [async-distribution-delta-e2e.json](file:///D:/HRP-app/t1c-verify/deploy/p1/evidence/async-distribution-delta-e2e.json) được giữ nguyên vẹn; [schema-p1.md](file:///D:/HRP-app/t1c-verify/docs/architecture/schema-p1.md) và [api-contract-p1.md](file:///D:/HRP-app/t1c-verify/docs/architecture/api-contract-p1.md) nhất quán với code `6c457b3` / `7bd7185`.
   - `check_translations_audit.py`: **`used=210, declared=409, vi.csv=613 rows (614 file lines), 0 missing, 0 untranslated, 0 orphan`**; đồng thời hàm `retryRun()` trong [campaigns.js#L1408-L1445](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/public/cskh/campaigns.js#L1408-L1445) đã bổ sung fallback `preview -> execute` cho đợt `completed_with_errors`, khép lại luôn ghi chú UX nhỏ của T1C từ vòng trước.
3. **Hai Ghi chú Kỹ thuật Nhỏ Không Chặn (`O-01`, `O-02` — Non-blocking Observations):**
   - **`O-01` [TRIVIAL — Diễn giải tên hàm & số lượng probe HTTP trong văn xuôi §7.11 / `pr-body.md`]:** Văn xuôi tóm tắt tại [P1-Exit-Report.md#L711, #L721-L725](file:///D:/HRP-app/t1c-verify/docs/architecture/P1-Exit-Report.md#L711) và [deploy/p1/pr-body.md](file:///D:/HRP-app/t1c-verify/deploy/p1/pr-body.md) gọi tắt tên 3 helper (`enforce_p1_http_filter_guard`, `parse_strict_int`, điều kiện `campaign.status == "Active"`) và ghi số probe HTTP thời `77e07da` là `167/167`, trong khi tên hàm thật trong mã nguồn `6c457b3` / `7bd7185` là `enforce_p1_filter_field_permissions` ([p1_permissions.py#L514](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/p1_permissions.py#L514)), `parse_int` ([api/_params.py](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/_params.py)), `campaign_doc.status == "Closed"` ([services/ownership.py#L129](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/ownership.py#L129); còn `distribution` kiểm `status in ("Active", "Draft")`), và CI `#41613509233` thực tế đã tăng lên **`170/170` HTTP cases PASS**. Không ảnh hưởng bất kỳ hành vi runtime hay tính toàn vẹn SHA nào.
   - **`O-02` [LOW — Dropdown UI `ownership_ui.assignees` khi DB có sẵn nhiều version rule lịch sử]:** Trong luồng chuẩn trên UI (`campaigns.save_rule` cập nhật tại chỗ `version = 1`), `ownership_ui.assignees(campaign)` khớp 100% với backend (`[leader_a]` tại [t1c-delta-65dd639.json#L167-L169](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L167-L169)). Nếu trong DB chèn tay nhiều bản ghi `version = 1` (`cap=5, enabled=1`) và `version = 2` (`cap=1` hoặc `enabled=0`), hàm `_campaign_ineligible_users` ([api/ownership_ui.py#L84-L127](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/api/ownership_ui.py#L84-L127)) tính `cap_limit` trên `enabled_by_user` xuyên các version nên vẫn hiển thị nhân sự đó trong dropdown (`#L170-L174`), **nhưng tầng mutation backend `_require_campaign_target_eligibility` ([services/ownership.py#L142-L163](file:///D:/HRP-app/t1c-verify/apps/crm_cskh/crm_cskh/services/ownership.py#L142-L163)) khóa chặt theo `latest_version` và chặn ngay bằng `400 INVALID_REQUEST` / `409 CAPACITY_EXCEEDED`** (`0` rủi ro vượt quyền hay vượt sức chứa). Có thể đồng bộ `_campaign_ineligible_users` sang `latest_by_user` ở P2 nếu mở UI tạo đa version rule.

---

## 6. Bảng Tổng hợp `T1-01 → T1-20` & `SEC-01 → SEC-10` trên SHA Nghiệm thu Cuối `65dd639` (`7bd7185`)

### 6.1. Trạng thái `T1-01 → T1-20` (`20/20 PASS`)

- **Nâng lên `PASS` / Cập nhật ký cuối trên `65dd639` (`7bd7185` + `6c457b3`):**
  - **`T1-19` (Kho khách theo scope — trước đó `FAIL` trên `a9094b13`):** **PASS — T1C KÝ MỚI TRÊN `65dd639`** (`pool_ui.list`, `customers.customer_pool`, `customers.pool_list` đồng nhất SQL trước phân trang trên cả 6 chuyển dịch `a1..a6`, positive control `Leader B`, `BOD` `0` PII — [t1c-delta-65dd639.json#L74-L132](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L74-L132)).
  - **`T1-11` (Assign/Reassign thủ công & kiểm tra hợp lệ trực tiếp):** **PASS — T1C KÝ CẬP NHẬT TRÊN `65dd639`** (khóa chặt direct `POST` `ownership.assign`/`reassign` cho team/rule/weight/capacity/disabled/`Closed`/Active Claim + `ownership.history` `L-26` trả `400` — `#L151-L175`).
  - **`T1-07`, `T1-08` (Chiến dịch, Rule, `capacity=0` & Phân phối nền RQ sau `L-29`):** **PASS — T1C TÁI XÁC MINH TRÊN `65dd639`** (`campaigns.*`, `run_history.runs`, `distribution.get_run` và chạy RQ thật `camp_rq` `6 -> 3/3` — `#L140-L150`).
  - **`T1-20` (Việt hóa UI P1):** **PASS — TÁI KÝ TRÊN `65dd639`** (`210` dùng / `409` khai báo / `613` dòng `vi.csv`, `0` lỗi).
- **Bảo toàn `PASS` đầy đủ căn cứ (`15/20` hạng mục còn lại):** `T1-01..T1-06` (đã đóng đủ `L-18..20` trên `a75c093` & `L-25` trên `a9094b13`), `T1-09`, `T1-10`, `T1-12..T1-18`.

### 6.2. Trạng thái `SEC-01 → SEC-10` (`10/10 PASS`)

| Mã | Kịch bản Bảo mật ([P1.md §10.2](file:///D:/HRP-app/new-crm/docs/root-plan/P1.md#L565-L580)) | Verdict Lịch sử `a9094b13` | **Verdict Cuối `65dd639` (`7bd7185` + `6c457b3`)** | Bằng chứng khóa sổ |
|---|---|---|---|---|
| **SEC-01** | `Guest` gọi mọi route `/cskh/*`, whitelisted API & `/api/resource/CSKH *` | **PASS** | **PASS** | [t1c-ui-api-d0842cc.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-ui-api-d0842cc.json), [t1c-delta-65dd639.json#L135-L165](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L135-L165) |
| **SEC-02** | `Staff` / non-Admin không đọc/ghi Import Batch, Import Batch Lead, Campaign, Rule, Run hoặc gọi mutation | **PASS** | **PASS (Củng cố thêm khóa ghi generic `403` trên mọi DocType P1)** | [t1c-delta-a9094b1.json#L203-L244](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json#L203-L244), [t1c-delta-65dd639.json#L136, #L164](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L136) |
| **SEC-03** | `Staff A` không đọc khách/work item/history của `Staff B`/`Staff C` | **PASS** | **PASS** | [t1c-security-http-3025495.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-security-http-3025495.json), CI `#41613509233` (`170/170` HTTP) |
| **SEC-04** | `Leader A` không đọc khách, campaign, rule, run hay raw JSON của Đội B trên mọi mặt phẳng | FAIL (`L-23` backend & `L-29`) | **PASS — T1C KÝ MỚI TRÊN `65dd639` (`L-23` + `L-29` ĐÃ ĐÓNG TRỌN VẸN)** | [t1c-delta-65dd639.json#L74-L150](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L74-L150) |
| **SEC-05** | `BOD` xem báo cáo/kho/run tổng hợp với **`0` PII** (không SĐT, không tên, không search oracle, không `selection_json`/raw run JSON) | **PASS** | **PASS (Mở rộng kiểm cả `permlevel=1` & 3 API Kho trên `65dd639`)** | [t1c-delta-65dd639.json#L107-L131, #L135-L145](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L107-L131) |
| **SEC-06** | Sau `reassign` / `revoke` (`False` & `True`) / `close` sang Đội B, chủ cũ và `Leader` đội cũ mất quyền ngay trên mọi API | PARTIAL (`L-23` backend) | **PASS — T1C KÝ MỚI TRÊN `65dd639` (kiểm đủ `a1..a6` Stage 1 & Stage 2)** | [t1c-delta-65dd639.json#L74-L132](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L74-L132) |
| **SEC-07** | Private File raw import & error CSV bị khóa chặt (`404` byte-identical với non-Admin; chỉ `CRM-ADMIN` tải qua API kiểm soát) | **PASS** | **PASS (Bảo toàn + CI P0 `#41613509298` `file-oracle 9/9` PASS)** | [t1c-delta-a75c093.json#L44-L65, #L309-L324](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a75c093.json#L44-L65), Artifact `13220756000` |
| **SEC-08** | Chống CSV formula injection (`'=`, `'@`, `'+`, `'-`) và XSS HTML/JS trên tên/nguồn/lý do | **PASS** | **PASS (`xss_triggered = false`, `sanitize_cell` trên `L-25`)** | [t1c-delta-a9094b1.json#L75-L150](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json#L75-L150), [round4-final-integration-e2e.json](file:///D:/HRP-app/t1c-verify/deploy/p1/evidence/round4-final-integration-e2e.json) |
| **SEC-09** | Bắt buộc CSRF token trên mutation web & chuẩn hóa mã lỗi HTTP JSON (`0` leak stacktrace/SQL) | **PASS** | **PASS (`16/16` probe `L-26` trả `400 INVALID_REQUEST`, `0` HTTP `500`)** | [t1c-ui-api-d0842cc.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-ui-api-d0842cc.json), [t1c-delta-65dd639.json#L166](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L166) |
| **SEC-10** | Không leo thang quyền qua generic Frappe API (`assign_to`, phone lookup, `frappe.client.*`, `reportview`, `search_link`) | **PASS** | **PASS (Củng cố toàn diện bởi `L-29` + direct `POST` eligibility)** | [t1c-delta-65dd639.json#L133-L165](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L133-L165), Artifact `13220756000` (`74/74` P0 gaps) |

---

## 7. Bảng Tổng hợp Nghiệm thu Cuối 14 Gate `G1-01 → G1-14` (`14/14 PASS`) & Bàn giao T0/Owner

| Gate | Tiêu chí PASS ([P1.md §14](file:///D:/HRP-app/new-crm/docs/root-plan/P1.md#L695-L711)) | Verdict Lịch sử `a9094b13` (`3f07b9b3`) | **Verdict Nghiệm thu Cuối `65dd639` (Code `7bd7185`, Backend `6c457b3`)** | Actor thực chứng | Artifact / Bằng chứng khóa sổ |
|---|---|---|---|---|---|
| **G1-01** | P0 đã GO; code đúng version lock và ADR; không làm vỡ P0 | **PASS** | **PASS (T1C tái ký trên `65dd639` / merge `d9cfd2c8`)** | CI P0 `#41613509298` + T1C | Artifact `13220756000` (`a85025b7...`): `99/99` P0 core + `8/8` transport + `18/18` HTTP + `9/9` file-oracle + `74/74` gaps + `backup_restore` PASS |
| **G1-02** | DocTypes, indexes/unique, `permlevel=1`, install/migrate lặp lại được | **PASS** | **PASS (T1C tái ký trên `65dd639` gồm `7` cột `permlevel=1` & `0` `Custom DocPerm`)** | CI `#41613509233` + T1C (`migrate × 2`) | [t1c-delta-65dd639.json#L32-L72](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L32-L72), `p1-schema-indexes.txt` (`13220970780`), [t1c-delta-a9094b1.json#L33-L70](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json#L33-L70) |
| **G1-03** | Phone-only & mapped CSV/XLSX; valid/trùng/lỗi xử lý đúng từ đầu đến cuối | **PASS** | **PASS (Bảo toàn `L-18..20` & `L-25` — `0` thay đổi logic import so với `a9094b13`)** | `CRM-ADMIN` (HTTP + RQ worker) | [t1c-delta-a9094b1.json#L71-L246](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json#L71-L246), [t1c-delta-a75c093.json#L29-L65](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a75c093.json#L29-L65), CI `#41613509233` |
| **G1-04** | Không tạo trùng `normalized_phone` qua concurrent imports | **PASS** | **PASS (Bảo toàn race 2/5 OS process + multi-batch `L-25`)** | 2/5 OS process + multi-batch race | [t1c-race-3025495.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-race-3025495.json), `test_p1_regressions` (`90/90` PASS trên `65dd639`) |
| **G1-05** | Tạo/sửa/đọc scope campaign, weights, `capacity_limit` (`0` = không giới hạn), chặn chiến dịch `Closed` | **PASS** | **PASS — T1C RÀ LẠI & TÁI KÝ TRÊN `65dd639` SAU KHÓA QUYỀN `L-29`** | `CRM-ADMIN`, `BOD`, `Leader A/B`, `Staff A` | [t1c-delta-65dd639.json#L140-L165](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L140-L165) (scoped `campaigns.*` + `Closed` `409` + `capacity=0` unlimited `200` + RQ `camp_rq` `3/3`) |
| **G1-06** | `1.000 = 500/300/200` và batch nhỏ `100 × 1 = 50/30/20` đúng policy | **PASS** | **PASS (Bảo toàn + CI `#41613509233` `test_p1_distribution` PASS)** | `CRM-ADMIN` + DB truth | [t1c-scale-probe.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-scale-probe.json), [t1c-delta-a75c093.json#L150-L186](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a75c093.json#L150-L186) |
| **G1-07** | Không trùng owner/work item khi 2+ jobs chạy; rollback & retry an toàn; 0 fault hook prod | **PASS** | **PASS (Bảo toàn + manifest `30/30` khớp SHA-256 trên `65dd639`)** | `CRM-ADMIN`, worker `exit 137` | [t1c-delta-a75c093.json#L247-L307](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a75c093.json#L247-L307), `p1-runtime-20261102/SHA256SUMS.txt` (`30/30`) |
| **G1-08** | `A → B` / thu hồi có history đầy đủ, kiểm tra đủ eligibility khi POST trực tiếp và dọn quyền cũ đúng | PARTIAL (`L-26` `ownership.history`) | **PASS — T1C KÝ MỚI TRÊN `65dd639` (`7bd7185` + `6c457b3`)** | `CRM-ADMIN`, `Leader A/B`, `BOD`, `Staff A`, `Guest` | [t1c-delta-65dd639.json#L151-L175](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L151-L175) (direct `POST` JSON/form `assign` & `reassign` + `ownership.history?limit=abc|201` -> `400 INVALID_REQUEST`) |
| **G1-09** | BOD / Leader / Admin xem được kho/giao/chưa giao/theo team/người, totals trước phân trang & filter chính xác | FAIL (`L-23, L-26` trên `customers.*`) | **PASS — T1C KÝ MỚI TRÊN `65dd639` (`L-23` + `L-26` ĐÓNG TRÊN CẢ 3 API KHO)** | `CRM-ADMIN`, `BOD`, `Leader A/B`, `Staff A` | [t1c-delta-65dd639.json#L74-L132, #L166](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L74-L132), [t1c-delta-a75c093.json#L494-L604](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a75c093.json#L494-L604) |
| **G1-10** | `My Customers`: Staff chỉ thấy khách được giao, không sửa quyền/phân phối, validation `400` sạch | **PASS** | **PASS (T1C tái ký trên `65dd639` gồm `customers.my_list` `400 INVALID_REQUEST` & `0` background `403`)** | `Staff A/B/C`, `Outside` | [t1c-delta-65dd639.json#L166](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L166), [round4-final-integration-e2e.json](file:///D:/HRP-app/t1c-verify/deploy/p1/evidence/round4-final-integration-e2e.json) |
| **G1-11** | Bảo mật: `0` High/Critical cross-access / unauthorized mutation trong toàn P1 scope | FAIL (`L-23`, `L-29`) | **PASS — T1C RÀ LẠI & KÝ MỚI TRÊN `65dd639` (`10/10` `SEC-01..SEC-10` = `PASS`)** | 6 actor HTTP thật (`Admin`, `BOD`, `Leader A/B`, `Staff A`, `Guest`) | [t1c-delta-65dd639.json#L74-L166](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json#L74-L166), CI `#41613509233` (`170/170` HTTP PASS) |
| **G1-12** | Màn hình P1 đủ tiếng Việt theo glossary, không lỗi dịch nghiêm trọng | **PASS** | **PASS — T1C TÁI XÁC MINH & KÝ TRÊN `65dd639`** | Audit i18n (`210/409/613`) | `check_translations_audit.py` (`0` thiếu, `0` chưa dịch, `0` mồ côi) |
| **G1-13** | CI xanh đầy đủ phạm vi; batch lỗi retry được; HTTP contract & luồng phân phối nền ổn định | PARTIAL (`L-26, L-27`) | **PASS — T1C KÝ MỚI TRÊN `65dd639` (`209` P1 + `12` UI + `170` HTTP + `99` P0 PASS, `0` HTTP `500`)** | CI `#41613509233` & `#41613509298` + T1C | Artifact `13220970780` (`82516cdf...`), Artifact `13220756000` (`a85025b7...`), [t1c-delta-65dd639.json](file:///D:/HRP-app/new-crm/deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json) |
| **G1-14** | Đủ docs/schema/API/ADRs và `P1-Exit-Report.md` nhất quán trên 1 SHA tích hợp cuối | HOLD (`L-28`) | **PASS — T1C KÝ MỚI TRÊN `65dd639` (`P1-Exit-Report.md` §7.0–§7.11, `schema-p1.md`, `api-contract-p1.md` nhất quán; kèm 2 lưu ý nhỏ không chặn `O-01`, `O-02`)** | T1C rà hồ sơ & provenance | [P1-Exit-Report.md#L374-L772](file:///D:/HRP-app/t1c-verify/docs/architecture/P1-Exit-Report.md#L374-L772), [schema-p1.md](file:///D:/HRP-app/t1c-verify/docs/architecture/schema-p1.md), [api-contract-p1.md](file:///D:/HRP-app/t1c-verify/docs/architecture/api-contract-p1.md) |

### Kết luận Bàn giao Cuối cho T0 / Owner

- **Tổng kết kỹ thuật trên PR #5 `65dd639c0a63011dee54bc2b973595dd69d7560a` (Code SHA `7bd7185a35680d4f716cfe0b0dc1d6b7bfb46cb9`, chứa Backend PR #4 `6c457b327e59eead47cc9d0982c4ee1df4fe9e64`):**
  - Toàn bộ **`29/29` finding `L-01 → L-29` đã ĐÓNG (`CLOSED / PASS`)** bằng bằng chứng runtime độc lập.
  - Toàn bộ **`20/20` testcase kế hoạch `T1-01 → T1-20`**, **`10/10` kịch bản bảo mật `SEC-01 → SEC-10`**, và **`14/14` cổng nghiệm thu `G1-01 → G1-14` đều đạt `PASS`** (`0` blocker còn mở, `0` regression P0).
  - **Đề xuất trình T0 / Owner:** **ĐỦ ĐIỀU KIỆN KỸ THUẬT ĐỂ T0 / OWNER RA QUYẾT ĐỊNH `GO P2` VÀ HỢP NHẤT THEO QUY TRÌNH.** *(Ghi chú vận hành máy chủ: do máy host Windows khởi động lại trước `20:45`, các container `crm-cskh-p1-*` và `crm-cskh-p1-dev-*` đang ở trạng thái `Exited (255)` từ lúc reboot — theo đúng kỷ luật cô lập, T1C tuyệt đối không tự chạm vào 2 stack đó và đã dừng gọn `crm-cskh-t1c-*` sau khi trích xuất xong artifact).*

---

*Cam kết ranh giới T1C: Trong toàn bộ quá trình kiểm định trên `65dd639c0a63011dee54bc2b973595dd69d7560a` (`7bd7185` / `6c457b3`), T1C không sửa bất kỳ dòng code/test/config nào của T1A/T1B, không chạm `D:\HRP-app\p1-ui`, `D:\HRP-app\p1-foundation`, `crm-cskh-p1-*` hay `crm-cskh-p1-dev-*`, không merge PR, không gọi subagent/agent khác, và không mở lại P0.*
