# Copyright (c) 2026, CRM-CSKH Project
# License: agpl-3.0
#
# P0-B §B4 — health API tối thiểu.
#
# Yêu cầu đăng nhập: @frappe.whitelist() mặc định allow_guest=False, nên Frappe
# từ chối Guest ở tầng `frappe.is_whitelisted()` TRƯỚC khi hàm này chạy.
#
# KHÔNG trả thông tin nhạy cảm: không DB credentials, không site config,
# không đường dẫn hệ thống, không phiên bản framework/site.

import frappe

from crm_cskh import __version__


@frappe.whitelist()
def ping():
	"""Trả trạng thái an toàn của app. Chỉ gọi được khi đã đăng nhập.

	Trả về:
		dict: ``app``, ``status``, ``app_version`` và ``user`` (chính người gọi).
	"""
	return {
		"app": "crm_cskh",
		"status": "ok",
		"app_version": __version__,
		"user": frappe.session.user,
	}
