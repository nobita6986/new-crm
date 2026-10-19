# GLOSSARY VIỆT HÓA & LỖI i18n ƯU TIÊN (P0)

> **Dự án:** CRM-CSKH
> **Chủ sở hữu tài liệu:** T1B
> **Trạng thái:** Khảo sát **tĩnh** trên `vi.po`; **chưa** render UI thật (chưa có DEV)
> **Tài liệu liên quan:** [`reuse-audit.md`](./reuse-audit.md), [`ui-extension-spike.md`](./ui-extension-spike.md), [`master-plan.md`](../root-plan/master-plan.md) §10, [`P0.md`](../root-plan/P0.md) §8, [`P2.md`](../root-plan/P2.md) §12.5, [`t1a-g009-vi-checklist.md`](../../deploy/dev/evidence/t1a-g009-vi-checklist.md) (checklist màn P0 + điều kiện thu screenshot)
> **Phiên bản:** 1.5 — 19/10/2026 (T1A: liên kết checklist G0-09 + ghi nhận thiếu bằng chứng runtime)

---

## 0. PROVENANCE

- Nguồn: `https://github.com/frappe/crm`
- Tag: `v1.87.0` — Commit: **`0d3910b456ef4fb913e77b29db011b3debc4f778`**
- File từ điển: `crm/locale/vi.po`
- Số liệu (đếm trên file tĩnh): **1782** `msgid` · **236** `msgstr ""` (chưa dịch) · **0** mục `#, fuzzy`.
- **Giới hạn:** chưa chạy site tiếng Việt → danh sách lỗi dưới đây là đọc tĩnh; cần screenshot xác nhận ngữ cảnh thực tế `[CẦN TEST DEV]`.

### 0.1. QUYẾT ĐỊNH & PHẠM VI (T0)

- **ADR-001:** runtime mục tiêu P0 là **Frappe Framework `v15.123.1`** (lựa chọn để kiểm chứng, **chưa** phải baseline cuối; v16 để khảo sát sau). Nội dung i18n ở đây đọc **tĩnh** từ `crm/locale/vi.po` của tag `v1.87.0`; **chưa** xác minh render trên framework nào.
- **AGPL-3.0:** cần **rà soát tuân thủ trước phát hành** (xem `reuse-audit.md` §0.3); tài liệu này **không** đưa kết luận pháp lý.
- Bảng dịch và lỗi ưu tiên là **đề xuất thiết kế**; **chưa** được xác nhận trên UI.

---

## 1. CƠ CHẾ i18n HIỆN CÓ (REUSE, KHÔNG XÂY MỚI)

| Thành phần | Evidence | Vai trò |
|---|---|---|
| Từ điển backend theo ngôn ngữ user | `crm/api/__init__.py:12`–`19` `get_translations()` → `frappe.translate.get_all_translations(language)` | API dịch |
| Nhúng vào SPA khi boot | `crm/www/crm.py:84` `"translated_messages": get_messages_for_boot()`; `frontend/src/App.vue:45` `setConfig('translatedMessages', window.translated_messages)` | Truyền bản dịch vào SPA |
| Hàm dịch `__()` + hỗ trợ context | `frontend/src/translation.js:14`–`35` (`translate(message, replace, context)`); global `__` `:3`–`6` | Dịch ở Vue |
| Chọn ngôn ngữ/timezone theo user | `frontend/src/components/Settings/PreferencesSettings.vue:63` (Language `Link → Language`), `:74` (Timezone) | Người dùng tự chọn |
| Đánh dấu dịch cho DocType | `crm_sales_hierarchy.json` cuối file (`"translated_doctype": 1`) | Dịch nhãn theo DocType |

**Nguyên tắc:** mọi UI mới của `crm_cskh` phải viết qua `__()` (Vue) / `_()` (Python); bổ sung bản dịch qua cơ chế app của Frappe (ví dụ `crm_cskh/translations/vi.csv`), **không** hard-code và **không** sửa `vi.po` core trực tiếp.

---

## 2. GLOSSARY NGHIỆP VỤ (chuẩn của dự án)

> Cột "Đang có trong `vi.po`" là giá trị tại commit khảo sát. `⚠` = cần sửa, `—` = chưa có/không áp dụng.

