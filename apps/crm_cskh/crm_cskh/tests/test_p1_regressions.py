"""P1 — Regression suite for the T1C baseline & round-2 findings L-01..L-22.

Every test in this module **reproduces one finding of the T1C verification
reports** (`deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md` and
`deploy/dev/evidence/t1c-p1-verification-report-round2-2026-11-02.md`) and pins
the fixed behaviour. The module is deliberately separate from the functional P1
suites so a regression in a *fix* cannot be masked by the functional test that
motivated it.

| Finding | Symptom before the fix | Test |
|---|---|---|
| L-01 | `reconcile()` read `assigned_to` from `CSKH Assignment History` (SQL error) and counted the whole-site history | `TestL01Reconcile` |
| L-02 | `_checksum()` crashed on the `str` returned by `File.get_content()` for a file flushed to disk | `TestL02Checksum` |
| L-03 | `_resolve_column` returned the **cell value** instead of the **column name** | `TestL03ColumnResolution` |
| L-04 | `pool_list()` emitted a backtick around an aliased column name | `TestL04PoolQuery` |
| L-05 | The dashboard **reported** the scope but never **applied** it | `TestL05DashboardScope` |
| L-06 | The `campaign` filter raised `KeyError` / `ValueError` | `TestL06CampaignFilter` |
| L-07 | A leader could not read the rules / runs of their own campaign | `TestL07LeaderRead` |
| L-08 | `CSKH Assignment History` never carried `distribution_run` | `TestL08AuditTrail` |
| L-09 | `cskh_is_placeholder_name` did not exist | `TestL09PlaceholderField` |
| L-10 | `capacity_limit = 0` (unlimited) was not written down and enforced consistently | `TestL10Capacity` |
| L-13 | No formula / HTML sanitization on the import cells and on the error artifact | `TestL13Sanitization` |
| L-14 | `pool_list` `total` was not scoped like `items` | `TestL04PoolQuery` |
| L-15 | `frappe.get_all` bypassed the row-level permission | `TestL15NoPermissionBypass` |
| L-16 | HTTP error envelope lacked stable `code`/`p1_code` while File-oracle must stay byte-identical | `TestL16ErrorEnvelopeAndFileOracle` |
| L-17 | `CSKH CRM Admin` fell back to `scope="self"` in `services/reporting.py::_scope()` | `TestL17CrmAdminReportingScope` |
| L-18 | First-time `imports.confirm()` left batch at `CONFIRMED`, breaking `CONFIRMED -> RUNNING` in worker | `TestL18FirstConfirmToWorker` |
| L-19 / L-20 | Import `>200` rows truncated worker input / missed error artifact rows / double-counted errors | `TestL19L20LargeImportAndErrorArtifact` |
| L-21 | `api.ownership.revoke` used `bool(return_to_pool)`, turning `"false"`/`"0"` into `True` | `TestL21RevokeBooleanParsing` |
| L-22 | Leader dashboard missed unassigned (`assigned_to IS NULL`) `revoked`/`closed`/`active_claims` and diverged on `unique_customers` | `TestL22LeaderUnassignedAndUniqueCustomers` |
"""

import csv
import io
import json
import os
import unittest
from unittest import mock

import frappe

from crm_cskh.errors import _canonical_not_found_body, normalize_error_response
from crm_cskh.jobs import import_worker
from crm_cskh.p1_errors import clear_p1_error_context, normalize_p1_error_response
from crm_cskh.services import distribution, eligibility, import_service, reporting
from crm_cskh.services.import_service import (
	_classify,
	_resolve_column_name,
	create_preview,
	sanitize_cell,
)
from crm_cskh.services.ownership import assign as ownership_assign
from crm_cskh.services.phone_identity import normalize_vn_phone
from crm_cskh.tests.p1_base import P1TestCase


def build_csv(rows, header=("Số điện thoại",)):
	"""Build a CSV payload in memory."""
	buffer = io.StringIO()
	writer = csv.writer(buffer)
	writer.writerow(header)
	for row in rows:
		writer.writerow(row)
	return buffer.getvalue().encode("utf-8")


class TestL01Reconcile(P1TestCase):
	"""L-01 — `reconcile()` must not raise and must count the campaign only.

	Before the fix the query selected `h.assigned_to`, a column that does not
	exist on `CSKH Assignment History` (the column is `to_user`), so **every**
	HTTP call to the reconcile endpoint returned 500. On top of that, the
	events of the whole installation were added to the reconciliation of a
	single campaign.
	"""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.other_campaign = self.make_campaign()
		self.leads = [self.make_canonical_lead() for _ in range(3)]
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": self.leads})
		)
		self.make_rule(self.campaign, self.staff_a, weight=10)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		distribution.execute(preview["preview_id"], actor=self.admin_user)

	def test_reconcile_does_not_raise_and_reports_the_five_numbers(self):
		report = distribution.reconcile(self.campaign, actor=self.admin_user)
		for key in (
			"campaign",
			"current_owners",
			"assignment_events",
			"distinct_leads",
			"active_work_items",
			"active_claims",
			"as_of",
		):
			self.assertIn(key, report)
		self.assertEqual(report["campaign"], self.campaign)
		self.assertEqual(report["distinct_leads"], 3)
		self.assertEqual(report["active_work_items"], 3)
		self.assertEqual(report["active_claims"], 3)

	def test_the_events_are_scoped_to_the_campaign(self):
		"""The events of another campaign never enter this reconciliation."""
		other_leads = [self.make_canonical_lead() for _ in range(2)]
		frappe.db.set_value(
			"CSKH Campaign",
			self.other_campaign,
			"selection_json",
			frappe.as_json({"leads": other_leads}),
		)
		self.make_rule(self.other_campaign, self.staff_b, weight=10)
		other_preview = distribution.preview(self.other_campaign, actor=self.admin_user)
		distribution.execute(other_preview["preview_id"], actor=self.admin_user)

		scoped = distribution.reconcile(self.campaign, actor=self.admin_user)
		self.assertEqual(sum(scoped["assignment_events"].values()), 3)
		# The whole installation has more events than this campaign.
		total_events = frappe.db.count("CSKH Assignment History", {"action": "assign"})
		self.assertGreater(total_events, 3)
		self.assertLess(sum(scoped["assignment_events"].values()), total_events)

	def test_the_current_owner_is_read_from_the_work_item(self):
		"""The current owner and the event counter are independent numbers."""
		report = distribution.reconcile(self.campaign, actor=self.admin_user)
		self.assertEqual(sum(report["current_owners"].values()), 3)
		self.assertEqual(sum(report["assignment_events"].values()), 3)
		self.assertEqual(
			sorted(report["current_owners"].keys()),
			sorted(report["assignment_events"].keys()),
		)


class TestL02Checksum(P1TestCase):
	"""L-02 — the checksum must work for `str` **and** `bytes`.

	`File.get_content()` returns `str` when the file has been flushed to disk
	and `bytes` while it is still in memory. The pipeline therefore always
	works on bytes.
	"""

	def test_the_checksum_is_identical_for_bytes_and_str(self):
		content = "Số điện thoại\n0911111001\n".encode()
		from_bytes = import_service._checksum(content)
		from_str = import_service._checksum(content.decode("utf-8"))
		self.assertEqual(from_bytes, from_str)
		self.assertEqual(len(from_bytes), 64)

	def test_a_file_flushed_to_disk_is_importable(self):
		"""The real path: the file is written to disk and read back."""
		phone = self.unique_phone()
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "l02-disk.csv",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": build_csv([(phone,)]),
			}
		)
		file_doc.insert(ignore_permissions=True)
		file_doc.save(ignore_permissions=True)
		frappe.db.commit()

		# Force the file onto the disk so `get_content()` returns `str`.
		reloaded = frappe.get_doc("File", file_doc.name)
		content = reloaded.get_content()
		self.assertIsInstance(content, str)
		self.assertIsInstance(import_service._file_content(file_doc.name)[1], bytes)

		response = create_preview(
			file_doc.name, actor=self.admin_user, idempotency_key=self.unique_key("l02")
		)
		self.assertEqual(response["status"], "DRY_RUN")
		self.assertEqual(response["totals"]["valid_rows"], 1)

	def test_a_utf8_bom_csv_is_importable(self):
		phone = self.unique_phone()
		content = b"\xef\xbb\xbf" + build_csv([(phone,)])
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "l02-bom.csv",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": content,
			}
		)
		file_doc.insert(ignore_permissions=True)
		response = create_preview(
			file_doc.name, actor=self.admin_user, idempotency_key=self.unique_key("l02-bom")
		)
		self.assertEqual(response["totals"]["valid_rows"], 1)
		self.assertEqual(response["totals"]["error_rows"], 0)

	def test_a_real_xlsx_is_importable(self):
		openpyxl = __import__("openpyxl")
		workbook = openpyxl.Workbook()
		sheet = workbook.active
		sheet.append(["Số điện thoại"])
		sheet.append([self.unique_phone()])
		buffer = io.BytesIO()
		workbook.save(buffer)
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "l02.xlsx",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": buffer.getvalue(),
			}
		)
		file_doc.insert(ignore_permissions=True)
		response = create_preview(
			file_doc.name, actor=self.admin_user, idempotency_key=self.unique_key("l02-xlsx")
		)
		self.assertEqual(response["totals"]["valid_rows"], 1)
		self.assertEqual(response["totals"]["error_rows"], 0)


