# Copyright (c) 2026, CRM-CSKH Project
# License: agpl-3.0

from frappe.model.document import Document


class CSKHAppSettings(Document):
	"""Cấu hình cấp app cho crm_cskh (Single DocType — P0-B §B3).

	    P0 dùng DocType này để xác minh lifecycle create → migrate → permission →
	read/write. Đây KHÔNG phải schema nghiệp vụ P1-P3.
	"""

	pass