| English | Thuật ngữ chuẩn (dự án) | Đang có trong `vi.po` (file:line) | Hành động |
|---|---|---|---|
| Lead | Khách hàng tiềm năng *(theo ngữ cảnh: "Khách được giao" ở màn nhân viên)* | **`Chì`** — `vi.po:4399`–`4400` | **⚠ Sửa (P0, ưu tiên cao nhất)** |
| Leads | Khách hàng tiềm năng | `Khách hàng tiềm năng` — `vi.po:4479`–`4480` | Giữ |
| Deal | Cơ hội / Thương vụ | `Deal` (chưa dịch) — `vi.po:2117`–`2118` | **⚠ Sửa** |
| Lead Owner | Nhân viên phụ trách | `Chủ sở hữu khách hàng tiềm năng` — `vi.po:4420`–`4421` | **⚠ Rút gọn theo chuẩn** |
| Sales User | Nhân viên tuyển dụng/CSKH | — | Thêm vào app |
| Sales Manager | Trưởng nhóm | — | Thêm vào app |
| Assigned To | Người phụ trách | `Được giao Cho` — `vi.po:751`–`752` | **⚠ Sửa (sai ngữ pháp)** |
| Assignment | Phân phối / Giao khách | — | Thêm vào app |
| Call Log | Nhật ký cuộc gọi | `Nhật ký cuộc gọi` — `vi.po:1283`–`1284` | Giữ |
| Contact | Liên hệ | `Liên hệ` — `vi.po:1749`–`1750` | Giữ |
| Note (bản ghi ghi chú) | Ghi chú | **`Lưu ý`** — `vi.po:5362`–`5363` | **⚠ Sửa (sai ngữ cảnh)** |
| Task | Nhiệm vụ | `Nhiệm vụ` — `vi.po:7406`–`7407` | Giữ |
| Organization | Tổ chức / Công ty | `Tổ chức` — `vi.po:5589`–`5590` | Giữ |
| Dashboard | Báo cáo tổng quan | `Trang tổng quan` — `vi.po:2058`–`2059` | Cân nhắc theo ngữ cảnh |
| Mobile No. | Số điện thoại | `Số di động` — `vi.po:4850`–`4851` | Giữ/đồng bộ "Số điện thoại" |
| Status | Trạng thái | `Trạng thái` — `vi.po:7246`–`7247` | Giữ |
| Source | Nguồn | `Nguồn` — `vi.po:7141`–`7142` | Giữ |
| **Contact Attempt** | Lần liên hệ / Ghi nhận liên hệ | — (mới) | Thêm vào app |
| **Contact Outcome** | Kết quả liên hệ | — (mới) | Thêm vào app |
| **Follow-up / Callback** | Hẹn liên hệ lại / Chăm sóc tiếp | — (mới) | Thêm vào app |
| **No Answer** | Không nghe máy | — | Thêm vào app |
| **Connected** | Đã kết nối | — | Thêm vào app |
| **Overdue** | Quá hạn | — | Thêm vào app |
| **SLA** | Thời hạn xử lý | — | Thêm vào app |
| **Self-reported** | Nhân viên tự báo cáo | — | Thêm vào app |
| **Verified** | Đã xác minh | — | Thêm vào app |
| **Do Not Contact** | Không liên hệ nữa | — | Thêm vào app |
| **Provisional** | Tạm tính | — | Thêm vào app |
| **On Hold** | Chờ chăm sóc lại | — | Thêm vào app |
| **In Progress** | Đang chăm sóc | — | Thêm vào app |

---

## 3. LỖI i18n ƯU TIÊN CẦN RÀ (P0)

