### CRM CSKH

App Frappe tùy chỉnh của dự án CRM-CSKH. P0 đã nghiệm thu nền tảng DEV và các guard bảo mật; app chưa chứa đầy đủ schema nghiệp vụ P1–P3. Xem [P0 exit report](../../docs/architecture/p0-exit-report.md).

Nội dung hiện có:

- `crm_cskh/api/health.py` — `crm_cskh.api.health.ping` (yêu cầu đăng nhập, không trả thông tin nhạy cảm).
- `crm_cskh/crm_cskh/doctype/cskh_app_settings/` — DocType PoC `CSKH App Settings` (Single).
- `crm_cskh/fixtures/custom_field.json` — Custom Field PoC `cskh_poc_marker` trên `CRM Lead`.
- `crm_cskh/crm_cskh/tests/` — test theo runner của Frappe.
- `crm_cskh/permissions.py` và `api/guarded_crm.py` — scope bản ghi, assignment và lọc/redact dữ liệu CRM.
- `crm_cskh/errors.py` — chuẩn hóa lỗi hai route File để không phản chiếu identifier hoặc phân biệt được file ngoài scope với ID không tồn tại qua status/body.
- `crm_cskh/api/guarded_reporting.py` — giữ reporting/export fail-closed cho tới service P1.

### Installation (DEV repo-hosted)

Trong `deploy/dev/`, app nằm ở `apps/crm_cskh` của repo và được đưa vào bench bằng symlink. Dùng [bootstrap chính thức](../../deploy/dev/README.md) với `INSTALL_CSKH=1` để đăng ký app, cài editable package, install-app và migrate đầy đủ. Không chỉ tạo symlink rồi bỏ qua bước cài package:

```bash
cd deploy/dev
docker compose exec bench bash /src/deploy/dev/bootstrap.sh
```

Với repo riêng (chưa dùng ở P0): `bench get-app <URL> && bench install-app crm_cskh`.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/crm_cskh
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

agpl-3.0
