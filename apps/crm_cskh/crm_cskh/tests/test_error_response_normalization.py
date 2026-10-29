# Unit tests for crm_cskh.errors.normalize_error_response (File oracle fix).
#
# The hook must be fail-closed: it only rewrites human-readable message
# strings in JSON 403/404 responses that echo File/Lead identifiers, and it
# NEVER changes status codes or exc_type.

import json
import unittest

from crm_cskh.errors import (
	_GENERIC_DENIAL,
	_looks_like_permission_echo,
	normalize_error_response,
)


class _FakeResponse:
	def __init__(self, status_code, payload=None, mimetype="application/json", raw=None):
		self.status_code = status_code
		self.mimetype = mimetype
		if raw is not None:
			self.data = raw
		elif payload is None:
			self.data = b""
		else:
			self.data = json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _permission_payload():
	return {
		"exc_type": "PermissionError",
		"exception": "frappe.exceptions.PermissionError",
		"_error_message": "You need the 'read' permission on File 1e0c8a768c",
		"_server_messages": json.dumps(
			[
				json.dumps(
					{
						"message": "User staff.c@dev.local no permission: File - 1e0c8a768c, CRM Lead - CRM-LEAD-00001"
					}
				)
			]
		),
	}


class TestNormalizeErrorResponse(unittest.TestCase):
	def test_detects_permission_echo(self):
		self.assertTrue(_looks_like_permission_echo(_permission_payload()))

	def test_ignores_non_permission_payloads(self):
		self.assertFalse(_looks_like_permission_echo({"exc_type": "PermissionError", "message": "ok"}))
		self.assertFalse(
			_looks_like_permission_echo({"exc_type": "ValidationError", "_error_message": "File - x"})
		)
		self.assertFalse(_looks_like_permission_echo({"message": "no markers"}))

	def test_normalizes_403_permission_echo_fail_closed(self):
		payload = _permission_payload()
		resp = _FakeResponse(403, payload)
		out = normalize_error_response(resp)
		self.assertEqual(out.status_code, 403)
		body = out.data.decode("utf-8") if isinstance(out.data, (bytes, bytearray)) else out.data
		cleaned = json.loads(body)
		self.assertEqual(cleaned["exc_type"], "PermissionError")
		self.assertEqual(cleaned["_error_message"], _GENERIC_DENIAL)
		server_messages = json.loads(cleaned["_server_messages"])
		for item in server_messages:
			self.assertIn(_GENERIC_DENIAL, item)
		self.assertNotIn("1e0c8a768c", body)
		self.assertNotIn("CRM-LEAD-00001", body)

	def test_leaves_200_responses_untouched(self):
		raw = b'{"message": {"name": "1e0c8a768c"}}'
		resp = _FakeResponse(200, raw=raw)
		out = normalize_error_response(resp)
		self.assertEqual(out.data, raw)

	def test_leaves_plain_404_untouched(self):
		payload = {"exc_type": "DoesNotExistError", "message": "File deadbeef00 not found"}
		resp = _FakeResponse(404, payload)
		data_before = resp.data
		out = normalize_error_response(resp)
		self.assertEqual(out.data, data_before)


if __name__ == "__main__":
	unittest.main()
