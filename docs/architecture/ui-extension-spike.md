# UI EXTENSION SPIKE — FRAPPE CRM `v1.87.0` (P0)

> **Dự án:** CRM-CSKH
> **Chủ sở hữu tài liệu:** T1B
> **Trạng thái:** Khảo sát **tĩnh**; **chưa PoC trên DEV**. **ADR-003 (T0)** đã cho phép PoC UI-A+UI-B trên cùng site/session, **không fork**; **ADR-UI-001 chốt sau PoC**. **ADR-001 (T0):** metadata pin cho lần chạy P0 là **CRM `v1.87.0`** + **Frappe Framework `v15.123.1`** + **frappe_docker `v4.0.1`**; **runtime compatibility CHƯA xác minh**, ADR-001 **chưa chốt cuối** cho đến khi baseline chạy thật.
> **Tài liệu liên quan:** [`reuse-audit.md`](./reuse-audit.md), [`data-model-spike.md`](./data-model-spike.md), [`P0.md`](../root-plan/P0.md) §5, [`P1.md`](../root-plan/P1.md) §3.2, [`P2.md`](../root-plan/P2.md) §12

---

## 0. PROVENANCE

- Nguồn: `https://github.com/frappe/crm`
- Tag: `v1.87.0` — Commit: **`0d3910b456ef4fb913e77b29db011b3debc4f778`**
- **Giới hạn:** chưa có DEV để chạy `yarn dev`/`bench start`; toàn bộ kết luận dưới đây là đọc mã tĩnh. Mọi hành vi runtime (deep-link refresh, session, CSRF, menu render) đánh dấu `[CẦN TEST DEV]`.

---

## 1. CƠ CHẾ HIỆN CÓ CỦA SPA CRM

### 1.1. Routing — **hard-code, không có điểm chèn**

`frontend/src/router.js`:

- Mảng `routes` được khai báo **tĩnh** `:24`–`142` (`Home`, `Leads`, `Lead`, `Deals`, `Notes`, `Tasks`, `Contacts`, `Organizations`, `Call Logs`, `DataImport*`, `Welcome`, `Onboarding`, `Invalid Page`, `Not Permitted`).
- Base URL cố định: `:148`–`151` `createRouter({ history: createWebHistory('/crm'), routes })`.
- Component nạp lazy theo `import()`; `Lead`/`Deal` đổi theo mobile (`handleMobileView` `:144`).
- `beforeEach` `:153`–`319` kiểm `isCrmUser()` và điều hướng (`Not Permitted`).

→ **Kết luận:** `v1.87.0` **không** đọc route từ app ngoài, **không** có registry route/plugin. Muốn thêm route trong chính SPA `/crm` phải **sửa `router.js`** (fork).

### 1.2. Menu/sidebar — **hard-code, chỉ hiển thị route đã tồn tại**

- `frontend/src/utils/navigation.js:11`–`25`: mảng `navigationItems` tĩnh (Dashboard, Leads, Deals, Contacts, Organizations, Notes, Tasks, Call Logs).
- `getNavigationItems` `:27`–`31` lọc theo `router.hasRoute(item.route)` → chỉ hiện route **đã đăng ký**; không có cơ chế "đăng ký mục menu từ app khác".
- `frontend/src/components/Layouts/AppSidebar.vue:188` import `getNavigationItems`; `:254` `links = getNavigationItems(...)`; `:256`–`286` dựng `allViews` từ `links` + Public/Pinned Views.
- Khung layout: `frontend/src/App.vue:29`–`41` chọn `DesktopLayout`/`MobileLayout`; `DesktopLayout.vue` gồm `AppSidebar` + `AppHeader`.

→ **Kết luận:** thêm trang CSKH vào **menu của SPA gốc** đòi hỏi sửa `navigation.js` (+ `router.js`) → fork.

### 1.3. Điểm nối phía server (không cần sửa SPA)

| Cơ chế | Evidence | Ý nghĩa |
|---|---|---|
| Route rule `/crm/<path>` → `crm` | `crm/hooks.py:71`–`74` `website_route_rules` | SPA CRM phục vụ dưới `/crm`; route lạ rơi vào SPA |
| App screen entry | `crm/hooks.py:15`–`23` `add_to_apps_screen` (`/crm`, `has_permission: crm.api.check_app_permission`) | CRM xuất hiện như 1 app; **app mới có thể thêm entry riêng của mình** |
| Kiểm quyền vào app | `crm/api/__init__.py:55`–`76` `check_app_permission` (role System Manager/Sales User/Sales Manager + module FCRM) | Mẫu để app `crm_cskh` tự kiểm quyền cho trang riêng |