class TestL03ColumnResolution(P1TestCase):
	"""L-03 — the mapping must resolve the **column name** of the phone.

	Before the fix `_resolve_column` returned the **value** of the cell, so the
	classification looked up a column named `0911113001` and every row was
	reported as `no_phone_column`.
	"""

	def test_the_resolution_returns_a_column_name_not_a_value(self):
		cells = {"Tên": "Nguyễn Văn A", "Số điện thoại": "0911113001"}
		column = _resolve_column_name(cells, None, {"Số điện thoại"})
		self.assertEqual(column, "Số điện thoại")
		self.assertIn(column, cells)
		# The **value** must never be returned as a column name.
		self.assertNotEqual(column, "0911113001")

	def test_the_mapping_with_several_fields_prefers_the_phone_column(self):
		"""A mapping of several fields must not read the *name* column."""
		cells = {"Tên": "Nguyễn Văn A", "Số điện thoại": "0911113001", "Nguồn": "Facebook"}
		mapping = {"name": "Tên", "source": "Nguồn", "phone": "Số điện thoại"}
		column = _resolve_column_name(cells, mapping, {"Số điện thoại"})
		self.assertEqual(column, "Số điện thoại")

	def test_a_mapping_without_a_phone_field_falls_back_to_a_phone_header(self):
		cells = {"Tên": "Nguyễn Văn A", "mobile_no": "0911113001"}
		column = _resolve_column_name(cells, {"name": "Tên"}, {"mobile_no"})
		self.assertEqual(column, "mobile_no")

	def test_a_mapping_without_any_phone_column_yields_no_column(self):
		cells = {"Tên": "Nguyễn Văn A", "Nguồn": "Facebook"}
		column = _resolve_column_name(cells, {"name": "Tên", "source": "Nguồn"}, set())
		self.assertIsNone(column)

	def test_the_classification_reads_the_phone_of_the_resolved_column(self):
		rows = [
			{"row_number": 2, "cells": {"Tên": "A", "Số điện thoại": "0911113001"}},
			{"row_number": 3, "cells": {"Tên": "B", "Số điện thoại": "0911113002"}},
		]
		column = _resolve_column_name(rows[0]["cells"], None, {"Số điện thoại"})
		result = _classify(rows, None, column)
		self.assertEqual(result["valid_rows"], 2)
		self.assertEqual(result["error_rows"], 0)
		self.assertEqual(result["preview"]["rows"][0]["canonical"], normalize_vn_phone("0911113001"))


class TestL04PoolQuery(P1TestCase):
	"""L-04 / L-14 — `pool_list` must build valid SQL and scope its `total`.

	Before the fix the filter fragment put a backtick around a **column name
	with an alias prefix**, which MariaDB parses as a table name. The endpoint
	therefore returned 500 for every actor. On top of that the `total` was not
	scoped, so it leaked the site-wide queue size.
	"""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.foreign_campaign = self.make_campaign(team_scope=self.team_b)
		for _ in range(3):
			lead = self.make_canonical_lead()
			self.make_work_item(self.campaign, lead, status="queued")
		for _ in range(2):
			lead = self.make_canonical_lead()
			self.make_work_item(self.foreign_campaign, lead, status="queued")

	def test_pool_list_does_not_raise_for_any_actor(self):
		for actor in (self.leader_a, self.staff_a, self.bod_user, self.admin_user):
			with self.subTest(actor=actor):
				payload = reporting.pool_list(actor, page_size=100)
				self.assertIn("items", payload)
				self.assertIn("total", payload)

	def _team_a_campaigns(self):
		"""The campaigns the leader of team A may read."""
		return frappe.db.sql(
			"""select name from `tabCSKH Campaign`
			   where team_scope is null or team_scope in (
			     select name from `tabCRM Sales Hierarchy`
			     where lft >= (select lft from `tabCRM Sales Hierarchy` where user = %s)
			       and rgt <= (select rgt from `tabCRM Sales Hierarchy` where user = %s))""",
			(self.leader_a, self.leader_a),
			pluck=True,
		)

	def test_the_total_is_scoped_like_the_items(self):
		payload = reporting.pool_list(self.leader_a, page_size=100)
		# The leader of team A sees the queue of their own campaigns only.
		expected = frappe.db.count(
			"CSKH Work Item",
			{"status": "queued", "campaign": ["in", self._team_a_campaigns()]},
		)
		self.assertEqual(payload["total"], expected)
		self.assertEqual(len(payload["items"]), min(expected, 100))
		campaigns = {item["campaign"] for item in payload["items"]}
		self.assertNotIn(self.foreign_campaign, campaigns)

	def test_the_total_never_exceeds_the_scoped_truth(self):
		payload = reporting.pool_list(self.leader_a, page_size=1)
		scoped = frappe.db.count(
			"CSKH Work Item",
			{"status": "queued", "campaign": ["in", self._team_a_campaigns()]},
		)
		self.assertEqual(payload["total"], scoped)
		self.assertEqual(len(payload["items"]), 1)

	def test_a_staff_member_reads_a_bounded_pool(self):
		"""A staff member has no hierarchy scope: the pool stays closed."""
		payload = reporting.pool_list(self.staff_a, page_size=100)
		self.assertEqual(payload["scope"], "self")
		self.assertEqual(payload["total"], len(payload["items"]))


