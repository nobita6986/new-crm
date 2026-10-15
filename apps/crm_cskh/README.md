### CRM CSKH

App Frappe tùy chỉnh của dự án CRM-CSKH. **P0-B (WP-02) mới ở mức scaffold**:
chỉ có PoC metadata/DocType/API, **không** chứa schema nghiệp vụ P1–P3.

Nội dung hiện có:

- `crm_cskh/api/health.py` — `crm_cskh.api.health.ping` (yêu cầu đăng nhập, không trả thông tin nhạy cảm).
- `crm_cskh/crm_cskh/doctype/cskh_app_settings/` — DocType PoC `CSKH App Settings` (Single).
- `crm_cskh/fixtures/custom_field.json` — Custom Field PoC `cskh_poc_marker` trên `CRM Lead`.
- `crm_cskh/crm_cskh/tests/` — test theo runner của Frappe.

### Installation (DEV repo-hosted)

Trong `deploy/dev/`, app nằm ở `apps/crm_cskh` của repo và được đưa vào bench bằng symlink
(`bootstrap.sh` với `INSTALL_CSKH=1`):

```bash
cd $PATH_TO_YOUR_BENCH
ln -s /src/apps/crm_cskh apps/crm_cskh     # repo là nguồn duy nhất, /src mount read-only
bench --site dev.localhost install-app crm_cskh
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
