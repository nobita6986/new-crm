# Lịch Làm Việc, Ngày Nghỉ Lễ & Quy Tắc Thời Gian (`docs/p3/calendars-and-holidays.md`)

---

## 1. Kết quả kiểm tra tương thích Upstream & Kiến trúc Adapter

Trong Frappe CRM `v1.87.0` (`0d3910b4`), kiểm tra trực tiếp mã nguồn đã ghim cho thấy:
- **`CRM Holiday List` & `CRM Holiday` (`crm_holiday_list.py:12-88`):** Biểu diễn chuẩn danh sách ngày nghỉ lễ và ngày nghỉ hàng tuần (`from_date`, `to_date`, `holidays[].date`, `holidays[].weekly_off`). P3 **tái sử dụng trực tiếp** `CRM Holiday List` làm nguồn danh mục ngày nghỉ lễ, đồng thời bổ sung guard quyền để chỉ `CSKH CRM Admin` / `System Manager` được phép thêm/sửa/xóa (khắc phục việc upstream mặc định mở quyền ghi cho `Sales User`).
- **`CRM Service Level Agreement` & `CRM Service Day` (`crm_service_level_agreement.py:224-359`):** Không đáp ứng được yêu cầu P3 vì: (1) chỉ cho 1 khung giờ mỗi thứ trong tuần (`res[row.workday] = row`), không biểu diễn được ca sáng + nghỉ trưa + ca chiều hay ca qua đêm; (2) `calc_elapsed_time` lặp `timedelta(seconds=1)` (`O(seconds)`); (3) `calc_time` dòng `241` nhảy ngày nghỉ bằng `add_to_date(res, days=1)` làm lệch giờ bắt đầu ca sáng hôm sau.
- **Giải pháp P3:** Xây dựng `CSKH Working Calendar` (liên kết `holiday_list -> CRM Holiday List` và bảng con `shifts` hỗ trợ nhiều ca/ngày + ca qua đêm) cùng 2 hàm thuần không phụ thuộc DB trong `crm_cskh/services/calendar.py`.

---

## 2. Đặc tả Toán học của 2 Hàm Thuần (`services/calendar.py`)

### 2.1 Cấu trúc dữ liệu `WorkingCalendarSpec` (Immutable dataclass, JSON-serializable)
- `calendar_name: str`
- `timezone: str` (mặc định `"Asia/Ho_Chi_Minh"`)
- `shifts_by_weekday: dict[int, tuple[tuple[time, time], ...]]` (`0 = Monday .. 6 = Sunday`)
  - Cho phép 0, 1 hoặc nhiều khung giờ trong cùng một ngày (ví dụ Thứ 2–Thứ 6: `(08:00–12:00, 13:00–17:30)`, Thứ 7: `(08:00–12:00)`).
  - **Hỗ trợ ca qua đêm (`Overnight Shift`):** Nếu cấu hình một ca có `end_time <= start_time` (ví dụ ca trực `22:00 -> 06:00` sáng hôm sau, và không phải `00:00 -> 00:00` full-day), bộ chuẩn hóa tách tường minh thành 2 đoạn bất biến theo ngày lịch địa phương: đoạn `[22:00, 24:00)` của ngày mở ca $d$ và đoạn `[00:00, 06:00)` gắn với ngày kế tiếp $d+1$ (trừ khi ngày mở ca hoặc phân đoạn bị quy định nghỉ lễ theo cờ `holiday_blocks_overnight_spillover`).
- `holidays: frozenset[date]` — tập ngày nghỉ đọc từ `CRM Holiday List`.

### 2.2 Chuẩn hóa Múi giờ & Bảo toàn Quy ước Lưu trữ P2
- **Lưu trữ MariaDB:** Giữ nguyên 100% quy ước của Frappe v15 và P1/P2 — cột `DATETIME(6)` lưu thời gian địa phương naive theo `Asia/Ho_Chi_Minh`.
- **Chuẩn hóa đầu vào hàm lịch:**
  - Nếu `dt` là `aware datetime` (ví dụ `2026-04-20T09:59:59Z` hoặc `2026-04-20T16:59:59+07:00`), chuyển đổi chính xác sang `ZoneInfo(calendar.timezone)` rồi lấy biểu diễn local tương ứng.
  - Nếu `dt` là `naive datetime`, hiểu trực tiếp trong `ZoneInfo(calendar.timezone)`.
  - Mọi phép so sánh và trả kết quả giữ độ chính xác đến microsecond/second nhất quán và trả về cùng kiểu quy ước (`naive Asia/Ho_Chi_Minh` cho tầng DB Frappe, hoặc `aware` khi gọi chế độ `preserve_tz=True`).