class TestL05DashboardScope(P1TestCase):
	"""L-05 — the scope must be **applied**, not only reported.

	Before the fix the dashboard computed `scope` and returned it in the
	payload, but every counter was a site-wide `frappe.db.count(...)`. A
	leader therefore saw the numbers of the whole installation.
	"""

	def setUp(self):
		super().setUp()
		self.campaign_a = self.make_campaign(team_scope=self.team_a)
		self.campaign_b = self.make_campaign(team_scope=self.team_b)
		self.shared_campaign = self.make_campaign()
		# Team A: 5 work items. Team B: 3 work items.
		self.team_a_items = []
		for _ in range(5):
			lead = self.make_canonical_lead()
			item = self.make_work_item(self.campaign_a, lead, status="assigned", assigned_to=self.staff_a)
			self.team_a_items.append(item)
		self.team_b_items = []
		for _ in range(3):
			lead = self.make_canonical_lead()
			item = self.make_work_item(self.campaign_b, lead, status="assigned", assigned_to=self.staff_c)
			self.team_b_items.append(item)

	def _team_a_users(self):
		return frappe.db.sql(
			"""select `user` from `tabCRM Sales Hierarchy`
			   where lft >= (select lft from `tabCRM Sales Hierarchy` where `user` = %s)
			     and rgt <= (select rgt from `tabCRM Sales Hierarchy` where `user` = %s)""",
			(self.leader_a, self.leader_a),
			pluck=True,
		)

	def _team_a_campaigns(self):
		return frappe.db.sql(
			"""select name from `tabCSKH Campaign`
			   where team_scope is null or team_scope in (
			     select name from `tabCRM Sales Hierarchy`
			     where lft >= (select lft from `tabCRM Sales Hierarchy` where user = %s)
			       and rgt <= (select rgt from `tabCRM Sales Hierarchy` where user = %s))""",
			(self.leader_a, self.leader_a),
			pluck=True,
		)

	def test_the_leader_card_counts_the_team_only(self):
		payload = reporting.distribution_dashboard(self.leader_a)
		expected = frappe.db.count(
			"CSKH Work Item",
			{"status": "assigned", "assigned_to": ["in", self._team_a_users()]},
		)
		self.assertEqual(payload["scope"], "team")
		self.assertEqual(payload["cards"]["assigned_work_items"], expected)

	def test_the_leader_per_user_has_no_foreign_user(self):
		payload = reporting.distribution_dashboard(self.leader_a)
		users = [row["user"] for row in payload["per_user"]]
		self.assertIn(self.staff_a, users)
		self.assertNotIn(self.staff_c, users)
		self.assertNotIn(self.staff_outside, users)
		# Every reported user belongs to the subtree of the actor.
		self.assertTrue(set(users).issubset(set(self._team_a_users())))

	def test_the_per_user_breakdown_matches_the_card(self):
		payload = reporting.distribution_dashboard(self.leader_a)
		per_user_sum = sum(int(row["current_total"] or 0) for row in payload["per_user"])
		self.assertEqual(per_user_sum, payload["cards"]["assigned_work_items"])

	def test_a_staff_member_sees_only_its_own_work_items(self):
		payload = reporting.distribution_dashboard(self.staff_a)
		expected = frappe.db.count("CSKH Work Item", {"status": "assigned", "assigned_to": self.staff_a})
		self.assertEqual(payload["scope"], "self")
		self.assertEqual(payload["cards"]["assigned_work_items"], expected)
		users = [row["user"] for row in payload["per_user"]]
		self.assertEqual(users, [self.staff_a])

	def test_the_bod_sees_the_whole_installation(self):
		payload = reporting.distribution_dashboard(self.bod_user)
		expected = frappe.db.count("CSKH Work Item", {"status": "assigned"})
		self.assertEqual(payload["scope"], "all")
		self.assertEqual(payload["cards"]["assigned_work_items"], expected)

	def test_the_active_claims_counter_is_scoped(self):
		for item in self.team_a_items:
			frappe.get_doc(
				{
					"doctype": "CSKH Active Claim",
					"lead": frappe.db.get_value("CSKH Work Item", item, "lead"),
					"campaign": self.campaign_a,
					"work_item": item,
				}
			).insert(ignore_permissions=True)
		# Also a queued (unassigned) active claim in Team A's campaign and Team B's campaign (L-22).
		queued_lead_a = self.make_canonical_lead()
		queued_item_a = self.make_work_item(self.campaign_a, queued_lead_a, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": queued_lead_a,
				"campaign": self.campaign_a,
				"work_item": queued_item_a,
			}
		).insert(ignore_permissions=True)
		queued_lead_b = self.make_canonical_lead()
		queued_item_b = self.make_work_item(self.campaign_b, queued_lead_b, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": queued_lead_b,
				"campaign": self.campaign_b,
				"work_item": queued_item_b,
			}
		).insert(ignore_permissions=True)
		# An assigned claim of the other team.
		foreign_lead = frappe.db.get_value("CSKH Work Item", self.team_b_items[0], "lead")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": foreign_lead,
				"campaign": self.campaign_b,
				"work_item": self.team_b_items[0],
			}
		).insert(ignore_permissions=True)

		payload = reporting.distribution_dashboard(self.leader_a)
		team_a_users = self._team_a_users()
		team_a_campaigns = self._team_a_campaigns()
		expected = frappe.db.sql(
			"""select count(*) from `tabCSKH Active Claim` ac
			   inner join `tabCSKH Work Item` wi on wi.name = ac.work_item
			   where wi.status in ('queued', 'assigned')
			     and (wi.assigned_to in ({}) or (wi.assigned_to is null and wi.campaign in ({})))""".format(
				",".join(["%s"] * len(team_a_users)),
				",".join(["%s"] * len(team_a_campaigns)),
			),
			(*team_a_users, *team_a_campaigns),
		)[0][0]
		self.assertEqual(payload["cards"]["active_claims"], int(expected))
		bod = reporting.distribution_dashboard(self.bod_user)
		self.assertEqual(bod["cards"]["active_claims"], frappe.db.count("CSKH Active Claim"))

	def test_the_failed_counters_are_scoped(self):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "l05-failed.csv",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": b"phone\n",
			}
		)
		file_doc.insert(ignore_permissions=True)
		# The creator of a batch is the **owner** of the document: Frappe sets
		# it from the session, so the batch is inserted *as* the staff member
		# of team A.
		frappe.set_user(self.staff_a)
		batch = frappe.get_doc(
			{
				"doctype": "CSKH Import Batch",
				"source_file": file_doc.name,
				"checksum": self.unique_key("l05"),
				"idempotency_key": self.unique_key("l05-batch"),
				"status": "FAILED",
			}
		)
		batch.insert(ignore_permissions=True)
		frappe.set_user("Administrator")
		run = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": self.campaign_b,
				"idempotency_key": self.unique_key("l05-run"),
				"rule_version": 1,
				"selection_fingerprint": "l05",
				"status": "failed",
			}
		)
		run.insert(ignore_permissions=True)

		payload = reporting.distribution_dashboard(self.leader_a)
		# The counter is scoped to the owner, so it matches the batches of the
		# subtree of the leader — and never the batches of the whole site.
		expected = frappe.db.count(
			"CSKH Import Batch",
			{"status": "FAILED", "owner": ["in", self._team_a_users()]},
		)
		self.assertEqual(payload["cards"]["failed_batches"], expected)
		self.assertGreaterEqual(expected, 1)
		# The failed run belongs to the campaign of team B ⇒ not visible.
		self.assertEqual(payload["cards"]["failed_runs"], 0)

	def test_the_identity_count_is_scoped_not_global(self):
		"""The identity index is system-only; only an aggregate is reported."""
		payload = reporting.distribution_dashboard(self.leader_a)
		global_total = frappe.db.count("CSKH Identity Index")
		self.assertLessEqual(payload["identity_count"], global_total)

	def test_the_campaign_summary_counts_work_items_not_users(self):
		"""`campaign_summary.assigned` counts the work items, not the users."""
		payload = reporting.campaign_summary(self.leader_a)
		rows = {row["campaign"]: row for row in payload["campaigns"]}
		self.assertIn(self.campaign_a, rows)
		expected = frappe.db.count(
			"CSKH Work Item",
			{"campaign": self.campaign_a, "status": "assigned", "assigned_to": ["in", self._team_a_users()]},
		)
		self.assertEqual(rows[self.campaign_a]["assigned"], expected)
		# The counter is a work-item count, never a distinct-user count.
		self.assertNotEqual(rows[self.campaign_a]["assigned"], len(self._team_a_users()))

	def test_the_campaign_summary_hides_the_campaign_of_another_team(self):
		payload = reporting.campaign_summary(self.leader_a)
		campaigns = {row["campaign"] for row in payload["campaigns"]}
		self.assertNotIn(self.campaign_b, campaigns)
		self.assertIn(self.shared_campaign, campaigns)

	def test_a_campaign_filter_narrows_the_cards(self):
		payload = reporting.distribution_dashboard(self.leader_a, campaign=self.campaign_a)
		expected = frappe.db.count(
			"CSKH Work Item",
			{"campaign": self.campaign_a, "status": "assigned", "assigned_to": ["in", self._team_a_users()]},
		)
		self.assertEqual(payload["cards"]["assigned_work_items"], expected)
		filtered = reporting.distribution_dashboard(self.leader_a, campaign=self.campaign_b)
		self.assertEqual(filtered["cards"]["assigned_work_items"], 0)


class TestL06CampaignFilter(P1TestCase):
	"""L-06 — the campaign / date filter must be applied or refused.

	Before the fix the `campaign_filters` dict was passed as a **tuple** to
	`frappe.db.count`, which Frappe read as an `(operator, value)` pair, so
	every call with a campaign raised `ValueError`.
	"""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.other_campaign = self.make_campaign()
		leads = [self.make_canonical_lead() for _ in range(2)]
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": leads})
		)
		self.make_rule(self.campaign, self.staff_a, weight=10)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		distribution.execute(preview["preview_id"], actor=self.admin_user)

	def test_the_campaign_filter_does_not_raise(self):
		payload = reporting.distribution_dashboard(self.bod_user, campaign=self.campaign)
		self.assertEqual(payload["cards"]["assigned_work_items"], 2)

	def test_a_campaign_without_a_run_reports_zero_events(self):
		"""The whole-site history is never substituted for a campaign."""
		payload = reporting.distribution_dashboard(self.bod_user, campaign=self.other_campaign)
		self.assertEqual(payload["assignment_events"], 0)
		# The site does have events, so the zero is a real filter result.
		self.assertGreater(frappe.db.count("CSKH Assignment History"), 0)

	def test_the_date_filter_is_applied(self):
		payload = reporting.distribution_dashboard(
			self.bod_user, campaign=self.campaign, date_from="2000-01-01", date_to="2000-01-02"
		)
		self.assertEqual(payload["assignment_events"], 0)
		unfiltered = reporting.distribution_dashboard(self.bod_user, campaign=self.campaign)
		self.assertGreater(unfiltered["assignment_events"], 0)

	def test_the_date_filter_narrows_the_cards_and_not_only_the_events(self):
		"""A filter the contract accepts must not be accepted-and-ignored.

		Before the fix the `date_from` / `date_to` were only applied to the
		assignment events, so the cards kept the numbers of the whole period
		while the events were filtered: the dashboard showed two different
		periods at the same time (L-06).
		"""
		payload = reporting.distribution_dashboard(
			self.bod_user, campaign=self.campaign, date_from="2000-01-01", date_to="2000-01-02"
		)
		self.assertEqual(payload["cards"]["assigned_work_items"], 0)
		self.assertEqual(payload["cards"]["unique_customers"], 0)
		self.assertEqual(payload["cards"]["active_claims"], 0)
		self.assertEqual(payload["per_user"], [])
		self.assertEqual(payload["identity_count"], 0)

		unfiltered = reporting.distribution_dashboard(self.bod_user, campaign=self.campaign)
		self.assertGreater(unfiltered["cards"]["assigned_work_items"], 0)
		self.assertGreater(unfiltered["cards"]["unique_customers"], 0)

	def test_reconcile_with_a_campaign_does_not_raise(self):
		report = distribution.reconcile(self.campaign, actor=self.admin_user)
		self.assertEqual(report["campaign"], self.campaign)
		self.assertEqual(report["distinct_leads"], 2)


