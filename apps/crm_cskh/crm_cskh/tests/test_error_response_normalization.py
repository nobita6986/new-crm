# Unit tests for crm_cskh.errors.normalize_error_response.
#
# Two behaviors are pinned:
#  1. Echo scrub (all other routes): human-readable message strings that echo
#     File/Lead identifiers are replaced with a generic denial. Status codes
#     and exc_type are NEVER changed on this path.
#  2. Existence-oracle closure (direct File routes only): the real-id denial
#     (403 PermissionError) and the random-id miss (404 DoesNotExistError)
#     both leave as the SAME fixed generic 404 body. Access stays denied;
#     in-scope 200s are untouched.

import json
import unittest

from crm_cskh.errors import (
	_GENERIC_DENIAL,
	_canonical_not_found_body,
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


class _FakeRequest:
	def __init__(self, path="", args=None):
		self.path = path
		self.args = args or {}


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


def _not_found_payload(supplied_name="deadbeef00"):
	return {
		"exc_type": "DoesNotExistError",
		"_server_messages": json.dumps(
			[json.dumps({"message": f"File {supplied_name} not found", "title": "Message"})]
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

	def test_normalizes_403_permission_echo_fail_closed_off_file_routes(self):
		payload = _permission_payload()
		resp = _FakeResponse(403, payload)
		out = normalize_error_response(resp, _FakeRequest(path="/api/method/crm_cskh.api.health.ping"))
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

	def test_file_rest_oracle_cases_are_indistinguishable(self):
		real = _FakeResponse(403, _permission_payload())
		random = _FakeResponse(404, _not_found_payload("deadbeef00"))
		out_real = normalize_error_response(real, _FakeRequest(path="/api/resource/File/1e0c8a768c"))
		out_random = normalize_error_response(random, _FakeRequest(path="/api/resource/File/deadbeef00"))
		self.assertEqual(out_real.status_code, 404)
		self.assertEqual(out_random.status_code, 404)
		body_real = out_real.data.decode() if isinstance(out_real.data, (bytes, bytearray)) else out_real.data
		body_random = (
			out_random.data.decode() if isinstance(out_random.data, (bytes, bytearray)) else out_random.data
		)
		self.assertEqual(body_real, body_random)
		self.assertEqual(body_real, _canonical_not_found_body())
		self.assertNotIn("1e0c8a768c", body_real)
		self.assertNotIn("deadbeef00", body_random)
		self.assertNotIn("CRM-LEAD-00001", body_real)

	def test_file_rpc_oracle_cases_are_indistinguishable(self):
		real = _FakeResponse(403, _permission_payload())
		random = _FakeResponse(404, _not_found_payload("deadbeef00"))
		args = {"doctype": "File"}
		out_real = normalize_error_response(
			real, _FakeRequest(path="/api/method/frappe.client.get", args=args)
		)
		out_random = normalize_error_response(
			random, _FakeRequest(path="/api/method/frappe.client.get", args=args)
		)
		self.assertEqual(out_real.status_code, 404)
		self.assertEqual(out_random.status_code, 404)
		body_real = out_real.data.decode() if isinstance(out_real.data, (bytes, bytearray)) else out_real.data
		body_random = (
			out_random.data.decode() if isinstance(out_random.data, (bytes, bytearray)) else out_random.data
		)
		self.assertEqual(body_real, body_random)
		self.assertNotIn("1e0c8a768c", body_real)
		self.assertNotIn("deadbeef00", body_random)

	def test_non_file_rpc_denial_keeps_403(self):
		payload = _permission_payload()
		resp = _FakeResponse(403, payload)
		out = normalize_error_response(
			resp, _FakeRequest(path="/api/method/frappe.client.get", args={"doctype": "CRM Lead"})
		)
		self.assertEqual(out.status_code, 403)
		body = out.data.decode("utf-8") if isinstance(out.data, (bytes, bytearray)) else out.data
		self.assertEqual(json.loads(body)["exc_type"], "PermissionError")

	def test_leaves_200_responses_untouched(self):
		raw = b'{"message": {"name": "1e0c8a768c"}}'
		resp = _FakeResponse(200, raw=raw)
		out = normalize_error_response(resp, _FakeRequest(path="/api/resource/File/1e0c8a768c"))
		self.assertEqual(out.data, raw)

	def test_leaves_plain_404_untouched_off_file_routes(self):
		payload = {"exc_type": "DoesNotExistError", "message": "File deadbeef00 not found"}
		resp = _FakeResponse(404, payload)
		data_before = resp.data
		out = normalize_error_response(resp, _FakeRequest(path="/api/method/crm_cskh.api.health.ping"))
		self.assertEqual(out.data, data_before)


if __name__ == "__main__":
	unittest.main()
