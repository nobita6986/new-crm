# Screenshot evidence (P0 i18n / G0-09)

Thư mục này chứa screenshot các màn tiếng Việt theo checklist
[`../t1a-g009-vi-checklist.md`](../t1a-g009-vi-checklist.md) §3.2.

## Quy tắc bắt buộc

1. **Không** chụp màn có dữ liệu nhạy cảm:
   - Số điện thoại thật hoặc SĐT Việt Nam format thật.
   - Tên/email người thật.
   - API key, session cookie, CSRF token, password.
2. **Mask SĐT** trước khi chụp (ví dụ `0900-XXX-001`) nếu trang không ẩn cột.
3. **Dung lượng** ≤ 500 KB/ảnh, PNG, viewport 1280×800.
4. **Tên file**: `t1a-g009-NN-<screen>.png` với `NN` là số thứ tự trong checklist.

## Trạng thái hiện tại

- 2026-10-19: chưa có ảnh nào. Phiên Codex không có Docker/WSL2 để chạy site
  tiếng Việt và không có quyền lưu ảnh từ browser ra repo một cách an toàn.
- Owner cần chạy checklist trên máy có Docker + browser thật.