class TestL07LeaderRead(P1TestCase):
	"""L-07 — a leader reads the rules and the runs of their own campaign.

	Before the fix the query condition compared the **campaign name** with the
	**hierarchy node name**: the two namespaces never match, so the leader saw
	0 rules and 0 runs. The tests run as the **real leader**, never as
	`Administrator`, because `Administrator` bypasses the row-level
	permission and would hide the bug.
	"""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.foreign_campaign = self.make_campaign(team_scope=self.team_b)
		self.own_rule = self.make_rule(self.campaign, self.staff_a, weight=50)
		self.foreign_rule = self.make_rule(self.foreign_campaign, self.staff_c, weight=50)
		self.run = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": self.campaign,
				"idempotency_key": self.unique_key("l07-run"),
				"rule_version": 1,
				"selection_fingerprint": "l07",
				"status": "planned",
			}
		)
		self.run.insert(ignore_permissions=True)
		frappe.db.commit()

	def test_the_leader_reads_the_rules_of_their_campaign(self):
		frappe.set_user(self.leader_a)
		rules = frappe.get_list(
			"CSKH Assignment Rule", filters={"campaign": self.campaign}, fields=["name", "user"]
		)
		self.assertTrue(rules)
		self.assertEqual([row.user for row in rules], [self.staff_a])

	def test_the_leader_does_not_read_the_rules_of_another_team(self):
		frappe.set_user(self.leader_a)
		rules = frappe.get_list(
			"CSKH Assignment Rule", filters={"campaign": self.foreign_campaign}, fields=["name", "user"]
		)
		self.assertEqual(rules, [])

	def test_the_leader_reads_the_runs_of_their_campaign(self):
		frappe.set_user(self.leader_a)
		runs = frappe.get_list("CSKH Distribution Run", filters={"campaign": self.campaign}, fields=["name"])
		self.assertTrue(runs)

	def test_a_shared_campaign_hides_the_rule_of_another_team(self):
		"""A shared campaign is visible, but the assignee still has a team."""
		shared = self.make_campaign()
		self.make_rule(shared, self.staff_c, weight=50)
		frappe.db.commit()
		frappe.set_user(self.leader_a)
		rules = frappe.get_list("CSKH Assignment Rule", filters={"campaign": shared}, fields=["name", "user"])
		self.assertNotIn(self.staff_c, [row.user for row in rules])

	def test_the_admin_role_still_reads_every_rule(self):
		"""The CRM Admin is a business role with the full read scope."""
		frappe.set_user(self.admin_user)
		rules = frappe.get_list(
			"CSKH Assignment Rule", filters={"campaign": self.foreign_campaign}, fields=["name", "user"]
		)
		self.assertTrue(rules)

	def test_the_api_rules_endpoint_works_for_a_leader(self):
		from crm_cskh.api import campaigns as campaigns_api

		frappe.set_user(self.leader_a)
		payload = campaigns_api.rules(campaign=self.campaign)
		self.assertEqual(payload["campaign"], self.campaign)
		self.assertTrue(payload["eligible"])


class TestL08AuditTrail(P1TestCase):
	"""L-08 — the history must carry the `distribution_run`.

	Before the fix no history row had a `distribution_run`, so the audit link
	between a run and its events was lost (the information only lived in the
	free-text `reason` / `request_id`).
	"""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.lead = self.make_lead()
		self.work_item = self.make_work_item(self.campaign, self.lead, status="queued")
		self.run = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": self.campaign,
				"idempotency_key": self.unique_key("l08-run"),
				"rule_version": 1,
				"selection_fingerprint": "l08",
				"status": "planned",
			}
		)
		self.run.insert(ignore_permissions=True)

	def test_the_assignment_event_carries_the_run(self):
		ownership_assign(
			self.work_item,
			self.staff_a,
			actor=self.admin_user,
			request_id=self.unique_key("l08-assign"),
			distribution_run=self.run.name,
		)
		row = frappe.db.get_value(
			"CSKH Assignment History", {"work_item": self.work_item}, "distribution_run"
		)
		self.assertEqual(row, self.run.name)

	def test_a_distribution_run_writes_the_run_in_every_event(self):
		leads = [self.make_canonical_lead() for _ in range(2)]
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": leads})
		)
		self.make_rule(self.campaign, self.staff_a, weight=10)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		result = distribution.execute(preview["preview_id"], actor=self.admin_user)
		events = frappe.get_all(
			"CSKH Assignment History",
			filters={"action": "assign", "distribution_run": result["run_id"]},
			pluck="name",
		)
		self.assertEqual(len(events), 2)

	def test_a_manual_reassign_keeps_the_source_of_the_original_assignment(self):
		"""A manual transfer must not claim that a run caused it."""
		ownership_assign(
			self.work_item,
			self.staff_a,
			actor=self.admin_user,
			request_id=self.unique_key("l08-assign-2"),
			distribution_run=self.run.name,
		)
		from crm_cskh.services import ownership

		ownership.reassign(
			self.work_item,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="manual transfer",
			request_id=self.unique_key("l08-reassign"),
		)
		rows = frappe.get_all(
			"CSKH Assignment History",
			filters={"work_item": self.work_item},
			fields=["action", "distribution_run"],
			order_by="event_at asc",
		)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0]["action"], "assign")
		self.assertEqual(rows[0]["distribution_run"], self.run.name)
		self.assertEqual(rows[1]["action"], "reassign")
		# The manual event keeps the source of the original assignment.
		self.assertEqual(rows[1]["distribution_run"], self.run.name)

	def test_a_manual_reassign_of_a_manual_assignment_has_no_run(self):
		ownership_assign(
			self.work_item,
			self.staff_a,
			actor=self.admin_user,
			request_id=self.unique_key("l08-assign-3"),
		)
		from crm_cskh.services import ownership

		ownership.reassign(
			self.work_item,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="manual transfer",
			request_id=self.unique_key("l08-reassign-2"),
		)
		row = frappe.db.get_value(
			"CSKH Assignment History",
			{"work_item": self.work_item, "action": "reassign"},
			"distribution_run",
		)
		self.assertFalse(row)

	def test_a_manual_revoke_keeps_the_source_of_the_original_assignment(self):
		ownership_assign(
			self.work_item,
			self.staff_a,
			actor=self.admin_user,
			request_id=self.unique_key("l08-assign-4"),
			distribution_run=self.run.name,
		)
		from crm_cskh.services import ownership

		ownership.revoke(
			self.work_item,
			expected_owner=self.staff_a,
			actor=self.admin_user,
			reason="manual revoke",
			request_id=self.unique_key("l08-revoke"),
		)
		row = frappe.db.get_value(
			"CSKH Assignment History",
			{"work_item": self.work_item, "action": "revoke"},
			"distribution_run",
		)
		self.assertEqual(row, self.run.name)


class TestL09PlaceholderField(P1TestCase):
	"""L-09 — the `cskh_is_placeholder_name` field must exist.

	Before the fix the field did not exist at all: the import worker set it on
	every lead it created and the insert failed with
	`Unknown column 'cskh_is_placeholder_name'`.
	"""

	def test_the_custom_field_exists(self):
		self.assertTrue(
			frappe.db.exists("Custom Field", {"dt": "CRM Lead", "fieldname": "cskh_is_placeholder_name"})
		)

	def test_the_column_exists_in_the_database(self):
		columns = frappe.db.sql("show columns from `tabCRM Lead`", as_dict=True)
		self.assertIn("cskh_is_placeholder_name", [row.Field for row in columns])

	def test_the_patch_is_idempotent(self):
		"""Running the patch twice must not create a second field."""
		from crm_cskh.patches.v1_0 import p1_placeholder_name_field

		before = frappe.db.count("Custom Field", {"dt": "CRM Lead", "fieldname": "cskh_is_placeholder_name"})
		p1_placeholder_name_field.execute()
		p1_placeholder_name_field.execute()
		after = frappe.db.count("Custom Field", {"dt": "CRM Lead", "fieldname": "cskh_is_placeholder_name"})
		self.assertEqual(before, after)
		self.assertEqual(after, 1)

	def test_the_worker_marks_the_lead_it_creates(self):
		phone = self.unique_phone()
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "l09.csv",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": build_csv([(phone,)]),
			}
		)
		file_doc.insert(ignore_permissions=True)
		batch = create_preview(file_doc.name, actor=self.admin_user, idempotency_key=self.unique_key("l09"))
		with mock.patch("frappe.enqueue"):
			confirmed = import_service.confirm(batch["batch"], actor=self.admin_user)
		self.assertEqual(confirmed["status"], "QUEUED")
		import_worker.run_batch(batch["batch"])

		# The import stores the **canonical** number (E.164), not the raw cell
		# (P1 §5.4 #3): the lead is found through the identity index.
		canonical = normalize_vn_phone(phone)
		lead = frappe.db.get_value("CRM Lead", {"mobile_no": canonical}, "name")
		if not lead:
			lead = frappe.db.get_value(
				"CSKH Identity Index", {"normalized_phone": canonical}, "canonical_lead"
			)
		self.assertTrue(lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "cskh_is_placeholder_name"), 1)
		self.assertEqual(
			frappe.db.get_value("CRM Lead", lead, "first_name"), import_worker.PLACEHOLDER_FIRST_NAME
		)


