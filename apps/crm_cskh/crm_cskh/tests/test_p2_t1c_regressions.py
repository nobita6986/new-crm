"""Regression suite for T1C P2 verification findings (`F-P2-01`, `F-P2-02`, `F-P2-04`, `F-P2-05`, `F-P2-06`).

Covers:
  1. `F-P2-01` (HIGH): Sensitive reason privacy across `ownership.history`, REST (`frappe.get_list`),
     `frappe.client.*`, `reportview.*`, and Desk `getdoc`, filter/search/sort oracle blocks,
     pre-fix data migration (`migrate_p2_assignment_history_privacy`), and `[H:<16hex>]` display
     separation without losing idempotency conflict detection.
  2. `F-P2-06` (HIGH): Server/document-lifecycle enforcement blocking generic API mutations of
     `CRM Lead` DNC fields (`cskh_do_not_contact`, `cskh_dnc_reason`, `cskh_dnc_at`,
     `cskh_dnc_work_item`) across REST v1/v2, `client.set_value`/`save`/`insert`/`bulk_update`,
     Desk `savedocs`, and ORM, while preserving normal Lead writes, audited CSKH DNC flows,
     and DNC persistence across re-import and campaign distribution.
  3. `F-P2-02` (MEDIUM): `run_history.runs` preserving historical run allocation counts across
     `assigned -> in_progress -> on_hold -> closed`, `reassign`, and `revoke`, distinguishing
     `assigned` (`historical_run_allocation`) from `active_owned` / `active_owned_total`, and
     enforcing `L-24` shared-campaign Leader scope.
  4. `F-P2-04A/B` (MEDIUM): SQL-level `care_state` filtering before `COUNT(*)` and `LIMIT/OFFSET`
     (off-first-page items), `closed` filtering distinguishing `owner_at_close` vs `closed_by`,
     and `L-23` / `SEC2-07` PII denial when Lead moves or for pre-close former owners.
  5. `F-P2-05` (MEDIUM): System-timezone half-open `[start_of_day, start_of_next_day)` full-day
     semantics for date-only `date_to`, explicit datetime validation (`400 INVALID_REQUEST`),
     `DISTINCT` Work Item counters (`cohort_work_items`, `attempted_work_items`,
     `unattempted_work_items`, `connected_work_items`), effective `correct`/`void`/`restore`
     overlays (including patched `occurred_at`), cross-team isolation, and distinct
     `dnc_leads` vs `dnc_work_items`.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import re
from unittest import mock

import frappe
import frappe.client
from frappe.desk.form.load import getdoc
from frappe.desk.form.save import savedocs
from frappe.utils import add_days, add_to_date, getdate, now_datetime
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from crm_cskh.api import guarded_crm, guarded_reporting, run_history
from crm_cskh.api import ownership as ownership_api
from crm_cskh.api import settings as settings_api
from crm_cskh.api import workflow as workflow_api
from crm_cskh.jobs import import_worker
from crm_cskh.p1_permissions import enforce_p1_filter_field_permissions
from crm_cskh.p2_models import LEAD_DNC_FIELDS, enforce_lead_dnc_request_guard
from crm_cskh.patches.v2_0.p2_schema_and_seeds import (
	backfill_closed_work_item_owners,
	migrate_p2_assignment_history_privacy,
)
from crm_cskh.services import (
	contact_attempt,
	correction,
	distribution,
	eligibility,
	followup,
	import_service,
	ownership,
	policies,
)
from crm_cskh.services.errors import Forbidden, IdempotencyConflict, InvalidRequest
from crm_cskh.tests._http_context import bound_request
from crm_cskh.tests.p2_base import P2TestCase

PHONE_RE = re.compile(r"(?:\+?84|0)\d{8,10}")
HASH_TAG_RE = re.compile(r"\[H:[0-9a-fA-F]{16,64}\]")


@contextlib.contextmanager
def _simulated_json_request(
	method: str, path: str, payload: dict | None = None, form_args: dict | None = None
):
	prev_req = getattr(frappe.local, "request", None)
	prev_fd = dict(getattr(frappe.local, "form_dict", None) or {})
	raw_data = json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None
	builder = EnvironBuilder(
		method=method,
		path=path,
		data=raw_data,
		content_type="application/json" if payload is not None else None,
	)
	frappe.local.request = Request(builder.get_environ())
	new_fd = frappe._dict()
	if isinstance(payload, dict):
		new_fd.update(payload)
	if isinstance(form_args, dict):
		new_fd.update(form_args)
	frappe.local.form_dict = new_fd
	try:
		yield
	finally:
		frappe.local.request = prev_req
		frappe.local.form_dict = frappe._dict(prev_fd)


class TestP2T1cRegressions(P2TestCase):
	"""End-to-end backend regressions for T1C findings `F-P2-01`, `F-P2-02`, `F-P2-04`, `F-P2-05`, `F-P2-06`."""

	def setUp(self):
		super().setUp()
		self.camp_a = self.make_campaign(team_scope=self.team_a, title="Chiến dịch T1C Team A")
		self.camp_b = self.make_campaign(team_scope=self.team_b, title="Chiến dịch T1C Team B")
		self.camp_shared = self.make_campaign(team_scope=None, title="Chiến dịch T1C Chung")
		self.make_rule(self.camp_a, self.staff_a, weight=50)
		self.make_rule(self.camp_a, self.staff_b, weight=50)
		self.make_rule(self.camp_b, self.staff_c, weight=100)
		self.make_rule(self.camp_shared, self.staff_a, weight=50, team=self.team_a)
		self.make_rule(self.camp_shared, self.staff_c, weight=50, team=self.team_b)

	# ------------------------------------------------------------------
	# 1. `F-P2-01` — HIGH: Assignment History privacy, filter oracles,
	#    pre-fix data migration & `[H:<16hex>]` display separation
	# ------------------------------------------------------------------

	def test_f_p2_01_dnc_autoclose_close_reopen_privacy_five_planes_oracles_and_legacy_migration(self):
		"""`F-P2-01`: Sensitive notes/reasons stay out of public `reason`, blocked on 5 planes + filter oracles, `[H:<16hex>]` hidden, legacy rows migrated."""
		# 1A. DNC auto-close WITHOUT `completion_reason` -> customer `note` must NEVER leak into `reason` or `cskh_dnc_reason`
		wi_dnc, lead_dnc = self.make_assigned_work_item(self.camp_a, self.staff_a)
		secret_dnc_note = "Khách Nguyễn Văn Mật 0987654321 bức xúc yêu cầu ngừng gọi vĩnh viễn"
		idem_dnc = self.unique_key("f01-dnc")
		contact_attempt.record_attempt(
			work_item=wi_dnc,
			channel="phone_call",
			outcome_code="do_not_contact",
			note=secret_dnc_note,
			idempotency_key=idem_dnc,
			actor=self.staff_a,
		)

		dnc_lead_row = frappe.db.get_value(
			"CRM Lead",
			lead_dnc,
			["cskh_do_not_contact", "cskh_dnc_reason"],
			as_dict=True,
		)
		self.assertEqual(int(dnc_lead_row.cskh_do_not_contact or 0), 1)
		self.assertNotIn("Nguyễn Văn Mật", str(dnc_lead_row.cskh_dnc_reason or ""))
		self.assertNotIn("0987654321", str(dnc_lead_row.cskh_dnc_reason or ""))

		dnc_wi_reason = str(frappe.db.get_value("CSKH Work Item", wi_dnc, "completion_reason") or "")
		self.assertNotIn("Nguyễn Văn Mật", dnc_wi_reason)
		self.assertNotIn("0987654321", dnc_wi_reason)

		dnc_hist = frappe.db.get_value(
			"CSKH Assignment History",
			{"work_item": wi_dnc, "action": "close"},
			["name", "reason", "private_reason", "payload_hash"],
			as_dict=True,
		)
		self.assertIsNotNone(dnc_hist)
		self.assertNotIn("Nguyễn Văn Mật", str(dnc_hist.reason or ""))
		self.assertNotIn("0987654321", str(dnc_hist.reason or ""))
		self.assertNotIn("Nguyễn Văn Mật", str(dnc_hist.private_reason or ""))
		self.assertIsNone(HASH_TAG_RE.search(str(dnc_hist.reason or "")))
		self.assertIsNone(HASH_TAG_RE.search(str(dnc_hist.private_reason or "")))

		# 1B. Manual `close_work_item` and `reopen_work_item` with sensitive operator reasons + idempotency
		wi_cr, _lead_cr = self.make_assigned_work_item(self.camp_a, self.staff_a)
		contact_attempt.record_attempt(
			work_item=wi_cr,
			channel="phone_call",
			outcome_code="no_answer",
			note="Chuông đổ lần 1",
			idempotency_key=self.unique_key("f01-att"),
			actor=self.staff_a,
		)

		secret_close_reason = "Lý do đóng riêng tư: khách Trần Thị Kín 0912345678 khiếu nại phí dịch vụ"
		idem_close = self.unique_key("f01-close")
		close_res = contact_attempt.close_work_item(
			work_item=wi_cr,
			completion_reason=secret_close_reason,
			idempotency_key=idem_close,
			actor=self.staff_a,
		)
		self.assertFalse(close_res["idempotent_replay"])
		close_replay = contact_attempt.close_work_item(
			work_item=wi_cr,
			completion_reason=secret_close_reason,
			idempotency_key=idem_close,
			actor=self.staff_a,
		)
		self.assertTrue(close_replay["idempotent_replay"])
		with self.assertRaises(IdempotencyConflict):
			contact_attempt.close_work_item(
				work_item=wi_cr,
				completion_reason="Lý do bị sửa đổi trái phép",
				idempotency_key=idem_close,
				actor=self.staff_a,
			)

		secret_reopen_reason = "Lý do mở lại riêng tư: liên hệ số 0912345678 xử lý hoàn phí"
		idem_reopen = self.unique_key("f01-reopen")
		reopen_res = contact_attempt.reopen_work_item(
			work_item=wi_cr,
			target_user=self.staff_a,
			reason=secret_reopen_reason,
			idempotency_key=idem_reopen,
			actor=self.leader_a,
		)
		self.assertFalse(reopen_res["idempotent_replay"])
		reopen_replay = contact_attempt.reopen_work_item(
			work_item=wi_cr,
			target_user=self.staff_a,
			reason=secret_reopen_reason,
			idempotency_key=idem_reopen,
			actor=self.leader_a,
		)
		self.assertTrue(reopen_replay["idempotent_replay"])
		with self.assertRaises(IdempotencyConflict):
			contact_attempt.reopen_work_item(
				work_item=wi_cr,
				target_user=self.staff_a,
				reason="Sửa lý do mở lại cùng idempotency_key",
				idempotency_key=idem_reopen,
				actor=self.leader_a,
			)

		hist_close_name = frappe.db.get_value(
			"CSKH Assignment History", {"work_item": wi_cr, "action": "close"}, "name"
		)
		hist_reopen_name = frappe.db.get_value(
			"CSKH Assignment History", {"work_item": wi_cr, "action": "reopen"}, "name"
		)
		self.assertTrue(hist_close_name)
		self.assertTrue(hist_reopen_name)

		# 1C. Verify `BOD` (`0` System Manager) across all 5 planes + filter/search/sort/aggregate oracles
		self.assertNotIn("System Manager", frappe.get_roles(self.bod_user))
		forbidden_markers = (
			"Nguyễn Văn Mật",
			"0987654321",
			"Trần Thị Kín",
			"0912345678",
			"khiếu nại phí",
			"xử lý hoàn phí",
		)

		frappe.set_user(self.bod_user)
		# Plane 1: `ownership.history`
		for target_wi in (wi_dnc, wi_cr):
			bod_hist = ownership_api.history(work_item=target_wi)
			serialized = json.dumps(bod_hist, ensure_ascii=False, default=str)
			for marker in forbidden_markers:
				self.assertNotIn(marker, serialized)
			self.assertIsNone(PHONE_RE.search(serialized))
			self.assertIsNone(HASH_TAG_RE.search(serialized))
			for ev in bod_hist["items"]:
				self.assertNotIn("private_reason", ev)
				self.assertNotIn("payload_hash", ev)
				self.assertTrue(bool(ev["reason"]))

		# Plane 2: REST / `frappe.get_list` with explicit restricted `fields`
		bod_list = frappe.get_list(
			"CSKH Assignment History",
			filters={"work_item": ["in", [wi_dnc, wi_cr]]},
			fields=["name", "action", "reason", "private_reason", "payload_hash"],
		)
		self.assertGreaterEqual(len(bod_list), 3)
		bod_list_json = json.dumps(bod_list, ensure_ascii=False, default=str)
		for marker in forbidden_markers:
			self.assertNotIn(marker, bod_list_json)
		for row in bod_list:
			self.assertNotIn("private_reason", row)
			self.assertNotIn("payload_hash", row)

		# Plane 3: `frappe.client.get` & `frappe.client.get_list`
		for h_name in (dnc_hist.name, hist_close_name, hist_reopen_name):
			doc_dict = frappe.client.get("CSKH Assignment History", h_name)
			self.assertIsNone(doc_dict.get("private_reason"))
			self.assertIsNone(doc_dict.get("payload_hash"))
			doc_json = json.dumps(doc_dict, ensure_ascii=False, default=str)
			for marker in forbidden_markers:
				self.assertNotIn(marker, doc_json)
		client_rows = frappe.client.get_list(
			"CSKH Assignment History",
			filters={"work_item": wi_cr},
			fields=["name", "reason", "private_reason"],
		)
		for row in client_rows:
			self.assertNotIn("private_reason", row)
			for marker in forbidden_markers:
				self.assertNotIn(marker, json.dumps(row, ensure_ascii=False, default=str))

		# Plane 4: `reportview` (`guarded_reporting.reportview_get_list`)
		with _simulated_json_request(
			"POST",
			"/api/method/frappe.desk.reportview.get_list",
			{
				"doctype": "CSKH Assignment History",
				"fields": ["name", "action", "reason"],
				"filters": [["CSKH Assignment History", "work_item", "=", wi_cr]],
			},
		):
			rv_rows = guarded_reporting.reportview_get_list()
		rv_json = json.dumps(rv_rows, ensure_ascii=False, default=str)
		for marker in forbidden_markers:
			self.assertNotIn(marker, rv_json)

		# Plane 5: Desk `getdoc`
		frappe.response.pop("docs", None)
		getdoc("CSKH Assignment History", hist_close_name)
		loaded_docs = frappe.response.get("docs") or []
		self.assertEqual(len(loaded_docs), 1)
		self.assertIsNone(loaded_docs[0].get("private_reason"))
		self.assertIsNone(loaded_docs[0].get("payload_hash"))
		getdoc_json = json.dumps(loaded_docs, ensure_ascii=False, default=str)
		for marker in forbidden_markers:
			self.assertNotIn(marker, getdoc_json)

		# Oracle blocks for `BOD` across `filters`, `or_filters`, `searchfield`, `order_by`, `fields` aggregate, `get_value`, `get_count`
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list(
				"CSKH Assignment History",
				filters=[["CSKH Assignment History", "private_reason", "like", "%khiếu nại%"]],
			)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list(
				"CSKH Assignment History",
				or_filters=[["CSKH Assignment History", "private_reason", "like", "%0912%"]],
			)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list(
				"CSKH Assignment History",
				filters=[["CSKH Assignment History", "payload_hash", "like", "%"]],
			)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("CSKH Assignment History", order_by="private_reason desc")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list(
				"CSKH Assignment History",
				fields=["count(`tabCSKH Assignment History`.`private_reason`) as cnt"],
			)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list(
				"CSKH Work Item",
				filters=[["CSKH Work Item", "completion_reason", "like", "%khiếu nại%"]],
			)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list(
				"CSKH Work Item",
				filters=[["CSKH Work Item", "reopen_reason", "like", "%hoàn phí%"]],
			)
		self.assertEqual(
			guarded_crm.client_get_value(
				"CSKH Assignment History",
				fieldname="private_reason",
				filters={"name": hist_close_name},
			),
			{},
		)
		mixed_gv = guarded_crm.client_get_value(
			"CSKH Assignment History",
			fieldname=["name", "reason", "private_reason", "payload_hash"],
			filters={"name": hist_close_name},
		)
		self.assertEqual(mixed_gv.get("name"), hist_close_name)
		self.assertNotIn("private_reason", mixed_gv)
		self.assertNotIn("payload_hash", mixed_gv)
		self.assertNotIn("Hoàng Thị Khiếu Nại", json.dumps(mixed_gv, ensure_ascii=False, default=str))
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.client_get_value(
				"CSKH Assignment History",
				fieldname="count(private_reason)",
				filters={"name": hist_close_name},
			)
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.client_get_value(
				"CSKH Assignment History",
				fieldname="name",
				filters={"private_reason": ["like", "%khiếu nại%"]},
			)
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.client_get_count(
				"CSKH Assignment History",
				filters=[["CSKH Assignment History", "private_reason", "like", "%khiếu nại%"]],
			)
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.search_link(
				doctype="CSKH Assignment History",
				txt="khiếu nại",
				searchfield="private_reason",
			)
		with _simulated_json_request(
			"POST",
			"/api/method/frappe.desk.reportview.get",
			{
				"doctype": "CSKH Assignment History",
				"fields": ["name"],
				"filters": [["CSKH Assignment History", "private_reason", "like", "%khiếu nại%"]],
			},
		):
			with self.assertRaises(frappe.PermissionError):
				enforce_p1_filter_field_permissions()
			with self.assertRaises(frappe.PermissionError):
				guarded_reporting.reportview_get()

		# 1D. Positive controls: authorized `Staff A`, `Leader A`, `CRM Admin` read full history & milestones with `0` `[H:<16hex>]`
		for authorized_actor in (self.staff_a, self.leader_a, self.admin_user):
			frappe.set_user(authorized_actor)
			auth_hist = ownership_api.history(work_item=wi_cr)
			hist_reasons = [ev["reason"] for ev in auth_hist["items"]]
			self.assertIn(secret_close_reason, hist_reasons)
			self.assertIn(secret_reopen_reason, hist_reasons)
			self.assertIsNone(HASH_TAG_RE.search(json.dumps(auth_hist, ensure_ascii=False, default=str)))

			tl = workflow_api.list_attempts(work_item=wi_cr, include_milestones=True)
			tl_json = json.dumps(tl, ensure_ascii=False, default=str)
			self.assertIn(secret_close_reason, tl_json)
			self.assertIn(secret_reopen_reason, tl_json)
			self.assertIsNone(HASH_TAG_RE.search(tl_json))

		# 1E. Pre-fix legacy rows migration (`migrate_p2_assignment_history_privacy`) — idempotent & preserves idempotency hash
		frappe.set_user("Administrator")
		wi_leg, lead_leg = self.make_assigned_work_item(self.camp_a, self.staff_a)
		leg_note = "Khách Lê Cũ 0933444555 báo nhầm số yêu cầu xóa ngay"
		idem_leg_dnc = self.unique_key("f01-leg-dnc")
		contact_attempt.record_attempt(
			work_item=wi_leg,
			channel="phone_call",
			outcome_code="do_not_contact",
			note=leg_note,
			idempotency_key=idem_leg_dnc,
			actor=self.staff_a,
		)
		# Simulate pre-fix database state on `wi_leg` (copied `leg_note` + embedded `[H:<16hex>]` in `reason`, empty `private_reason` & `payload_hash`)
		leg_dnc_hist_name = frappe.db.get_value(
			"CSKH Assignment History", {"work_item": wi_leg, "action": "close"}, "name"
		)
		leg_hash_16 = hashlib.sha256(b"legacy-dnc-payload").hexdigest()[:16]
		frappe.db.sql(
			"""
			update `tabCSKH Assignment History`
			set reason = %s, private_reason = null, payload_hash = null
			where name = %s
			""",
			(f"[H:{leg_hash_16}] {leg_note}", leg_dnc_hist_name),
		)
		frappe.db.sql(
			"update `tabCSKH Work Item` set completion_reason = %s where name = %s",
			(leg_note, wi_leg),
		)
		frappe.db.sql(
			"update `tabCRM Lead` set cskh_dnc_reason = %s where name = %s",
			(leg_note, lead_leg),
		)

		# Also simulate a pre-fix manual close row with `[H:<16hex>]` + operator reason
		wi_leg_manual, _ = self.make_assigned_work_item(self.camp_a, self.staff_b)
		contact_attempt.record_attempt(
			work_item=wi_leg_manual,
			channel="phone_call",
			outcome_code="no_answer",
			note="Gọi thử",
			idempotency_key=self.unique_key("f01-leg-att2"),
			actor=self.staff_b,
		)
		idem_leg_close = self.unique_key("f01-leg-close")
		leg_manual_reason = "Khách Đỗ Văn Riêng 0944555666 hẹn sang quý sau"
		contact_attempt.close_work_item(
			work_item=wi_leg_manual,
			completion_reason=leg_manual_reason,
			idempotency_key=idem_leg_close,
			actor=self.staff_b,
		)
		leg_manual_hist_name = frappe.db.get_value(
			"CSKH Assignment History", {"work_item": wi_leg_manual, "action": "close"}, "name"
		)
		orig_full_hash = frappe.db.get_value("CSKH Assignment History", leg_manual_hist_name, "payload_hash")
		self.assertTrue(orig_full_hash and len(orig_full_hash) >= 16)
		frappe.db.sql(
			"""
			update `tabCSKH Assignment History`
			set reason = %s, private_reason = null, payload_hash = null
			where name = %s
			""",
			(f"[H:{orig_full_hash[:16]}] {leg_manual_reason}", leg_manual_hist_name),
		)

		# Run migration twice to prove idempotency
		migrate_p2_assignment_history_privacy()
		migrate_p2_assignment_history_privacy()

		# Verify `BOD` sees 0 PII / 0 customer note / 0 `[H:` on migrated rows
		frappe.set_user(self.bod_user)
		for migrated_wi in (wi_leg, wi_leg_manual):
			mig_bod = ownership_api.history(work_item=migrated_wi)
			mig_json = json.dumps(mig_bod, ensure_ascii=False, default=str)
			self.assertNotIn("Lê Cũ", mig_json)
			self.assertNotIn("0933444555", mig_json)
			self.assertNotIn("Đỗ Văn Riêng", mig_json)
			self.assertNotIn("0944555666", mig_json)
			self.assertIsNone(HASH_TAG_RE.search(mig_json))

		# Verify copied DNC note was scrubbed from `CRM Lead.cskh_dnc_reason` and `CSKH Work Item.completion_reason`
		frappe.set_user("Administrator")
		self.assertNotIn("Lê Cũ", str(frappe.db.get_value("CRM Lead", lead_leg, "cskh_dnc_reason") or ""))
		self.assertNotIn(
			"Lê Cũ", str(frappe.db.get_value("CSKH Work Item", wi_leg, "completion_reason") or "")
		)

		# Verify authorized `Leader A` still reads operator audit reason on `wi_leg_manual` without `[H:`
		frappe.set_user(self.leader_a)
		ldr_mig_hist = ownership_api.history(work_item=wi_leg_manual)
		ldr_mig_json = json.dumps(ldr_mig_hist, ensure_ascii=False, default=str)
		self.assertIn(leg_manual_reason, ldr_mig_json)
		self.assertIsNone(HASH_TAG_RE.search(ldr_mig_json))

		# Verify migrated 16-hex `payload_hash` STILL preserves idempotency replay AND detects payload conflicts!
		replay_after_mig = contact_attempt.close_work_item(
			work_item=wi_leg_manual,
			completion_reason=leg_manual_reason,
			idempotency_key=idem_leg_close,
			actor=self.staff_b,
		)
		self.assertTrue(replay_after_mig["idempotent_replay"])
		with self.assertRaises(IdempotencyConflict):
			contact_attempt.close_work_item(
				work_item=wi_leg_manual,
				completion_reason="Lý do khác để kiểm tra xung đột sau migrate",
				idempotency_key=idem_leg_close,
				actor=self.staff_b,
			)

	# ------------------------------------------------------------------
	# 2. `F-P2-06` — HIGH: Generic API DNC mutation block across all
	#    actors/surfaces + normal Lead writes + import/campaign DNC retention
	# ------------------------------------------------------------------

	def test_f_p2_06_dnc_generic_api_bypass_blocked_across_actors_and_lead_writes_preserved(self):
		"""`F-P2-06`: Direct DNC mutations blocked on ORM/REST v1&v2/`client.*`/Desk `savedocs`; normal Lead edits & audited CSKH DNC flows work; import/distribution keep DNC."""
		wi_a, lead_a = self.make_assigned_work_item(self.camp_a, self.staff_a)

		# Confirm `staff_a` holds active `DocShare(read=1, write=1)` on `lead_a` (the exact pre-fix bypass vector!)
		share_write = frappe.db.get_value(
			"DocShare",
			{"share_doctype": "CRM Lead", "share_name": lead_a, "user": self.staff_a},
			"write",
		)
		self.assertEqual(int(share_write or 0), 1)

		dnc_field_mutations = (
			("cskh_do_not_contact", 1),
			("cskh_dnc_reason", "Giả mạo lý do DNC"),
			("cskh_dnc_at", str(now_datetime())),
			("cskh_dnc_work_item", wi_a),
		)

		# 2A. Every business actor (`Staff`, `Leader`, `BOD`, `CRM Admin`) is blocked from mutating any of the 4 DNC fields
		for actor in (self.staff_a, self.leader_a, self.bod_user, self.admin_user):
			self.assertNotIn("System Manager", frappe.get_roles(actor))
			frappe.set_user(actor)
			for fieldname, bad_val in dnc_field_mutations:
				# `client.set_value` guard
				with self.assertRaises(frappe.PermissionError):
					guarded_crm.client_set_value("CRM Lead", lead_a, fieldname, bad_val)
				with self.assertRaises(frappe.PermissionError):
					guarded_crm.client_set_value("CRM Lead", lead_a, {fieldname: bad_val})
				# `client.bulk_update` guard
				with self.assertRaises(frappe.PermissionError):
					guarded_crm.client_bulk_update(
						[{"doctype": "CRM Lead", "docname": lead_a, fieldname: bad_val}]
					)
				# `frappe.client.save`
				lead_dict = frappe.get_doc("CRM Lead", lead_a).as_dict()
				lead_dict[fieldname] = bad_val
				with self.assertRaises(frappe.PermissionError):
					frappe.client.save(lead_dict)
				# Direct ORM `doc.save(ignore_permissions=True)` STILL blocked by `validate_crm_lead_dnc_fields`!
				orm_doc = frappe.get_doc("CRM Lead", lead_a)
				orm_doc.set(fieldname, bad_val)
				with self.assertRaises(frappe.PermissionError):
					orm_doc.save(ignore_permissions=True)
				# Direct `insert` with pre-populated DNC field blocked
				new_phone = self.next_phone()
				with self.assertRaises(frappe.PermissionError):
					frappe.get_doc(
						{
							"doctype": "CRM Lead",
							"first_name": "Khách Giả Mạo Insert",
							"lead_name": "Khách Giả Mạo Insert",
							"mobile_no": new_phone,
							fieldname: bad_val,
						}
					).insert(ignore_permissions=True)

		# Desk `savedocs` attempt by `staff_a` (with `DocShare(write=1)`) trying to flip `cskh_do_not_contact=1` -> blocked
		frappe.set_user(self.staff_a)
		desk_tampered = frappe.get_doc("CRM Lead", lead_a).as_dict()
		desk_tampered["cskh_do_not_contact"] = 1
		with self.assertRaises(frappe.PermissionError):
			savedocs(json.dumps(desk_tampered, default=str), "Save")

		# Simulated HTTP REST v1, REST v2, and `crm.api.doc.set_value` requests (including spoofed bypass flags in JSON!)
		for method, path, body in (
			(
				"POST",
				"/api/resource/CRM Lead",
				{"first_name": "REST Insert", "cskh_do_not_contact": 0},
			),
			(
				"PUT",
				f"/api/resource/CRM Lead/{lead_a}",
				{
					"cskh_do_not_contact": 1,
					"cskh_in_settings_service": 1,
					"ignore_permissions": 1,
					"_CSKH_AUTHORIZED_LEAD_DNC_MUTATION": True,
				},
			),
			(
				"PUT",
				f"/api/resource/CRM Lead/{lead_a}",
				{"cskh_do_not_contact": 0},
			),
			(
				"PATCH",
				f"/api/v2/document/CRM Lead/{lead_a}",
				{"cskh_dnc_reason": "REST v2 tamper"},
			),
			(
				"POST",
				"/api/method/crm.api.doc.set_value",
				{
					"doctype": "CRM Lead",
					"name": lead_a,
					"fieldname": {"cskh_do_not_contact": 1},
				},
			),
		):
			with _simulated_json_request(method, path, body):
				with self.assertRaises(frappe.PermissionError):
					enforce_lead_dnc_request_guard()

		# DB state on `lead_a` remains 100% untouched
		db_dnc_after_attacks = frappe.db.get_value("CRM Lead", lead_a, list(LEAD_DNC_FIELDS), as_dict=True)
		self.assertEqual(int(db_dnc_after_attacks.cskh_do_not_contact or 0), 0)
		self.assertFalse(bool(db_dnc_after_attacks.cskh_dnc_reason))
		self.assertIsNone(db_dnc_after_attacks.cskh_dnc_at)
		self.assertFalse(bool(db_dnc_after_attacks.cskh_dnc_work_item))

		# 2B. Normal non-DNC Lead edits (`client_set_value`, full round-trip `PUT` guard, Desk `savedocs`, `doc.insert`) work cleanly
		frappe.set_user(self.staff_a)
		guarded_crm.client_set_value("CRM Lead", lead_a, "first_name", "Khách Hợp Lệ Lần 1")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead_a, "first_name"), "Khách Hợp Lệ Lần 1")

		roundtrip_doc = frappe.get_doc("CRM Lead", lead_a).as_dict()
		roundtrip_doc["first_name"] = "Khách Hợp Lệ Qua Desk"
		with _simulated_json_request("PUT", f"/api/resource/CRM Lead/{lead_a}", roundtrip_doc):
			enforce_lead_dnc_request_guard()
		savedocs(json.dumps(roundtrip_doc, default=str), "Save")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead_a, "first_name"), "Khách Hợp Lệ Qua Desk")

		# 2C. Authorized CSKH DNC flows (`record_attempt` DNC -> cannot clear via generic API -> Admin `set_lead_dnc` & `reopen_work_item(override_dnc=True)`)
		with bound_request("POST"):
			dnc_att = workflow_api.record_attempt(
				work_item=wi_a,
				channel="phone_call",
				outcome_code="do_not_contact",
				note="Khách yêu cầu DNC chính thức",
				completion_reason="Đóng theo yêu cầu DNC",
				idempotency_key=self.unique_key("f06-dnc-att"),
			)
		self.assertTrue(dnc_att["do_not_contact"])
		self.assertEqual(int(frappe.db.get_value("CRM Lead", lead_a, "cskh_do_not_contact") or 0), 1)

		# `CRM Admin` STILL cannot clear DNC via generic `client.set_value` or `doc.save()`!
		frappe.set_user(self.admin_user)
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.client_set_value("CRM Lead", lead_a, "cskh_do_not_contact", 0)
		adm_doc = frappe.get_doc("CRM Lead", lead_a)
		adm_doc.cskh_do_not_contact = 0
		with self.assertRaises(frappe.PermissionError):
			adm_doc.save(ignore_permissions=True)
		self.assertEqual(int(frappe.db.get_value("CRM Lead", lead_a, "cskh_do_not_contact") or 0), 1)

		# 2D. Re-importing same phone & running a new campaign distribution NEVER clear `cskh_do_not_contact = 1`
		frappe.set_user("Administrator")
		dnc_phone = frappe.db.get_value("CRM Lead", lead_a, "mobile_no")
		csv_buf = io.StringIO()
		writer = csv.writer(csv_buf)
		writer.writerow(["Số điện thoại", "Họ tên"])
		writer.writerow([dnc_phone, "Tên Import Lại"])
		imp_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "reimport-dnc.csv",
				"is_private": 1,
				"content": csv_buf.getvalue().encode("utf-8"),
			}
		).insert(ignore_permissions=True)

		preview_imp = import_service.create_preview(
			imp_file.name,
			actor=self.admin_user,
			idempotency_key=self.unique_key("f06-imp"),
		)
		with mock.patch("frappe.enqueue"):
			import_service.confirm(preview_imp["batch"], actor=self.admin_user)
		import_worker.run_batch(preview_imp["batch"])
		self.assertEqual(int(frappe.db.get_value("CRM Lead", lead_a, "cskh_do_not_contact") or 0), 1)

		camp_dnc_check = self.make_campaign(
			team_scope=self.team_a,
			title="Chiến dịch kiểm tra giữ DNC",
			selection={"leads": [lead_a]},
		)
		self.make_rule(camp_dnc_check, self.staff_a, weight=100)
		elig_res = eligibility.eligible_leads(camp_dnc_check)
		self.assertEqual(elig_res["eligible"], [])
		self.assertEqual(elig_res["excluded"], [{"lead": lead_a, "reason": "do_not_contact"}])
		self.assertEqual(int(frappe.db.get_value("CRM Lead", lead_a, "cskh_do_not_contact") or 0), 1)

		# Audited Admin DNC override via `workflow.reopen_work_item(override_dnc=True)` & `settings.set_lead_dnc`
		frappe.set_user(self.admin_user)
		with bound_request("POST"):
			reopen_override = workflow_api.reopen_work_item(
				work_item=wi_a,
				target_user=self.staff_a,
				reason="Khách xác nhận bằng văn bản mở lại liên hệ gói mới",
				override_dnc=True,
				idempotency_key=self.unique_key("f06-reopen-ovr"),
			)
		self.assertEqual(reopen_override["work_item_status"], "in_progress")
		self.assertEqual(int(frappe.db.get_value("CRM Lead", lead_a, "cskh_do_not_contact") or 0), 0)

		with bound_request("POST"):
			admin_dnc_set = settings_api.set_lead_dnc(
				lead=lead_a,
				do_not_contact=True,
				reason="Admin khóa DNC theo phiếu tuân thủ",
				idempotency_key=self.unique_key("f06-adm-dnc"),
			)
		self.assertTrue(admin_dnc_set["do_not_contact"])
		self.assertEqual(int(frappe.db.get_value("CRM Lead", lead_a, "cskh_do_not_contact") or 0), 1)
		audit_comments = frappe.db.count(
			"Comment",
			{"reference_doctype": "CRM Lead", "reference_name": lead_a, "comment_type": "Info"},
		)
		self.assertGreaterEqual(audit_comments, 2)

	# ------------------------------------------------------------------
	# 3. `F-P2-02` — MEDIUM: `run_history.runs` across P2 states,
	#    reassignment, revoke, and `L-24` shared-campaign scoping
	# ------------------------------------------------------------------

	def test_f_p2_02_run_history_preserves_counts_across_p2_states_reassignment_and_shared_campaign(self):
		"""`F-P2-02`: `_db_team_assigned_by_run` & global fallback never lose counts on `in_progress`/`on_hold`/`closed`/`revoked` or `reassign`; `active_owned` tracks live ownership; `L-24` preserved."""
		# Create a shared campaign distribution run with EMPTY `actual_json` (forcing the DB fallback path!)
		run_doc = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": self.camp_shared,
				"status": "completed",
				"rule_version": 1,
				"selection_fingerprint": self.unique_key("fp-f02"),
				"idempotency_key": self.unique_key("idem-f02"),
				"progress": 4,
				"preview_json": json.dumps(
					{
						"eligible_count": 4,
						"excluded_count": 0,
						"targets": [
							{"user": self.staff_a, "weight": 50, "planned": 3},
							{"user": self.staff_c, "weight": 50, "planned": 1},
						],
						"simulated": {self.staff_a: 3, self.staff_c: 1},
						"excluded_assignees": [],
					}
				),
				"actual_json": None,
			}
		).insert(ignore_permissions=True)

		def _alloc_for_run(target_user: str) -> tuple[str, str]:
			lead = self.make_canonical_lead()
			wi = frappe.get_doc(
				{
					"doctype": "CSKH Work Item",
					"campaign": self.camp_shared,
					"lead": lead,
					"status": "queued",
					"allocation_run": run_doc.name,
				}
			).insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "CSKH Active Claim",
					"lead": lead,
					"campaign": self.camp_shared,
					"work_item": wi.name,
				}
			).insert(ignore_permissions=True)
			ownership.assign(
				wi.name,
				target_user,
				actor=self.admin_user,
				reason=f"Distribution run {run_doc.name}",
				request_id=self.unique_key("f02-asg"),
				distribution_run=run_doc.name,
			)
			return wi.name, lead

		wi_a1, _ = _alloc_for_run(self.staff_a)
		wi_a2, _ = _alloc_for_run(self.staff_a)
		wi_a3, _ = _alloc_for_run(self.staff_a)
		wi_c1, _ = _alloc_for_run(self.staff_c)

		# Transition `wi_a1 -> in_progress`, `wi_a2 -> on_hold`, `wi_a3 -> closed`, `wi_c1 -> in_progress`
		contact_attempt.record_attempt(
			work_item=wi_a1,
			channel="phone_call",
			outcome_code="no_answer",
			note="Đang chăm sóc",
			idempotency_key=self.unique_key("f02-a1"),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_a2,
			channel="phone_call",
			outcome_code="callback_requested",
			followup={"due_at": self.future_due_str(hours=3), "title": "Hẹn gọi lại F02"},
			idempotency_key=self.unique_key("f02-a2"),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_a3,
			channel="phone_call",
			outcome_code="connected_not_interested",
			close_work_item=True,
			completion_reason="Khách chưa có nhu cầu",
			idempotency_key=self.unique_key("f02-a3"),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_c1,
			channel="phone_call",
			outcome_code="no_answer",
			note="Team B gọi lần 1",
			idempotency_key=self.unique_key("f02-c1"),
			actor=self.staff_c,
		)

		# Reassign `wi_a1` (`in_progress`) from `staff_a -> staff_b` (same Team A), and revoke `wi_a2` (`on_hold`)!
		self.make_rule(self.camp_shared, self.staff_b, weight=50, team=self.team_a)
		ownership.reassign(
			wi_a1,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="Điều phối nội bộ Team A",
			request_id=self.unique_key("f02-reass"),
		)
		ownership.revoke(
			wi_a2,
			expected_owner=self.staff_a,
			return_to_pool=False,
			actor=self.admin_user,
			reason="Thu hồi tạm ngừng",
			request_id=self.unique_key("f02-rev"),
		)

		# Zero items remain in `status == 'assigned'` (`wi_a1` is `in_progress` under `staff_b`, `wi_a2` is `revoked`, `wi_a3` is `closed`)!
		# Verify `Leader A` STILL sees historical allocation `assigned == {staff_a: 3}` AND live `active_owned == {staff_b: 1}`, with `0` Team B leak (`L-24`)!
		frappe.set_user(self.leader_a)
		ldr_a_resp = run_history.runs(run_id=run_doc.name)
		self.assertEqual(len(ldr_a_resp["items"]), 1)
		ldr_a_item = ldr_a_resp["items"][0]
		self.assertEqual(ldr_a_item["allocation_semantics"], "historical_run_allocation")
		self.assertEqual(ldr_a_item["assigned"], {self.staff_a: 3})
		self.assertEqual(ldr_a_item["created"], 3)
		self.assertEqual(ldr_a_item["active_owned"], {self.staff_b: 1})
		self.assertEqual(ldr_a_item["active_owned_total"], 1)
		self.assertNotIn(self.staff_c, json.dumps(ldr_a_resp, ensure_ascii=False, default=str))

		# Verify `Leader B` sees `assigned == {staff_c: 1}`, `active_owned == {staff_c: 1}`, and `0` Team A leak
		frappe.set_user(self.leader_b)
		ldr_b_item = run_history.runs(run_id=run_doc.name)["items"][0]
		self.assertEqual(ldr_b_item["assigned"], {self.staff_c: 1})
		self.assertEqual(ldr_b_item["active_owned"], {self.staff_c: 1})
		self.assertEqual(ldr_b_item["active_owned_total"], 1)
		self.assertNotIn(self.staff_a, json.dumps(ldr_b_item, ensure_ascii=False, default=str))
		self.assertNotIn(self.staff_b, json.dumps(ldr_b_item, ensure_ascii=False, default=str))

		# Verify `CRM Admin` & `BOD` see full historical allocation `{staff_a: 3, staff_c: 1}` AND live `active_owned == {staff_b: 1, staff_c: 1}` (`active_owned_total == 2`)
		for global_reader in (self.admin_user, self.bod_user):
			frappe.set_user(global_reader)
			glob_item = run_history.runs(run_id=run_doc.name)["items"][0]
			self.assertEqual(glob_item["allocation_semantics"], "historical_run_allocation")
			self.assertEqual(glob_item["assigned"], {self.staff_a: 3, self.staff_c: 1})
			self.assertEqual(glob_item["created"], 4)
			self.assertEqual(glob_item["active_owned"], {self.staff_b: 1, self.staff_c: 1})
			self.assertEqual(glob_item["active_owned_total"], 2)

	# ------------------------------------------------------------------
	# 4. `F-P2-04A` & `F-P2-04B` — MEDIUM: SQL `care_state` before
	#    LIMIT/OFFSET, `owner_at_close` vs `closed_by`, and `L-23` denial
	# ------------------------------------------------------------------

	def test_f_p2_04_server_side_care_state_pagination_closed_owner_vs_actor_and_l23_denial(self):
		"""`F-P2-04`: `care_state` filters in SQL before `LIMIT/OFFSET` (finds items on page 3 when pages 1-2 have 0 matches); `owner_at_close` != `closed_by`; `L-23` blocks former/cross-team PII."""
		camp_q = self.make_campaign(team_scope=self.team_a, title="Chiến dịch F-P2-04 Phân trang")
		self.make_rule(camp_q, self.staff_a, weight=50)
		self.make_rule(camp_q, self.staff_b, weight=50)

		now_dt = now_datetime()
		# Create 6 OLDEST items first (so under default `recent_activity` DESC sort, they sit on Page 3 behind 20 newer unattempted items!)
		wi_conn_1, _ = self.make_assigned_work_item(camp_q, self.staff_a)
		wi_conn_2, _ = self.make_assigned_work_item(camp_q, self.staff_a)
		wi_not_conn_1, _ = self.make_assigned_work_item(camp_q, self.staff_a)
		wi_not_conn_2, _ = self.make_assigned_work_item(camp_q, self.staff_a)
		wi_due, _ = self.make_assigned_work_item(camp_q, self.staff_a)
		wi_overdue, _ = self.make_assigned_work_item(camp_q, self.staff_a)

		for wi in (wi_conn_1, wi_conn_2):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="connected_interested",
				note="Khách quan tâm thật ở trang 3",
				idempotency_key=self.unique_key("f04-conn"),
				actor=self.staff_a,
			)
		for wi in (wi_not_conn_1, wi_not_conn_2):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="no_answer",
				note="Không bắt máy ở trang 3",
				idempotency_key=self.unique_key("f04-nc"),
				actor=self.staff_a,
			)
		followup.schedule_followup(
			work_item=wi_due,
			due_at=str(add_to_date(now_dt, hours=5)),
			title="Hẹn chưa quá hạn",
			idempotency_key=self.unique_key("f04-due"),
			actor=self.staff_a,
		)
		fu_ov = followup.schedule_followup(
			work_item=wi_overdue,
			due_at=str(add_to_date(now_dt, hours=2)),
			title="Hẹn đã quá hạn",
			idempotency_key=self.unique_key("f04-ov"),
			actor=self.staff_a,
		)
		past_due = add_to_date(now_dt, hours=-3)
		frappe.db.set_value("CRM Task", fu_ov["task_id"], "due_date", past_due, update_modified=False)
		frappe.db.set_value("CSKH Work Item", wi_overdue, "next_action_due", past_due, update_modified=False)

		# Push `modified` & `assigned_at` of these 6 worked items 2 hours earlier so 20 brand-new `unattempted` items occupy Pages 1 & 2 (`page_size=10`)!
		older_ts = add_to_date(now_dt, hours=-2)
		for wi in (wi_conn_1, wi_conn_2, wi_not_conn_1, wi_not_conn_2, wi_due, wi_overdue):
			frappe.db.sql(
				"update `tabCSKH Work Item` set modified = %s, assigned_at = %s where name = %s",
				(older_ts, older_ts, wi),
			)

		unattempted_wis = []
		for _ in range(20):
			wi_u, _ = self.make_assigned_work_item(camp_q, self.staff_a)
			unattempted_wis.append(wi_u)

		# Also add 1 closed DNC item in `camp_q`
		wi_dnc, _ = self.make_assigned_work_item(camp_q, self.staff_a)
		contact_attempt.record_attempt(
			work_item=wi_dnc,
			channel="phone_call",
			outcome_code="do_not_contact",
			note="Khách báo DNC",
			completion_reason="Đóng DNC",
			idempotency_key=self.unique_key("f04-dnc"),
			actor=self.staff_a,
		)

		frappe.set_user(self.staff_a)
		# Sanity check: unfiltered Page 1 (`page_size=10`) has ONLY `unattempted` items (proving worked items are off Page 1 & Page 2!)
		p1_unfiltered = workflow_api.list_work_items(campaign=camp_q, page=1, page_size=10)
		self.assertEqual(p1_unfiltered["total"], 26)
		self.assertTrue(all(it["attempt_count"] == 0 for it in p1_unfiltered["items"]))

		# Now filter `care_state` on `page=1, page_size=10` -> MUST find exact off-page matches and `total` == full item count!
		expected_by_care_state = {
			"connected": {wi_conn_1, wi_conn_2},
			"attempted_not_connected": {wi_not_conn_1, wi_not_conn_2},
			"attempted": {wi_conn_1, wi_conn_2, wi_not_conn_1, wi_not_conn_2},
			"on_hold": {wi_due, wi_overdue},
			"followup_due": {wi_due, wi_overdue},
			"followup_overdue": {wi_overdue},
			"unattempted": set(unattempted_wis),
			"closed": {wi_dnc},
			"do_not_contact": {wi_dnc},
		}
		for c_state, expected_set in expected_by_care_state.items():
			with self.subTest(care_state=c_state):
				res_p1 = workflow_api.list_work_items(
					campaign=camp_q, care_state=c_state, page=1, page_size=10
				)
				self.assertEqual(res_p1["total"], len(expected_set))
				collected: list[str] = []
				p_num = 1
				while True:
					pg = workflow_api.list_work_items(
						campaign=camp_q, care_state=c_state, page=p_num, page_size=10
					)
					self.assertEqual(pg["total"], len(expected_set))
					collected.extend(it["work_item"] for it in pg["items"])
					if len(collected) >= pg["total"] or not pg["items"]:
						break
					p_num += 1
				self.assertEqual(set(collected), expected_set)
				self.assertEqual(len(collected), len(expected_set))

		# Invalid `care_state` raises `InvalidRequest` (`400`)
		with self.assertRaises(InvalidRequest):
			workflow_api.list_work_items(campaign=camp_q, care_state="not_a_valid_state")

		# 4B. Distinguish `owner_at_close` (`staff_a`) vs `closed_by` (`leader_a`) on closed Work Item!
		frappe.set_user("Administrator")
		wi_ldr_closed, lead_ldr_closed = self.make_assigned_work_item(camp_q, self.staff_a)
		contact_attempt.record_attempt(
			work_item=wi_ldr_closed,
			channel="phone_call",
			outcome_code="connected_not_interested",
			note="Khách từ chối gói",
			idempotency_key=self.unique_key("f04-ldr-att"),
			actor=self.staff_a,
		)
		close_out = contact_attempt.close_work_item(
			work_item=wi_ldr_closed,
			completion_reason="Leader A duyệt đóng hồ sơ của Staff A",
			idempotency_key=self.unique_key("f04-ldr-close"),
			actor=self.leader_a,
		)
		self.assertEqual(close_out["owner_at_close"], self.staff_a)
		self.assertEqual(close_out["closed_by"], self.leader_a)

		# Also verify `backfill_closed_work_item_owners()` reconstructs `owner_at_close` if cleared on legacy row
		frappe.db.set_value("CSKH Work Item", wi_ldr_closed, "owner_at_close", None, update_modified=False)
		backfill_closed_work_item_owners()
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi_ldr_closed, "owner_at_close"), self.staff_a)

		frappe.set_user(self.leader_a)
		by_owner = workflow_api.list_work_items(campaign=camp_q, status="closed", owner_at_close=self.staff_a)
		self.assertIn(wi_ldr_closed, {x["work_item"] for x in by_owner["items"]})
		by_assignee_closed = workflow_api.list_work_items(
			campaign=camp_q, status="closed", assignee=self.staff_a
		)
		self.assertIn(wi_ldr_closed, {x["work_item"] for x in by_assignee_closed["items"]})
		by_closer = workflow_api.list_work_items(campaign=camp_q, status="closed", closed_by=self.leader_a)
		self.assertEqual([x["work_item"] for x in by_closer["items"]], [wi_ldr_closed])
		self.assertEqual(by_closer["items"][0]["owner_at_close"], self.staff_a)
		self.assertEqual(by_closer["items"][0]["closed_by"], self.leader_a)

		# `Staff A` (who owned `wi_ldr_closed` when `Leader A` closed it!) can view `wi_ldr_closed` while Lead remains unallocated
		frappe.set_user(self.staff_a)
		staff_closed_list = workflow_api.list_work_items(campaign=camp_q, status="closed")
		self.assertIn(wi_ldr_closed, {x["work_item"] for x in staff_closed_list["items"]})
		detail_closed = workflow_api.get_work_item(work_item=wi_ldr_closed)
		self.assertEqual(detail_closed["work_item"]["owner_at_close"], self.staff_a)
		self.assertEqual(detail_closed["work_item"]["closed_by"], self.leader_a)

		# 4C. Pre-close former owner (`staff_a -> staff_b` BEFORE `staff_b` closes) MUST NOT regain PII access!
		frappe.set_user("Administrator")
		wi_reass_closed, _ = self.make_assigned_work_item(camp_q, self.staff_a)
		ownership.reassign(
			wi_reass_closed,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="Chuyển sang Staff B trước khi đóng",
			request_id=self.unique_key("f04-reass-b"),
		)
		contact_attempt.record_attempt(
			work_item=wi_reass_closed,
			channel="phone_call",
			outcome_code="connected_not_interested",
			close_work_item=True,
			completion_reason="Staff B đóng hồ sơ",
			idempotency_key=self.unique_key("f04-b-close"),
			actor=self.staff_b,
		)
		frappe.set_user(self.staff_a)
		self.assertNotIn(
			wi_reass_closed,
			{x["work_item"] for x in workflow_api.list_work_items(campaign=camp_q, status="closed")["items"]},
		)
		with self.assertRaises(Forbidden):
			workflow_api.get_work_item(work_item=wi_reass_closed)

		# 4D. `L-23` / `SEC2-07`: Once `lead_ldr_closed` is assigned to Team B (`staff_c`), BOTH `staff_a` AND `leader_a` lose access!
		frappe.set_user("Administrator")
		self.make_assigned_work_item(self.camp_b, self.staff_c, lead=lead_ldr_closed)

		for revoked_actor in (self.staff_a, self.leader_a):
			frappe.set_user(revoked_actor)
			closed_after_move = workflow_api.list_work_items(campaign=camp_q, status="closed")
			self.assertNotIn(wi_ldr_closed, {x["work_item"] for x in closed_after_move["items"]})
			with self.assertRaises(Forbidden):
				workflow_api.get_work_item(work_item=wi_ldr_closed)

	# ------------------------------------------------------------------
	# 5. `F-P2-05` — MEDIUM: Half-open date bounds, DISTINCT Work Item
	#    counters, effective corrections/void/restore, and DNC units
	# ------------------------------------------------------------------

	def test_f_p2_05_progress_summary_half_open_dates_distinct_wi_counters_overlays_and_dnc_units(self):
		"""`F-P2-05`: Date-only `date_to` covers `[start_of_day, start_of_next_day)` including `23:59:55`; explicit datetimes & validation work; `DISTINCT` Work Item counters reconcile with `correct`/`void`/`restore`; `dnc_leads` != `dnc_work_items`."""
		camp_p = self.make_campaign(team_scope=self.team_a, title="Chiến dịch F-P2-05 Báo cáo")
		camp_p2 = self.make_campaign(team_scope=self.team_a, title="Chiến dịch F-P2-05 Phụ DNC")
		self.make_rule(camp_p, self.staff_a, weight=100)
		self.make_rule(camp_p2, self.staff_a, weight=100)

		# Allow up to 7-day (`10000` min) backdated `occurred_at` in `camp_p` & `camp_p2` so morning/night timestamps are 100% legal
		for c_id, p_code in ((camp_p, self.unique_key("POLF05A")), (camp_p2, self.unique_key("POLF05B"))):
			policies.save_policy(
				policy_code=p_code,
				title="Chính sách test F-P2-05",
				scope_type="Campaign",
				campaign=c_id,
				allowed_backdate_minutes=10000,
				idempotency_key=self.unique_key("f05-pol"),
				actor=self.admin_user,
			)

		# Choose deterministic past dates relative to system `now_datetime()` so timestamps are NEVER in the future
		target_day = add_days(getdate(now_datetime()), -2)
		next_day = add_days(target_day, 1)
		cohort_created_at = f"{add_days(target_day, -2)} 09:00:00"
		morning_ts = f"{target_day} 08:05:00"
		late_night_ts = f"{target_day} 23:59:55"
		next_midnight_ts = f"{next_day} 00:00:00"

		# Create 4 Work Items in `camp_p` (`cohort_work_items == 4`):
		#   - `wi_multi`: 3 attempts on `target_day` (`08:05:00` no_answer, `14:00:00` busy, `23:59:55` connected_interested)
		#   - `wi_nc`: 1 attempt on `target_day` (`10:30:00` no_answer) -> later corrected/voided/restored
		#   - `wi_next_day`: 1 attempt at `next_day 00:00:00` (must be EXCLUDED from `target_day`!)
		#   - `wi_untouched`: 0 attempts
		wi_multi, lead_shared_dnc = self.make_assigned_work_item(camp_p, self.staff_a)
		wi_nc, _ = self.make_assigned_work_item(camp_p, self.staff_a)
		wi_next_day, _ = self.make_assigned_work_item(camp_p, self.staff_a)
		wi_untouched, _ = self.make_assigned_work_item(camp_p, self.staff_a)

		for wi in (wi_multi, wi_nc, wi_next_day, wi_untouched):
			frappe.db.sql(
				"update `tabCSKH Work Item` set creation = %s, assigned_at = %s where name = %s",
				(cohort_created_at, cohort_created_at, wi),
			)
			frappe.db.sql(
				"""
				update `tabCSKH Assignment History`
				set event_at = %s, ownership_start = %s
				where work_item = %s and action = 'assign'
				""",
				(cohort_created_at, cohort_created_at, wi),
			)

		contact_attempt.record_attempt(
			work_item=wi_multi,
			channel="phone_call",
			outcome_code="no_answer",
			occurred_at=morning_ts,
			note="Sáng gọi chưa nghe",
			idempotency_key=self.unique_key("f05-m1"),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_multi,
			channel="phone_call",
			outcome_code="busy",
			occurred_at=f"{target_day} 14:00:00",
			note="Chiều máy bận",
			idempotency_key=self.unique_key("f05-m2"),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_multi,
			channel="phone_call",
			outcome_code="connected_interested",
			occurred_at=late_night_ts,
			note="Tối muộn 23:59:55 khách bắt máy quan tâm",
			idempotency_key=self.unique_key("f05-m3"),
			actor=self.staff_a,
		)

		att_nc = contact_attempt.record_attempt(
			work_item=wi_nc,
			channel="phone_call",
			outcome_code="no_answer",
			occurred_at=f"{target_day} 10:30:00",
			note="Gọi lần 1 wi_nc",
			idempotency_key=self.unique_key("f05-nc1"),
			actor=self.staff_a,
		)
		att_boundary = contact_attempt.record_attempt(
			work_item=wi_next_day,
			channel="phone_call",
			outcome_code="connected_interested",
			occurred_at=next_midnight_ts,
			note="Đúng 00:00:00 ngày kế tiếp",
			idempotency_key=self.unique_key("f05-bnd"),
			actor=self.staff_a,
		)

		# 5A. Date-only `date_from == date_to == target_day` uses half-open `[00:00:00, next_day 00:00:00)`:
		#     Includes BOTH `08:05:00` AND `23:59:55`, excludes `next_day 00:00:00`!
		#     `effective_attempts == 4` (3 on `wi_multi` + 1 on `wi_nc`), while `DISTINCT` `attempted_work_items == 2`,
		#     `unattempted_work_items == 2` (`wi_next_day` + `wi_untouched`), `connected_work_items == 1` (`wi_multi`).
		frappe.set_user(self.leader_a)
		sum_day = workflow_api.progress_summary(
			campaign=camp_p,
			date_from=str(target_day),
			date_to=str(target_day),
		)
		self.assertEqual(sum_day["period"]["end_bound_mode"], "exclusive")
		self.assertEqual(sum_day["period"]["date_to"], next_midnight_ts)
		cards = sum_day["cards"]
		self.assertEqual(cards["cohort_work_items"], 4)
		self.assertEqual(cards["effective_attempts"], 4)
		self.assertEqual(cards["attempted_work_items"], 2)
		self.assertEqual(cards["unattempted_work_items"], 2)
		self.assertEqual(
			cards["attempted_work_items"] + cards["unattempted_work_items"],
			cards["cohort_work_items"],
		)
		self.assertEqual(cards["connected_attempts"], 1)
		self.assertEqual(cards["connected_work_items"], 1)

		# 5B. Explicit datetime bounds `[08:00:00, 12:00:00]` keep inclusive explicit datetime semantics (excludes `14:00:00` & `23:59:55`)
		sum_morning = workflow_api.progress_summary(
			campaign=camp_p,
			date_from=f"{target_day} 08:00:00",
			date_to=f"{target_day} 12:00:00",
		)
		self.assertEqual(sum_morning["period"]["end_bound_mode"], "inclusive")
		self.assertEqual(sum_morning["cards"]["effective_attempts"], 2)
		self.assertEqual(sum_morning["cards"]["attempted_work_items"], 2)
		self.assertEqual(sum_morning["cards"]["connected_work_items"], 0)

		# Invalid dates & inverted ranges fail closed with `InvalidRequest` (`400`)
		for bad_from, bad_to in (
			("2026-02-30", str(target_day)),
			(str(target_day), "not-a-date"),
			(str(next_day), str(target_day)),
			(f"{target_day} 18:00:00", f"{target_day} 09:00:00"),
		):
			with self.assertRaises(InvalidRequest):
				workflow_api.progress_summary(campaign=camp_p, date_from=bad_from, date_to=bad_to)

		# 5C. Effective `void` -> `restore` -> `correct` (outcome + `occurred_at` across day boundary!)
		# Void `att_nc` -> `attempted_work_items` drops `2 -> 1`, `unattempted_work_items` rises `2 -> 3`
		correction.request_correction(
			attempt=att_nc["attempt_id"],
			action="void",
			reason="Nhập nhầm cuộc gọi",
			auto_approve=True,
			idempotency_key=self.unique_key("f05-void"),
			actor=self.leader_a,
		)
		cards_after_void = workflow_api.progress_summary(
			campaign=camp_p, date_from=str(target_day), date_to=str(target_day)
		)["cards"]
		self.assertEqual(cards_after_void["effective_attempts"], 3)
		self.assertEqual(cards_after_void["attempted_work_items"], 1)
		self.assertEqual(cards_after_void["unattempted_work_items"], 3)

		# Restore `att_nc` (Admin-only action) and correct its `outcome_code` to `connected_interested` -> `connected_work_items` rises `1 -> 2`!
		correction.request_correction(
			attempt=att_nc["attempt_id"],
			action="restore",
			reason="Khôi phục bản ghi đúng",
			auto_approve=True,
			idempotency_key=self.unique_key("f05-restore"),
			actor=self.admin_user,
		)
		correction.request_correction(
			attempt=att_nc["attempt_id"],
			action="correct",
			corrected_fields={
				"outcome_code": "connected_interested",
				"note": "Khách thực tế có nghe máy và quan tâm",
			},
			reason="Cập nhật đúng kết quả nghe máy",
			auto_approve=True,
			idempotency_key=self.unique_key("f05-corr-conn"),
			actor=self.leader_a,
		)
		# Also correct `att_boundary`'s `occurred_at` from `next_day 00:00:00` back into `target_day 22:30:00`!
		correction.request_correction(
			attempt=att_boundary["attempt_id"],
			action="correct",
			corrected_fields={"occurred_at": f"{target_day} 22:30:00"},
			reason="Ghi nhận cuộc gọi diễn ra lúc 22:30 đêm trước",
			auto_approve=True,
			idempotency_key=self.unique_key("f05-corr-occ"),
			actor=self.leader_a,
		)

		cards_after_corr = workflow_api.progress_summary(
			campaign=camp_p, date_from=str(target_day), date_to=str(target_day)
		)["cards"]
		self.assertEqual(cards_after_corr["effective_attempts"], 5)
		self.assertEqual(cards_after_corr["attempted_work_items"], 3)
		self.assertEqual(cards_after_corr["unattempted_work_items"], 1)
		self.assertEqual(cards_after_corr["connected_attempts"], 3)
		self.assertEqual(cards_after_corr["connected_work_items"], 3)

		# 5D. Distinct `dnc_leads` vs `dnc_work_items`: close `wi_multi` in `camp_p` first, then create 2nd Work Item for SAME `lead_shared_dnc` in `camp_p2` and mark DNC!
		frappe.set_user("Administrator")
		contact_attempt.close_work_item(
			work_item=wi_multi,
			completion_reason="Kết thúc chiến dịch 1",
			idempotency_key=self.unique_key("f05-close-c1"),
			actor=self.staff_a,
		)
		wi_camp2, _ = self.make_assigned_work_item(camp_p2, self.staff_a, lead=lead_shared_dnc)
		contact_attempt.record_attempt(
			work_item=wi_camp2,
			channel="phone_call",
			outcome_code="do_not_contact",
			note="Khách báo ngừng liên hệ ở chiến dịch 2",
			completion_reason="Đóng DNC chiến dịch 2",
			idempotency_key=self.unique_key("f05-dnc-c2"),
			actor=self.staff_a,
		)

		# Across `Leader A` team scope (spanning BOTH `camp_p` and `camp_p2`), `lead_shared_dnc` is 1 Lead linked to 2 Work Items!
		frappe.set_user(self.leader_a)
		team_sum = workflow_api.progress_summary()
		self.assertGreaterEqual(team_sum["cards"]["dnc_leads"], 1)
		self.assertGreater(team_sum["cards"]["dnc_work_items"], team_sum["cards"]["dnc_leads"])

		# 5E. Cross-team isolation on shared campaign + `BOD` 0-PII verification
		frappe.set_user("Administrator")
		wi_sh_a, lead_sh_a = self.make_assigned_work_item(self.camp_shared, self.staff_a)
		wi_sh_c, _ = self.make_assigned_work_item(self.camp_shared, self.staff_c)
		contact_attempt.record_attempt(
			work_item=wi_sh_a,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Ghi chú riêng Team A trên chiến dịch chung",
			idempotency_key=self.unique_key("f05-sha"),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_sh_c,
			channel="phone_call",
			outcome_code="no_answer",
			note="Ghi chú riêng Team B trên chiến dịch chung",
			idempotency_key=self.unique_key("f05-shc"),
			actor=self.staff_c,
		)

		frappe.set_user(self.leader_a)
		sh_ldr_a = workflow_api.progress_summary(campaign=self.camp_shared)
		self.assertEqual(sh_ldr_a["cards"]["cohort_work_items"], 1)
		self.assertEqual(sh_ldr_a["cards"]["attempted_work_items"], 1)
		self.assertEqual(sh_ldr_a["cards"]["connected_work_items"], 1)
		self.assertNotIn(self.staff_c, json.dumps(sh_ldr_a, ensure_ascii=False, default=str))

		frappe.set_user(self.leader_b)
		sh_ldr_b = workflow_api.progress_summary(campaign=self.camp_shared)
		self.assertEqual(sh_ldr_b["cards"]["cohort_work_items"], 1)
		self.assertEqual(sh_ldr_b["cards"]["attempted_work_items"], 1)
		self.assertEqual(sh_ldr_b["cards"]["connected_work_items"], 0)
		self.assertNotIn(self.staff_a, json.dumps(sh_ldr_b, ensure_ascii=False, default=str))

		frappe.set_user(self.bod_user)
		bod_sh = workflow_api.progress_summary(campaign=self.camp_shared)
		self.assertEqual(bod_sh["cards"]["cohort_work_items"], 2)
		self.assertEqual(bod_sh["cards"]["attempted_work_items"], 2)
		self.assertEqual(bod_sh["cards"]["connected_work_items"], 1)
		bod_sh_json = json.dumps(bod_sh, ensure_ascii=False, default=str)
		self.assertIsNone(PHONE_RE.search(bod_sh_json))
		self.assertNotIn(lead_sh_a, bod_sh_json)
		self.assertNotIn("Ghi chú riêng Team A", bod_sh_json)
