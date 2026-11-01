"""HTTP request context cho test gọi `frappe.handler.execute_cmd()`.

Vì sao cần
----------
`frappe.handler.execute_cmd()` — entry point thật của `/api/method/...` — gọi
`is_valid_http_method()` ngay dòng đầu (frappe/handler.py:84 → :107), hàm này
đọc `frappe.local.request.method`:

    def is_valid_http_method(method):
        http_method = frappe.local.request.method

Test runner (`bench run-tests`) **không** có HTTP server thật, nên
`frappe.local.request` là `None` ⇒ `AttributeError: 'NoneType' object has no
attribute 'method'`. Đây là *thiếu HTTP context trong test harness*, KHÔNG phải
bug của app: các assertion fail-closed của wrapper vẫn đúng, chỉ là chưa tới
được tầng handler.

Bằng chứng CI: run #39807874178 và #39810335017 — 13 method ERROR với
`AttributeError: request` / `'NoneType' object has no attribute 'method'`.

Đáng chú ý: các test **Guest** vẫn PASS vì `is_whitelisted()` raise
`PermissionError` *trước* khi tới dòng 107. Vì vậy lỗi chỉ lộ ra ở các test
authenticated, và che mất đúng những assertion quan trọng nhất.

Cách dùng
---------
    with bound_request():
        frappe.handler.execute_cmd("crm_cskh.api.guarded_crm.get_call_log")

Helper chỉ bind một `werkzeug.Request` tối thiểu cho đúng code path đó và
trả lại `frappe.local.request` về giá trị cũ khi thoát `with`. Nó KHÔNG nới
lỏng assertion nào, không bỏ qua `is_valid_http_method`, và không đụng tới app
source.

File này cố ý **không** đặt tên `test_*` để `bench run-tests --module ...`
và JUnit gate của G0-07 không sinh thêm testcase ngoài inventory 99 method.
"""

from __future__ import annotations

import collections.abc
import contextlib
from typing import Any

import frappe

__all__ = ["bound_request", "build_request"]


def build_request(method: str = "GET", headers: dict[str, str] | None = None) -> Any:
	"""Trả về một `werkzeug.Request` tối thiểu, đúng shape mà Frappe cần.

	`EnvironBuilder` tạo WSGI environ hợp lệ (method, path, headers) nên
	`request.method` trả đúng giá trị — điều `is_valid_http_method()` kiểm tra.
	"""
	from werkzeug.test import EnvironBuilder
	from werkzeug.wrappers import Request

	builder = EnvironBuilder(method=method, headers=headers or {})
	return Request(builder.get_environ())


@contextlib.contextmanager
def bound_request(method: str = "GET", headers: dict[str, str] | None = None):
	"""Bind `frappe.local.request` trong suốt `with`, rồi khôi phục lại.

	Khôi phục (không set cứng `None`) để không phá test lồng nhau: nếu test
	gọi lồng nhau hoặc `setUp` đã bind request riêng, giá trị cũ được giữ nguyên.
	"""
	previous = getattr(frappe.local, "request", None)
	frappe.local.request = build_request(method, headers)
	try:
		yield frappe.local.request
	finally:
		frappe.local.request = previous