class TestL10Capacity(P1TestCase):
	"""L-10 — `capacity_limit = 0` means **unlimited**, everywhere.

	Before the fix the semantics were not written down and the three places
	that read the limit (`eligibility`, `ownership`, `weighted_allocator`)
	could drift apart. A positive capacity must never be exceeded.
	"""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()

	def test_a_zero_capacity_is_unlimited_in_the_eligibility(self):
		self.make_rule(self.campaign, self.staff_a, weight=50, capacity=0)
		eligible, excluded = eligibility.eligible_assignees(self.campaign, None, 1)
		self.assertIn(self.staff_a, [rule["user"] for rule in eligible])
		self.assertNotIn(self.staff_a, [rule["user"] for rule in excluded])

	def test_a_zero_capacity_stays_eligible_after_many_assignments(self):
		self.make_rule(self.campaign, self.staff_a, weight=50, capacity=0)
		for _ in range(5):
			lead = self.make_canonical_lead()
			self.make_work_item(self.campaign, lead, status="assigned", assigned_to=self.staff_a)
		eligible, excluded = eligibility.eligible_assignees(self.campaign, None, 1)
		self.assertIn(self.staff_a, [rule["user"] for rule in eligible])
		self.assertEqual(excluded, [])

	def test_a_positive_capacity_is_enforced(self):
		self.make_rule(self.campaign, self.staff_a, weight=50, capacity=2)
		for _ in range(2):
			lead = self.make_canonical_lead()
			self.make_work_item(self.campaign, lead, status="assigned", assigned_to=self.staff_a)
		eligible, excluded = eligibility.eligible_assignees(self.campaign, None, 1)
		self.assertNotIn(self.staff_a, [rule["user"] for rule in eligible])
		self.assertEqual(excluded[0]["reason"], "capacity_full")

	def test_a_positive_capacity_is_never_exceeded_by_a_distribution(self):
		self.make_rule(self.campaign, self.staff_a, weight=100, capacity=2)
		leads = [self.make_canonical_lead() for _ in range(5)]
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": leads})
		)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		result = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertLessEqual(result["assigned"].get(self.staff_a, 0), 2)
		self.assertEqual(result["created"] + result["errors"], 5)

	def test_a_positive_capacity_is_never_exceeded_by_a_manual_reassign(self):
		from crm_cskh.services import ownership
		from crm_cskh.services.errors import Conflict

		self.make_rule(self.campaign, self.staff_b, weight=50, capacity=1)
		lead_a = self.make_lead()
		lead_b = self.make_lead()
		item_a = self.make_work_item(self.campaign, lead_a, status="queued")
		item_b = self.make_work_item(self.campaign, lead_b, status="queued")
		ownership.assign(item_a, self.staff_a, actor=self.admin_user, request_id=self.unique_key("l10-a"))
		ownership.assign(item_b, self.staff_b, actor=self.admin_user, request_id=self.unique_key("l10-b"))
		with self.assertRaises(Conflict) as ctx:
			ownership.reassign(
				item_a,
				expected_owner=self.staff_a,
				target_user=self.staff_b,
				actor=self.admin_user,
				reason="over capacity",
				request_id=self.unique_key("l10-reassign"),
			)
		self.assertEqual(ctx.exception.code, "CAPACITY_EXCEEDED")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", item_a, "assigned_to"), self.staff_a)

	def test_the_dashboard_reports_an_unlimited_capacity_as_null(self):
		self.make_rule(self.campaign, self.staff_a, weight=50, capacity=0)
		lead = self.make_canonical_lead()
		self.make_work_item(self.campaign, lead, status="assigned", assigned_to=self.staff_a)
		payload = reporting.distribution_dashboard(self.bod_user)
		row = next(item for item in payload["per_user"] if item["user"] == self.staff_a)
		self.assertIsNone(row["capacity"])
		self.assertIsNone(row["remaining_capacity"])


class TestL13Sanitization(P1TestCase):
	"""L-13 — the import cells and the error artifact must be injection-safe.

	Before the fix there was **no** sanitization at all: a formula cell and an
	HTML cell were stored verbatim. The sanitizer must neutralise a formula
	without breaking a valid `+84` phone number.
	"""

	def test_a_formula_is_neutralized(self):
		for raw in ("=1+1", "=cmd|'/c calc'!A1", "@SUM(A1)"):
			with self.subTest(raw=raw):
				self.assertTrue(sanitize_cell(raw).startswith("'"))

	def test_html_is_removed(self):
		self.assertNotIn("<script>", sanitize_cell("<script>alert(1)</script>"))

	def test_a_valid_plus84_phone_is_not_broken(self):
		"""A `+84` phone is a number, not a formula."""
		self.assertEqual(sanitize_cell("+84912345678", allow_leading_sign=True), "+84912345678")

	def test_the_preview_never_stores_a_formula(self):
		"""The preview only keeps the canonical phone, never a raw cell."""
		rows = [{"row_number": 2, "cells": {"Số điện thoại": "=cmd|'/c calc'!A1"}}]
		result = _classify(rows, None, "Số điện thoại")
		self.assertEqual(result["valid_rows"], 0)
		self.assertEqual(result["error_rows"], 1)

	def test_the_error_artifact_is_private_and_sanitized(self):
		from crm_cskh.services.import_service import write_error_artifact

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "l13.csv",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": b"phone\n",
			}
		)
		file_doc.insert(ignore_permissions=True)
		batch = frappe.get_doc(
			{
				"doctype": "CSKH Import Batch",
				"source_file": file_doc.name,
				"checksum": self.unique_key("l13"),
				"idempotency_key": self.unique_key("l13-batch"),
				"status": "DRY_RUN",
			}
		)
		batch.insert(ignore_permissions=True)
		artifact = write_error_artifact(
			batch.name,
			[
				{"row_number": 2, "reason": "=1+1"},
				{"row_number": 3, "reason": "<script>alert(1)</script>"},
				{"row_number": 4, "reason": "0912345678"},
			],
		)
		doc = frappe.get_doc("File", artifact)
		self.assertEqual(doc.is_private, 1)
		content = doc.get_content()
		if isinstance(content, bytes):
			content = content.decode("utf-8")
		self.assertNotIn("<script>", content)
		self.assertIn("'=1+1", content)
		# A formula may never start a line of the CSV.
		for line in content.splitlines()[1:]:
			self.assertFalse(line.startswith(("=", "+", "-", "@")))

	def test_the_stored_error_of_a_failed_batch_carries_no_phone_number(self):
		"""The error message is redacted: no PII reaches the batch."""
		redacted = import_worker._redact("boom 0912345678 and +84912345678")
		self.assertNotIn("0912345678", redacted)
		self.assertNotIn("84912345678", redacted)
		self.assertIn("***", redacted)


class TestL15NoPermissionBypass(P1TestCase):
	"""L-15 — no `frappe.get_all` on data that needs a row-level permission.

	`frappe.get_all` does **not** apply `permission_query_conditions`, so it
	bypasses the scope. The rule / campaign / history reads must use
	`frappe.get_list`.
	"""

	def _source_of(self, relative_path):
		path = os.path.join(os.path.dirname(__file__), "..", relative_path)
		with open(path, encoding="utf-8") as handle:
			return handle.read()

	def test_the_reporting_service_does_not_use_get_all(self):
		self.assertNotIn("frappe.get_all(", self._source_of("services/reporting.py"))

	def test_the_eligibility_service_does_not_use_get_all(self):
		self.assertNotIn("frappe.get_all(", self._source_of("services/eligibility.py"))

	def test_the_campaigns_api_does_not_use_get_all(self):
		self.assertNotIn("frappe.get_all(", self._source_of("api/campaigns.py"))

	def test_the_ownership_api_does_not_use_get_all(self):
		self.assertNotIn("frappe.get_all(", self._source_of("api/ownership.py"))

	def test_the_history_endpoint_respects_the_row_level_permission(self):
		"""The history of a work item of another team stays unreadable."""
		from crm_cskh.api import ownership as ownership_api
		from crm_cskh.services.errors import Forbidden

		foreign_campaign = self.make_campaign(team_scope=self.team_b)
		lead = self.make_lead(owner=self.staff_c)
		item = self.make_work_item(foreign_campaign, lead, status="queued")
		ownership_assign(item, self.staff_c, actor=self.admin_user, request_id=self.unique_key("l15-hist"))

		frappe.set_user(self.leader_a)
		# The endpoint answers with the stable `FORBIDDEN` code (contract §2),
		# never with a raw Frappe exception.
		with self.assertRaises(Forbidden) as ctx:
			ownership_api.history(work_item=item)
		self.assertEqual(ctx.exception.code, "FORBIDDEN")
		self.assertEqual(ctx.exception.http_status, 403)

	def test_the_leader_only_sees_the_history_of_their_team(self):
		from crm_cskh.api import ownership as ownership_api

		campaign = self.make_campaign(team_scope=self.team_a)
		lead = self.make_lead(owner=self.staff_a)
		item = self.make_work_item(campaign, lead, status="queued")
		ownership_assign(item, self.staff_a, actor=self.admin_user, request_id=self.unique_key("l15-hist-2"))

		frappe.set_user(self.leader_a)
		payload = ownership_api.history(work_item=item)
		self.assertEqual(payload["total"], 1)
		self.assertEqual(payload["items"][0]["to_user"], self.staff_a)