→ **Đây là cửa mở chính:** một **page Frappe độc lập** (ví dụ `/cskh`) do `crm_cskh` cung cấp, dùng **chung session/Frappe site**, **không cần** chèn vào SPA CRM. Frappe cho phép app khai báo page/route riêng (`www/` hoặc `website_route_rules`) mà không đụng `crm`.

### 1.4. Dashboard — **không có hook ở tag này**

- `crm/api/dashboard.py:53`–`61`: dashboard gốc lặp `layout` và gọi động `get_<l['name']>` trên `crm.api.dashboard`.
- `get_chart` `:64`–`88` tương tự; `@sales_user_only` `:25`–`27`.
- Layout lưu ở DocType `CRM Dashboard`, name cố định `Manager Dashboard` (`crm_dashboard.py:19`–`33`).
- **Không có** hook `crm_dashboard_charts`: `grep -r "crm_dashboard_charts\|dashboard_chart\|render_dashboard"` trên repo tag → **0 match**.

→ Xác nhận master plan §3.2.7: Dashboard CSKH **không** dựa được vào hook chưa phát hành; phải là **endpoint + page riêng** của `crm_cskh`.

### 1.5. i18n trong SPA (để trang mới dùng chung)

- Boot nhúng từ điển: `crm/www/crm.py:84` `"translated_messages": get_messages_for_boot()`; frontend `App.vue:45` `setConfig('translatedMessages', window.translated_messages)`.
- `frontend/src/translation.js:14`–`35` `translate(message, replace, context)`, key có context `message:context`; đăng ký global `__` (`:3`–`6`).
- Backend API dịch theo user: `crm/api/__init__.py:12`–`19`.

→ Page CSKH mới nên dùng cùng pipeline (Frappe i18n + `__()`), không dựng cơ chế dịch riêng.

---

## 2. BA PHƯƠNG ÁN (UI-A / UI-B / UI-C)

| | **UI-A — Page riêng cùng site** | **UI-B — Dùng CRM UI gốc + nút dẫn sang page mới** | **UI-C — Fork/patch SPA CRM** |
|---|---|---|---|
| Cách làm | `crm_cskh` cung cấp page `/cskh` (+ route rule), dùng chung session, gọi API `crm_cskh.api.*` | Tận dụng Lead/Task/List/CallLog gốc; thêm nút/menu trỏ sang page `/cskh`; có thể dùng custom action | Sửa `router.js` + `navigation.js` (+ `Activities.vue` nếu cần) để nhúng route/menu/tab vào SPA |
| Sửa core | **Không** | **Không** (hoặc rất nhỏ nếu cần custom action) | **Có** (fork upstream) |
| Đăng nhập | Cùng session (1 login) | Cùng session | Cùng session |
| Điều hướng | Rời SPA sang trang mới (một số điều hướng chéo) | Trong SPA + nhảy sang trang mới khi cần | Liền mạch trong SPA |
| Chi phí dựng | Trung bình | Thấp nhất | Cao |
| **Chi phí duy trì / upgrade** | **Thấp** (không đụng upstream; chỉ dựa API ổn định) | **Thấp** | **Cao** (mỗi lần CRM nâng version phải rebase patch; rủi ro hồi quy) |
| Rủi ro | Cần PoC session/CSRF/deep-link `[CẦN TEST DEV]` | UX phân mảnh; menu nút phải đặt ở UI gốc | Patchset phình, khó audit, dễ lệch baseline |
| Phù hợp | Dashboard CSKH, kho khách, phân phối, My Customers | Quick access từ Lead/Deal sang CSKH | Chỉ khi A/B bất khả thi |

### 2.1. Quyết định (T0 / ADR-003) và hướng PoC

**Quyết định thiết kế (T0):** cho phép **PoC UI-A + UI-B trên cùng site/session**; **không fork UI** ở bước này. Đây là **quyết định thiết kế để kiểm chứng** — **chưa** phải kết luận runtime; **kết luận cuối phụ thuộc kết quả PoC** (WP-04). Hướng PoC cụ thể (đúng mặc định `P0.md §5.2`):

1. **UI-A** cho màn hình nghiệp vụ CSKH tách riêng: `/cskh`, `/cskh/my-customers`, `/cskh/distribution`, `/cskh/reports`.
2. **UI-B** cho lối vào: từ Lead/Deal/Task gốc có nút mở hộp CSKH; dùng custom action/form script nếu đủ (Frappe có `CRM Form Script`, `CRM Global Settings` — xem `install.py:545` và `crm_form_script`).
3. **Tránh UI-C.** Chỉ cân nhắc khi A/B chứng minh không đáp ứng; khi đó giữ diff **tối thiểu**, pin SHA, có regression.