### 2.3 `business_minutes_between(start, end, calendar) -> float`
- Nếu `end <= start`: trả về `0.0` (không bao giờ trả số âm).
- Nếu `calendar` không có bất kỳ ca làm việc hợp lệ nào: raise `InvalidCalendarError`.
- Duyệt theo từng ngày lịch địa phương từ `start.date() - 1 day` (để bắt phần đuôi ca qua đêm nếu có) tới `end.date()`, lấy phần giao `[max(start, seg_start), min(end, seg_end)]` trên các phân đoạn làm việc không rơi vào `holidays`, cộng tổng số giây chính xác chia `60.0`.
- Độ phức tạp: $O(D \cdot S)$ với $D$ là số ngày giữa `start` và `end`, $S$ là số ca/ngày ($S \le 4$) — nhanh gấp hàng chục nghìn lần vòng lặp từng giây của upstream.

### 2.4 `add_business_minutes(start, target_minutes, calendar) -> datetime`
- Nếu `target_minutes < 0`: raise `ValueError`.
- Nếu `target_minutes == 0`:
  - Nếu `start` đang nằm **trong** một phân đoạn giờ làm việc hợp lệ (hoặc đúng biên mở ca): trả về chính `start`.
  - Nếu `start` nằm **ngoài** giờ làm việc (nghỉ trưa, sau giờ chiều, cuối tuần, ngày lễ): dời tới **đầu phân đoạn làm việc hợp lệ gần nhất tiếp theo**.
- Nếu `target_minutes > 0`:
  - Nếu `start` nằm ngoài giờ làm việc, dời con trỏ tới đầu phân đoạn làm việc hợp lệ kế tiếp rồi tiêu thụ dần `target_minutes` qua các phân đoạn làm việc liên tiếp (bỏ qua giờ nghỉ trưa, cuối tuần và mọi ngày trong `holidays`).
  - Bảo vệ chống vòng lặp vô hạn: nếu sau `max_search_days = 366` ngày liên tiếp không có bất kỳ phút làm việc nào (ví dụ lịch rỗng hoặc toàn bộ năm bị đánh dấu nghỉ lễ), raise `InvalidCalendarError` rõ ràng.

---

## 3. Bộ Golden Test Cases Bắt Buộc (`T-CAL-01..T-CAL-09`)

Với lịch chuẩn `VN_OFFICE_STD` (Thứ 2–Thứ 6: `08:00–12:00` & `13:00–17:30`; Thứ 7: `08:00–12:00`; Chủ nhật nghỉ; Lễ `2026-04-30`, `2026-05-01`):

1. **Trong cùng ca sáng:** `add_business_minutes("2026-04-20 10:00:00", 30)` -> `"2026-04-20 10:30:00"`.
2. **Vắt qua giờ nghỉ trưa (`12:00–13:00`):** `add_business_minutes("2026-04-20 11:45:00", 30)` -> `"2026-04-20 13:15:00"` (`15p` trước `12:00` + `15p` từ `13:00`).
3. **Tạo trong giờ nghỉ trưa (`12:20`):** `add_business_minutes("2026-04-20 12:20:00", 30)` -> `"2026-04-20 13:30:00"`.
4. **Vắt qua hết giờ chiều sang sáng hôm sau:** `add_business_minutes("2026-04-20 17:15:00", 30)` -> `"2026-04-21 08:15:00"`.
5. **Tạo tối Thứ 7 vắt qua Chủ nhật sang sáng Thứ 2:** `add_business_minutes("2026-04-25 16:00:00", 30)` -> `"2026-04-27 08:30:00"` (khắc phục lỗi giữ nguyên `16:00` của upstream).
6. **Vắt qua kỳ nghỉ lễ 30/04–01/05 + cuối tuần:** `add_business_minutes("2026-04-29 17:15:00", 30)` -> tiêu thụ `15p` ngày `29/04` (`17:15–17:30`), bỏ qua Thứ 5 `30/04` (Lễ) & Thứ 6 `01/05` (Lễ), cộng `15p` vào sáng Thứ 7 `02/05` -> `"2026-05-02 08:15:00"`.
7. **Ca qua đêm (`22:00 -> 02:00`):** `add_business_minutes("2026-04-20 23:30:00", 60, OVERNIGHT_CAL)` -> `"2026-04-21 00:30:00"`; `business_minutes_between("2026-04-20 23:30:00", "2026-04-21 00:30:00", OVERNIGHT_CAL)` -> `60.0`.
8. **Biên ngày / Timezone (`UTC` vs `+07:00`):** `"2026-04-20T09:59:00Z"` (`= 16:59:00+07:00`) + `31` phút làm việc -> `"2026-04-20 17:30:00"` (hoặc `+32` phút -> `"2026-04-21 08:01:00"`), khớp tuyệt đối giữa input `Z`, `+07:00` và naive local.
9. **Edge cases:** `target = 0`, `end < start` -> `0.0`, lịch rỗng -> raise `InvalidCalendarError`.