class TestImportIdempotency(P1TestCase):
	"""The import must be idempotent and must never touch an existing lead.

	These invariants back the T1-03 / T1-04 / T1-06 rows of the baseline.
	"""

	def _upload(self, content, file_name="import.csv"):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": content,
			}
		)
		file_doc.insert(ignore_permissions=True)
		return file_doc.name

	def test_the_same_file_and_key_returns_the_same_batch(self):
		phone = self.unique_phone()
		file_name = self._upload(build_csv([(phone,)]))
		key = self.unique_key("idem")
		first = create_preview(file_name, actor=self.admin_user, idempotency_key=key)
		second = create_preview(file_name, actor=self.admin_user, idempotency_key=key)
		self.assertEqual(first["batch"], second["batch"])
		self.assertEqual(frappe.db.count("CSKH Import Batch", {"idempotency_key": key}), 1)

	def test_the_same_file_in_another_mode_creates_a_second_batch(self):
		"""T1-04: the same file in another mode must be a distinct batch."""
		phone = self.unique_phone()
		file_name = self._upload(build_csv([(phone,)]))
		first = create_preview(
			file_name, actor=self.admin_user, idempotency_key=self.unique_key("mode-a"), import_mode="a"
		)
		second = create_preview(
			file_name, actor=self.admin_user, idempotency_key=self.unique_key("mode-b"), import_mode="b"
		)
		self.assertNotEqual(first["batch"], second["batch"])

	def test_a_duplicate_import_does_not_change_the_owner_or_the_status(self):
		phone = self.unique_phone()
		lead = self.make_lead(phone=phone, owner=self.staff_a)
		frappe.db.set_value("CRM Lead", lead, "status", "Converted")
		frappe.db.set_value("CRM Lead", lead, "lead_owner", self.staff_a)

		file_name = self._upload(build_csv([(phone,)]))
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("dup-owner"))
		with mock.patch("frappe.enqueue"):
			import_service.confirm(batch["batch"], actor=self.admin_user)
		import_worker.run_batch(batch["batch"])

		self.assertEqual(frappe.db.count("CRM Lead", {"mobile_no": phone}), 1)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "lead_owner"), self.staff_a)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), "Converted")

	def test_a_duplicate_import_does_not_distribute_the_lead(self):
		"""The import never creates a work item on its own."""
		phone = self.unique_phone()
		self.make_lead(phone=phone)
		file_name = self._upload(build_csv([(phone,)]))
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("dup-dist"))
		with mock.patch("frappe.enqueue"):
			import_service.confirm(batch["batch"], actor=self.admin_user)
		import_worker.run_batch(batch["batch"])
		lead = frappe.db.get_value("CRM Lead", {"mobile_no": phone}, "name")
		self.assertEqual(frappe.db.count("CSKH Work Item", {"lead": lead}), 0)

	def test_the_canonical_phone_is_shared_by_the_zero_and_84_forms(self):
		"""`0…`, `84…` and `+84…` are the same customer."""
		phone = self.unique_phone()
		local = f"0{phone[1:]}"
		country = f"84{phone[1:]}"
		canonical = f"+84{phone[1:]}"
		file_name = self._upload(build_csv([(local,), (country,), (canonical,)]))
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("forms"))
		self.assertEqual(batch["totals"]["valid_rows"], 1)
		self.assertEqual(batch["totals"]["duplicate_rows"], 2)
		self.assertEqual(batch["totals"]["error_rows"], 0)

	def test_an_empty_or_numeric_or_scientific_cell_is_an_error_not_a_lead(self):
		leads_before = frappe.db.count("CRM Lead")
		file_name = self._upload(
			build_csv(
				[
					("",),
					("123",),
					("9.12346E+08",),
					("0911111001",),
				]
			)
		)
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("bad-cells"))
		self.assertGreaterEqual(batch["totals"]["error_rows"], 2)
		self.assertEqual(batch["totals"]["valid_rows"], 1)
		self.assertEqual(frappe.db.count("CRM Lead"), leads_before)


class _DummyResponse:
	def __init__(self, status_code, payload=None, mimetype="application/json"):
		self.status_code = status_code
		self.mimetype = mimetype
		self.data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""


class _DummyRequest:
	def __init__(self, path="", args=None):
		self.path = path
		self.args = args or {}


class TestL16ErrorEnvelopeAndFileOracle(P1TestCase):
	"""L-16 — stable `code`/`p1_code` on P1Error HTTP envelope while preserving P0 File-oracle."""

	def tearDown(self):
		clear_p1_error_context()
		super().tearDown()

	def test_every_p1_error_subclass_emits_stable_code_and_clears_context(self):
		from crm_cskh.services import errors

		cases = [
			(errors.InvalidRequest("bad param"), 400, "INVALID_REQUEST"),
			(errors.Forbidden("denied"), 403, "FORBIDDEN"),
			(errors.NotFound("missing"), 404, "NOT_FOUND"),
			(errors.Conflict("already owned", code="ALREADY_ASSIGNED"), 409, "ALREADY_ASSIGNED"),
			(errors.RuleChanged("rule changed", details={"expected": 1, "current": 2}), 409, "RULE_CHANGED"),
			(errors.PreviewExpired("expired"), 409, "PREVIEW_EXPIRED"),
			(errors.CapacityExceeded("cap"), 409, "CAPACITY_EXCEEDED"),
			(errors.UnsupportedFormat("bad ext"), 400, "UNSUPPORTED_FORMAT"),
			(errors.FileTooLarge("big"), 413, "FILE_TOO_LARGE"),
			(errors.TooManyRows("many"), 413, "TOO_MANY_ROWS"),
		]
		for factory_exc, expected_status, expected_code in cases:
			with self.subTest(code=expected_code):
				clear_p1_error_context()
				# Constructing `P1Error` records `p1_error_code` on `frappe.local`.
				exc = type(factory_exc)(
					str(factory_exc),
					code=factory_exc.code,
					details=factory_exc.details,
				)
				self.assertEqual(exc.http_status_code, expected_status)
				resp = _DummyResponse(
					expected_status,
					{
						"exc_type": type(exc).__name__,
						"_error_message": str(exc),
					},
				)
				req = _DummyRequest(path="/api/method/crm_cskh.api.distribution.execute")
				out = normalize_error_response(resp, req)
				out = normalize_p1_error_response(out, req)
				raw = out.data.decode("utf-8") if isinstance(out.data, (bytes, bytearray)) else out.data
				parsed = json.loads(raw)
				self.assertEqual(parsed["code"], expected_code)
				self.assertEqual(parsed["p1_code"], expected_code)
				self.assertEqual(parsed["exc_type"], expected_code)
				if factory_exc.details:
					self.assertEqual(parsed["details"], factory_exc.details)
				# Context is cleared after normalization so a subsequent request cannot inherit it.
				self.assertIsNone(getattr(frappe.local, "p1_error_code", None))

	def test_chained_normalizers_keep_file_oracle_byte_identical_even_after_p1_error(self):
		from crm_cskh.services import errors

		# Pollute `frappe.local` with a P1Error right beforehand to prove File routes ignore it.
		errors.Forbidden("polluted before file route")
		real_denied = _DummyResponse(
			403,
			{
				"exc_type": "PermissionError",
				"exception": "frappe.exceptions.PermissionError: ('read', 'File', 'secret99')",
				"_error_message": "No permission for File secret99",
			},
		)
		random_missing = _DummyResponse(
			404,
			{
				"exc_type": "DoesNotExistError",
				"_server_messages": json.dumps([json.dumps({"message": "File missing00 not found"})]),
			},
		)
		for path, args in (
			("/api/resource/File/secret99", {}),
			("/api/method/frappe.client.get", {"doctype": "File"}),
		):
			with self.subTest(path=path):
				req = _DummyRequest(path=path, args=args)
				r_real = normalize_p1_error_response(
					normalize_error_response(
						_DummyResponse(403, json.loads(real_denied.data.decode("utf-8"))), req
					),
					req,
				)
				r_rand = normalize_p1_error_response(
					normalize_error_response(
						_DummyResponse(404, json.loads(random_missing.data.decode("utf-8"))), req
					),
					req,
				)
				self.assertEqual(r_real.status_code, 404)
				self.assertEqual(r_rand.status_code, 404)
				body_real = (
					r_real.data.decode("utf-8")
					if isinstance(r_real.data, (bytes, bytearray))
					else r_real.data
				)
				body_rand = (
					r_rand.data.decode("utf-8")
					if isinstance(r_rand.data, (bytes, bytearray))
					else r_rand.data
				)
				self.assertEqual(body_real, body_rand)
				self.assertEqual(body_real, _canonical_not_found_body())


class TestL17CrmAdminReportingScope(P1TestCase):
	"""L-17 — `CSKH CRM Admin` gets `scope='system'` without `System Manager`."""

	def setUp(self):
		super().setUp()
		self.campaign_a = self.make_campaign(team_scope=self.team_a)
		self.campaign_b = self.make_campaign(team_scope=self.team_b)
		self.lead_assigned = self.make_canonical_lead()
		self.lead_queued = self.make_canonical_lead()
		self.wi_assigned = self.make_work_item(
			self.campaign_a, self.lead_assigned, status="assigned", assigned_to=self.staff_a
		)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": self.lead_assigned,
				"campaign": self.campaign_a,
				"work_item": self.wi_assigned,
			}
		).insert(ignore_permissions=True)
		self.wi_queued = self.make_work_item(self.campaign_b, self.lead_queued, status="queued")

	def test_crm_admin_has_no_system_manager_yet_reads_full_system_scope_matching_db(self):
		from crm_cskh.api import customers as customers_api
		from crm_cskh.api import dashboard as dashboard_api

		self.assertNotIn("System Manager", frappe.get_roles(self.admin_user))
		self.assertIn("CSKH CRM Admin", frappe.get_roles(self.admin_user))

		frappe.set_user(self.admin_user)
		dash = dashboard_api.distribution()
		self.assertEqual(dash["scope"], "system")
		cards = dash["cards"]
		self.assertGreater(cards["assigned_work_items"], 0)
		self.assertGreater(cards["unassigned_work_items"], 0)
		self.assertGreater(cards["unique_customers"], 0)
		self.assertEqual(
			cards["assigned_work_items"], frappe.db.count("CSKH Work Item", {"status": "assigned"})
		)
		self.assertEqual(
			cards["unassigned_work_items"],
			frappe.db.count("CSKH Work Item", {"status": "queued"}),
		)
		self.assertEqual(cards["active_claims"], frappe.db.count("CSKH Active Claim"))
		expected_unique = int(frappe.db.sql("select count(distinct lead) from `tabCSKH Work Item`")[0][0])
		self.assertEqual(cards["unique_customers"], expected_unique)

		camp_summary = dashboard_api.campaigns()
		camp_ids = {row["campaign"] for row in camp_summary["campaigns"]}
		self.assertIn(self.campaign_a, camp_ids)
		self.assertIn(self.campaign_b, camp_ids)

		pool = customers_api.pool_list(page_size=100)
		self.assertEqual(pool["scope"], "system")
		self.assertGreater(pool["total"], 0)
		self.assertEqual(
			pool["total"],
			frappe.db.count("CSKH Work Item", {"status": "queued", "assigned_to": ["is", "not set"]}),
		)