| # | Ưu tiên | Chuỗi | Hiện tại | Đề xuất | Evidence |
|---|---|---|---|---|---|
| 1 | **P0 — Cao** | `Lead` (số ít) | `Chì` | `Khách hàng tiềm năng` | `vi.po:4399`–`4400` |
| 2 | **P0 — Cao** | `Note` | `Lưu ý` | `Ghi chú` | `vi.po:5362`–`5363` |
| 3 | P0 — Trung | `Assigned To` | `Được giao Cho` | `Người phụ trách` | `vi.po:751`–`752` |
| 4 | P0 — Trung | `Deal` | `Deal` (giữ tiếng Anh) | `Cơ hội`/`Thương vụ` (theo ngữ cảnh) | `vi.po:2117`–`2118` |
| 5 | P0 — Trung | `Lead Owner` | `Chủ sở hữu khách hàng tiềm năng` | `Nhân viên phụ trách` | `vi.po:4420`–`4421` |
| 6 | P1 | 236 mục `msgstr ""` | chưa dịch | dịch theo glossary, ưu tiên luồng chính | đếm `msgstr ""` = 236 |

> Lỗi #1 đúng là ví dụ đã nêu trong master plan §10.3 (`Lead → Chì`). Ở đây xác nhận **có thật** tại dòng cụ thể.

**Lưu ý về ngữ cảnh:** `Lead` được dùng ở nhiều chỗ (nhãn menu, tiêu đề trang, field). Vì `vi.po` là từ điển phẳng theo `msgid`, sửa `Lead` ảnh hưởng mọi nơi dùng cùng `msgid`. Nếu cần phân biệt ngữ cảnh, dùng **translation context** (`message:context` — `translation.js:18`–`23`) ở UI mới; với UI gốc phải cân nhắc khi vá.

---

## 4. NƠI THÊM/SỬA BẢN DỊCH (không sửa core)

1. **Bản dịch mới của CSKH:** thêm vào app `crm_cskh` (Frappe app-level translations, ví dụ `translations/vi.csv`), dùng `__()`/`_()` trong code mới.
2. **Sửa lỗi bản dịch upstream (`Lead → Chì`...):** ưu tiên đóng góp qua Frappe/Crowdin (`crowdin.yml` có trong repo) hoặc quản lý patch dịch có kiểm soát; **không** rải hard-code trong Vue core. Nếu buộc patch cục bộ, phải có ADR + diff tối thiểu + test.
3. **Nhãn DocType mới** (`CSKH *`): khai báo `label` tiếng Anh + cung cấp bản dịch; hoặc dùng `translated_doctype`.

---

## 5. NGHIỆM THU VIỆT HÓA (P0-F)

- [ ] Bật tiếng Việt cho 1 account DEV + timezone `Asia/Ho_Chi_Minh`.
- [ ] Chụp Login / Leads / Lead Detail / Call Logs / Tasks / Data Import / Settings / Dashboard / lỗi validation.
- [ ] Đối chiếu bảng §3; xác nhận `Lead → Chì` không còn xuất hiện trên UI. `[CẦN TEST DEV]`
- [ ] Xác nhận luồng chính không còn chuỗi tiếng Anh lẫn (đặc biệt `Deal`).
- [ ] Xác nhận date/time hiển thị `Asia/Ho_Chi_Minh`.
- [ ] Page CSKH mới dùng `__()` và hiển thị tiếng Việt.

**T1A (2026-10-19):** checklist màn chi tiết (10 màn) + quy tắc khử PII khi chụp
screenshot đã chuyển sang [`t1a-g009-vi-checklist.md`](../../deploy/dev/evidence/t1a-g009-vi-checklist.md)
§2–§3 để khỏi trùng lặp. Bản G0-09 vẫn PARTIAL; cần runtime evidence mới.

**Phiên Codex 2026-10-19** không có Docker/Yarn nên không tạo được bằng chứng
runtime mới cho G0-09. Checklist ở `t1a-g009-vi-checklist.md` được viết để
Owner chạy ngay khi có máy DEV.

---

## 6. CHƯA THỂ XÁC MINH (THIẾU DEV)

- Chưa render UI để biết `msgid` nào thực sự xuất hiện ở màn nào (một số `msgid` có thể không dùng).
- Chưa xác nhận việc sửa `Lead`/`Note` trong `vi.po` có phá ngữ cảnh màn khác không.
- Chưa sinh được `crm_cskh/translations/vi.csv` và kiểm tra pipeline dịch của app mới (chưa cài app).
- Chưa xác minh `get_messages_for_boot()` gộp bản dịch của `crm_cskh` cùng `crm` đúng thứ tự ưu tiên.