**Lý do (dựa evidence, không phải sở thích):**
- `router.js:24`–`142` và `navigation.js:11`–`25` là **hard-code**, không có extension point → bất kỳ cách nhúng vào SPA đều = fork.
- `hooks.py:71`–`74` + `add_to_apps_screen` cho phép app riêng có route/entry riêng mà **không** fork.
- `crm/api/__init__.py:55`–`76` cho mẫu kiểm quyền truy cập app → áp dụng cho `/cskh`.
- Không có hook dashboard (`grep` = 0) → dashboard phải là page riêng bất kể chọn phương án nào.

---

## 3. HỆ QUẢ CỤ THỂ THEO MÀN HÌNH

| Màn hình (dự kiến) | Phương án | Ghi chú |
|---|---|---|
| Kho khách hàng | UI-A (`/cskh/warehouse`) | Hoặc tái dùng `Leads.vue` cho bản ghi (REUSE) |
| Nhập danh sách | Reuse component `DataImport` (`frontend/src/pages/DataImport.vue:11`) + wrapper | Có thể mount trong page CSKH |
| Phân phối / campaign | UI-A | Hoàn toàn mới (`crm_cskh`) |
| My Customers | UI-A | Danh sách lọc theo current user, kiểm quyền backend |
| Ghi nhận kết quả CSKH | UI-A (form riêng) | Không sửa `Activities.vue` |
| Timeline CSKH | **Tab riêng** trong page CSKH | Tránh sửa `Activities.vue` (`activity_type` hard-code, `:96`–`234`) |
| Dashboard chất lượng | UI-A + endpoint `crm_cskh.api.reporting.*` | Reuse chart component của Frappe UI |
| Lối vào từ Lead/Deal | UI-B | Custom action/link |

---

## 4. KẾ HOẠCH PoC (khi có DEV — WP-04)

1. Tạo page `/cskh` trong `crm_cskh` (route rule riêng), xác nhận **1 domain, 1 session** (không login lần hai).
2. Từ Lead List (`/crm/leads`) mở `/cskh`, xác nhận session còn hiệu lực; quay lại `/crm` không cần đăng nhập lại. `[CẦN TEST DEV]`
3. Kiểm tra Staff chỉ thấy dữ liệu được giao qua API của trang mới (không rò rỉ). `[CẦN TEST DEV]`
4. Xác nhận route `/crm/*` không bị page mới chiếm; **refresh deep-link** hoạt động. `[CẦN TEST DEV]`
5. Kiểm tra cookie/session + **CSRF** cho POST (dùng `csrf_token` từ boot `crm/www/crm.py:77`). `[CẦN TEST DEV]`
6. Đối chiếu công sức UI-A + UI-B với UI-C và chi phí nâng cấp upstream.
7. Ghi **ADR-UI-001** với ảnh chụp + đường dẫn demo.

---

## 5. ĐIỀU CHƯA THỂ XÁC MINH (THIẾU DEV)

- Deep-link refresh / history mode của page mới (`createWebHistory('/crm')` `router.js:149`).
- Session chia sẻ giữa SPA `/crm` và page `/cskh` (cookie path, login redirect `router.js:227`).
- CSRF cho POST từ page mới.
- `add_to_apps_screen` của `crm_cskh` render thế nào (chưa cài app).
- Nội bộ component `frappe-ui` (`Sidebar`, `DataImport`) — bundle ngoài repo CRM.
- Tương thích runtime **chưa xác minh** với pin lần chạy P0: **CRM `v1.87.0` + Frappe Framework `v15.123.1` + frappe_docker `v4.0.1`** (ADR-001, xem [`version-lock.md`](./version-lock.md)); **ADR-001 chưa chốt cuối** cho đến khi baseline chạy thật; v16 để khảo sát sau.

---

## 6. KẾT LUẬN

- Ở `v1.87.0`, SPA CRM (`router.js`, `navigation.js`) **không có extension point**; mọi nhúng vào SPA = fork.
- Khả thi và **ưu tiên**: **page riêng cùng Frappe site** (UI-A) + **lối vào từ UI gốc** (UI-B). Không fork.
- Dashboard CSKH bắt buộc là **page/endpoint riêng** vì không có hook `crm_dashboard_charts` ở tag này (đã xác nhận bằng grep = 0).
- Cần T0 xác nhận hướng UI-A/B và cho phép chạy PoC khi DEV sẵn sàng.