class TestL18FirstConfirmToWorker(P1TestCase):
	"""L-18 — first-time `imports.confirm()` reaches `QUEUED` and completes in worker cleanly."""

	def test_first_confirm_reaches_queued_without_db_set_value_or_second_confirm(self):
		from crm_cskh.api import imports as imports_api

		phones = [self.unique_phone(), self.unique_phone(), self.unique_phone()]
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "l18-first-confirm.csv",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": build_csv([(p,) for p in phones]),
			}
		)
		file_doc.insert(ignore_permissions=True)

		frappe.set_user(self.admin_user)
		preview = imports_api.create_preview(
			file_name=file_doc.name,
			idempotency_key=self.unique_key("l18-first"),
		)
		batch_name = preview["batch"]
		self.assertEqual(preview["status"], "DRY_RUN")

		with mock.patch("frappe.enqueue") as mocked_enqueue:
			confirmed = imports_api.confirm(batch=batch_name)
			# Double-submit on an already QUEUED batch does not enqueue twice.
			double_confirmed = imports_api.confirm(batch=batch_name)
			self.assertEqual(mocked_enqueue.call_count, 1)

		self.assertEqual(confirmed["status"], "QUEUED")
		self.assertEqual(double_confirmed["status"], "QUEUED")
		self.assertEqual(frappe.db.get_value("CSKH Import Batch", batch_name, "status"), "QUEUED")

		# Worker runs directly on the batch produced by the SINGLE confirm call.
		import_worker.run_batch(batch_name)

		status_after = imports_api.get_status(batch=batch_name)
		self.assertEqual(status_after["status"], "COMPLETED")
		self.assertEqual(status_after["created_rows"], 3)
		self.assertEqual(status_after["totals"]["error_rows"], 0)
		for p in phones:
			self.assertEqual(
				frappe.db.count("CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p)}),
				1,
			)


class TestL19L20LargeImportAndErrorArtifact(P1TestCase):
	"""L-19 / L-20 — `>200` row import loses zero data, writes full error artifact, never double-counts."""

	def test_260_row_import_commits_all_valid_rows_and_preserves_exact_error_counts_across_resume(self):
		from crm_cskh.api import imports as imports_api

		valid_phones = [self.unique_phone() for _ in range(254)]
		invalid_positions = {10, 88, 199, 205, 240, 261}  # CSV row_number 2..261 (3 <= 200, 3 > 200)
		rows = []
		v_idx = 0
		for row_number in range(2, 262):
			if row_number in invalid_positions:
				rows.append((f"invalid-cell-{row_number}",))
			else:
				rows.append((valid_phones[v_idx],))
				v_idx += 1

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "l19-l20-260rows.csv",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": build_csv(rows),
			}
		)
		file_doc.insert(ignore_permissions=True)

		frappe.set_user(self.admin_user)
		preview = imports_api.create_preview(
			file_name=file_doc.name,
			idempotency_key=self.unique_key("l19-l20-260"),
		)
		batch_name = preview["batch"]
		self.assertTrue(preview["preview_truncated"])
		self.assertEqual(len(preview["preview"]), 200)
		self.assertEqual(preview["totals"]["total_rows"], 260)
		self.assertEqual(preview["totals"]["valid_rows"], 254)
		self.assertEqual(preview["totals"]["new_rows"], 254)
		self.assertEqual(preview["totals"]["error_rows"], 6)

		with mock.patch("frappe.enqueue"):
			confirmed = imports_api.confirm(batch=batch_name)
		self.assertEqual(confirmed["status"], "QUEUED")

		import_worker.run_batch(batch_name)
		status = imports_api.get_status(batch=batch_name)
		self.assertEqual(status["status"], "COMPLETED_WITH_ERRORS")
		self.assertEqual(status["created_rows"], 254)
		self.assertEqual(status["totals"]["valid_rows"], 254)
		self.assertEqual(status["totals"]["new_rows"], 254)
		self.assertEqual(status["totals"]["error_rows"], 6)
		self.assertTrue(status["error_artifact"])

		# Double-confirm on COMPLETED_WITH_ERRORS where all 254 new_rows were created is idempotent.
		with mock.patch("frappe.enqueue") as mocked_reconfirm:
			reconfirmed = imports_api.confirm(batch=batch_name)
			mocked_reconfirm.assert_not_called()
		self.assertEqual(reconfirmed["status"], "COMPLETED_WITH_ERRORS")

		artifact_doc = frappe.get_doc("File", status["error_artifact"])
		self.assertEqual(artifact_doc.is_private, 1)
		raw_csv = artifact_doc.get_content()
		if isinstance(raw_csv, bytes):
			raw_csv = raw_csv.decode("utf-8")
		artifact_rows = list(csv.DictReader(io.StringIO(raw_csv)))
		self.assertEqual(len(artifact_rows), 6)
		self.assertEqual(
			{int(r["row_number"]) for r in artifact_rows},
			invalid_positions,
		)


class TestL21RevokeBooleanParsing(P1TestCase):
	"""L-21 — strict boolean parsing on `ownership.revoke` (`return_to_pool`) and `campaigns.save_rule` (`enabled`)."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		frappe.set_user(self.admin_user)

	def _assigned_item_with_claim(self, tag):
		lead = self.make_canonical_lead()
		wi = self.make_work_item(self.campaign, lead, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead,
				"campaign": self.campaign,
				"work_item": wi,
			}
		).insert(ignore_permissions=True)
		ownership_assign(
			wi,
			self.staff_a,
			actor=self.admin_user,
			request_id=self.unique_key(f"l21-assign-{tag}"),
		)
		return lead, wi

	def test_revoke_parses_all_false_and_true_variants_and_syncs_db_artifacts(self):
		from crm_cskh.api import ownership as ownership_api

		false_variants = [False, "false", 0, "0"]
		true_variants = [True, "true", 1, "1"]

		for idx, raw_val in enumerate(false_variants):
			with self.subTest(kind="false", raw=raw_val):
				lead, wi = self._assigned_item_with_claim(f"f{idx}")
				res = ownership_api.revoke(
					work_item=wi,
					expected_owner=self.staff_a,
					reason=f"L-21 false variant {raw_val!r}",
					request_id=self.unique_key(f"l21-rev-f{idx}"),
					return_to_pool=raw_val,
				)
				self.assertEqual(res["status"], "revoked")
				self.assertIsNone(res["owner"])
				self._assert_revoked_db_truth(lead, wi, expected_status="revoked")

		for idx, raw_val in enumerate(true_variants):
			with self.subTest(kind="true", raw=raw_val):
				lead, wi = self._assigned_item_with_claim(f"t{idx}")
				res = ownership_api.revoke(
					work_item=wi,
					expected_owner=self.staff_a,
					reason=f"L-21 true variant {raw_val!r}",
					request_id=self.unique_key(f"l21-rev-t{idx}"),
					return_to_pool=raw_val,
				)
				self.assertEqual(res["status"], "queued")
				self.assertIsNone(res["owner"])
				self._assert_revoked_db_truth(lead, wi, expected_status="queued")

	def _assert_revoked_db_truth(self, lead, wi, expected_status):
		wi_row = frappe.db.get_value("CSKH Work Item", wi, ["status", "assigned_to"], as_dict=True)
		self.assertEqual(wi_row.status, expected_status)
		self.assertIsNone(wi_row.assigned_to)
		self.assertIsNone(frappe.db.get_value("CRM Lead", lead, "lead_owner"))
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead}), 0)
		self.assertEqual(
			frappe.db.count("ToDo", {"reference_type": "CRM Lead", "reference_name": lead, "status": "Open"}),
			0,
		)
		self.assertGreaterEqual(
			frappe.db.count(
				"ToDo", {"reference_type": "CRM Lead", "reference_name": lead, "status": "Cancelled"}
			),
			1,
		)
		self.assertEqual(
			frappe.db.count("DocShare", {"share_doctype": "CRM Lead", "share_name": lead}),
			0,
		)
		events = frappe.get_all(
			"CSKH Assignment History",
			filters={"work_item": wi},
			fields=["action", "from_user", "to_user", "ownership_end"],
			order_by="event_at asc",
		)
		self.assertEqual([e.action for e in events], ["assign", "revoke"])
		self.assertIsNotNone(events[0].ownership_end)
		self.assertEqual(events[1].from_user, self.staff_a)
		self.assertIsNone(events[1].to_user)

	def test_invalid_boolean_strings_fail_closed_with_invalid_request(self):
		from crm_cskh.api import campaigns as campaigns_api
		from crm_cskh.api import ownership as ownership_api
		from crm_cskh.services.errors import InvalidRequest

		_, wi = self._assigned_item_with_claim("invalid")
		for bad in ("maybe", "yes", "2"):
			with self.subTest(bad=bad):
				with self.assertRaises(InvalidRequest) as ctx:
					ownership_api.revoke(
						work_item=wi,
						expected_owner=self.staff_a,
						reason="invalid bool",
						request_id=self.unique_key(f"l21-bad-{bad}"),
						return_to_pool=bad,
					)
				self.assertEqual(ctx.exception.code, "INVALID_REQUEST")
				self.assertEqual(ctx.exception.http_status, 400)

		# Also verify `campaigns.save_rule` boolean parsing on `enabled`.
		saved_false = campaigns_api.save_rule(
			campaign=self.campaign, user=self.staff_a, weight=50, enabled="false"
		)
		self.assertFalse(saved_false["enabled"])
		saved_zero = campaigns_api.save_rule(
			campaign=self.campaign, user=self.staff_b, weight=30, enabled="0"
		)
		self.assertFalse(saved_zero["enabled"])
		saved_true = campaigns_api.save_rule(
			campaign=self.campaign, user=self.staff_c, weight=20, enabled="true"
		)
		self.assertTrue(saved_true["enabled"])
		with self.assertRaises(InvalidRequest):
			campaigns_api.save_rule(
				campaign=self.campaign, user=self.staff_a, weight=50, enabled="not-a-bool"
			)


class TestL22LeaderUnassignedAndUniqueCustomers(P1TestCase):
	"""L-22 — Leader counts unassigned `revoked`/`closed`/`active_claims` in readable campaigns, and `unique_customers` is unified across roles."""

	def setUp(self):
		super().setUp()
		self.camp_a = self.make_campaign(team_scope=self.team_a)
		self.camp_b = self.make_campaign(team_scope=self.team_b)
		self.camp_shared = self.make_campaign()

		# Shared customer appearing in both `camp_a` (assigned to staff_a) and `camp_shared` (revoked)
		# to prove `COUNT(DISTINCT wi.lead)` does not double-count multi-campaign customers.
		self.multi_lead = self.make_canonical_lead()
		wi_multi_a = self.make_work_item(
			self.camp_a, self.multi_lead, status="assigned", assigned_to=self.staff_a
		)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": self.multi_lead,
				"campaign": self.camp_a,
				"work_item": wi_multi_a,
			}
		).insert(ignore_permissions=True)
		self.make_work_item(self.camp_shared, self.multi_lead, status="revoked")

		# Unassigned `queued` work item WITH Active Claim in `camp_a` (must be counted by Leader A, NOT Leader B).
		self.lead_qa = self.make_canonical_lead()
		wi_qa = self.make_work_item(self.camp_a, self.lead_qa, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": self.lead_qa,
				"campaign": self.camp_a,
				"work_item": wi_qa,
			}
		).insert(ignore_permissions=True)

		# Unassigned `revoked` and `closed` work items in `camp_a` (`assigned_to IS NULL`).
		self.lead_rev_a = self.make_canonical_lead()
		self.make_work_item(self.camp_a, self.lead_rev_a, status="revoked")
		self.lead_cls_a = self.make_canonical_lead()
		self.make_work_item(self.camp_a, self.lead_cls_a, status="closed")

		# Foreign Team B items (`assigned`, `queued`+claim, `revoked`, `closed`) — MUST NOT leak into Leader A.
		self.lead_asg_b = self.make_canonical_lead()
		wi_asg_b = self.make_work_item(
			self.camp_b, self.lead_asg_b, status="assigned", assigned_to=self.staff_c
		)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": self.lead_asg_b,
				"campaign": self.camp_b,
				"work_item": wi_asg_b,
			}
		).insert(ignore_permissions=True)
		self.lead_qb = self.make_canonical_lead()
		wi_qb = self.make_work_item(self.camp_b, self.lead_qb, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": self.lead_qb,
				"campaign": self.camp_b,
				"work_item": wi_qb,
			}
		).insert(ignore_permissions=True)
		self.make_work_item(self.camp_b, self.make_canonical_lead(), status="revoked")
		self.make_work_item(self.camp_b, self.make_canonical_lead(), status="closed")

	def _leader_users_and_campaigns(self, leader):
		users = frappe.db.sql(
			"""select `user` from `tabCRM Sales Hierarchy`
			   where lft >= (select lft from `tabCRM Sales Hierarchy` where `user` = %s)
			     and rgt <= (select rgt from `tabCRM Sales Hierarchy` where `user` = %s)""",
			(leader, leader),
			pluck=True,
		)
		campaigns = frappe.db.sql(
			"""select name from `tabCSKH Campaign`
			   where team_scope is null or team_scope in (
			     select name from `tabCRM Sales Hierarchy`
			     where lft >= (select lft from `tabCRM Sales Hierarchy` where `user` = %s)
			       and rgt <= (select rgt from `tabCRM Sales Hierarchy` where `user` = %s))""",
			(leader, leader),
			pluck=True,
		)
		return users, campaigns

	def test_leader_counts_unassigned_revoked_closed_and_claims_without_cross_team_leak(self):
		dash_leader_a = reporting.distribution_dashboard(self.leader_a)
		cards_a = dash_leader_a["cards"]
		users_a, camps_a = self._leader_users_and_campaigns(self.leader_a)

		u_ph = ",".join(["%s"] * len(users_a))
		c_ph = ",".join(["%s"] * len(camps_a))
		scope_pred = f"(wi.assigned_to in ({u_ph}) or (wi.assigned_to is null and wi.campaign in ({c_ph})))"
		params = (*users_a, *camps_a)

		exp_revoked = int(
			frappe.db.sql(
				f"select count(*) from `tabCSKH Work Item` wi where wi.status = 'revoked' and {scope_pred}",
				params,
			)[0][0]
		)
		exp_closed = int(
			frappe.db.sql(
				f"select count(*) from `tabCSKH Work Item` wi where wi.status = 'closed' and {scope_pred}",
				params,
			)[0][0]
		)
		exp_claims = int(
			frappe.db.sql(
				f"""select count(*) from `tabCSKH Active Claim` ac
				    inner join `tabCSKH Work Item` wi on wi.name = ac.work_item
				    where wi.status in ('queued', 'assigned') and {scope_pred}""",
				params,
			)[0][0]
		)
		exp_unique = int(
			frappe.db.sql(
				f"select count(distinct wi.lead) from `tabCSKH Work Item` wi where {scope_pred}",
				params,
			)[0][0]
		)

		self.assertGreaterEqual(exp_revoked, 2)  # `camp_a` revoked + `camp_shared` revoked
		self.assertGreaterEqual(exp_closed, 1)  # `camp_a` closed
		self.assertGreaterEqual(exp_claims, 2)  # `camp_a` assigned + `camp_a` queued
		self.assertEqual(cards_a["revoked_work_items"], exp_revoked)
		self.assertEqual(cards_a["closed_work_items"], exp_closed)
		self.assertEqual(cards_a["active_claims"], exp_claims)
		self.assertEqual(cards_a["unique_customers"], exp_unique)

		# Narrowing to `camp_a` verifies `multi_lead`, `lead_qa`, `lead_rev_a`, `lead_cls_a` = 4 unique customers!
		dash_camp_a = reporting.distribution_dashboard(self.leader_a, campaign=self.camp_a)
		self.assertEqual(dash_camp_a["cards"]["assigned_work_items"], 1)
		self.assertEqual(dash_camp_a["cards"]["unassigned_work_items"], 1)
		self.assertEqual(dash_camp_a["cards"]["revoked_work_items"], 1)
		self.assertEqual(dash_camp_a["cards"]["closed_work_items"], 1)
		self.assertEqual(dash_camp_a["cards"]["active_claims"], 2)
		self.assertEqual(dash_camp_a["cards"]["unique_customers"], 4)

		# Foreign campaign `camp_b` returns 0 across every work-item card for Leader A.
		dash_foreign = reporting.distribution_dashboard(self.leader_a, campaign=self.camp_b)
		for key in (
			"unique_customers",
			"assigned_work_items",
			"unassigned_work_items",
			"revoked_work_items",
			"closed_work_items",
			"active_claims",
		):
			self.assertEqual(dash_foreign["cards"][key], 0, f"foreign leak in {key}")

	def test_unique_customers_definition_is_unified_across_admin_bod_leader_and_staff(self):
		dash_admin = reporting.distribution_dashboard(self.admin_user)
		dash_bod = reporting.distribution_dashboard(self.bod_user)
		dash_staff = reporting.distribution_dashboard(self.staff_a)

		expected_global_unique = int(
			frappe.db.sql("select count(distinct lead) from `tabCSKH Work Item`")[0][0]
		)
		# Site also has canonical leads without any Work Item (e.g. an extra lead created now) ->
		# `unique_customers` MUST equal `COUNT(DISTINCT wi.lead)` for both Admin and BOD, NOT `Identity Index`.
		extra_unworked_lead = self.make_canonical_lead()
		self.assertTrue(extra_unworked_lead)

		dash_admin_after = reporting.distribution_dashboard(self.admin_user)
		dash_bod_after = reporting.distribution_dashboard(self.bod_user)
		self.assertEqual(dash_admin["cards"]["unique_customers"], expected_global_unique)
		self.assertEqual(dash_bod["cards"]["unique_customers"], expected_global_unique)
		self.assertEqual(dash_admin_after["cards"]["unique_customers"], expected_global_unique)
		self.assertEqual(dash_bod_after["cards"]["unique_customers"], expected_global_unique)

		expected_staff_unique = int(
			frappe.db.sql(
				"select count(distinct lead) from `tabCSKH Work Item` where assigned_to = %s",
				(self.staff_a,),
			)[0][0]
		)
		self.assertEqual(dash_staff["cards"]["unique_customers"], expected_staff_unique)


if __name__ == "__main__":
	unittest.main()
