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
| L-23 | `reporting.customer_pool` / `customers.customer_pool` admitted historical `revoked`/`closed` Team A leads now active/owned in Team B | `TestL23BackendCustomerPoolCrossTeamIsolation` |
| L-25 | Import ignored `name`/`source` mapping, lacked `Import Batch ↔ Lead` relation for pool filtering without Work Items / across reused batches | `TestL25ImportMappingAndBatchLink` |
| L-26 | `ownership.history`, `customers.pool_list`, `customers.customer_pool`, `customers.my_list` raised HTTP 500 `ValueError` on invalid `page`/`page_size`/`limit` | `TestL26BackendPaginationAndLimitValidation` |
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


class TestL25ImportMappingAndBatchLink(P1TestCase):
	"""L-25 — apply `name`/`source` mapping on new leads, preserve canonical leads, and persist `Import Batch ↔ Lead`."""

	def _upload(self, content, file_name="l25.csv"):
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": content,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _confirm_and_run(self, batch_name):
		with mock.patch("frappe.enqueue"):
			resp = import_service.confirm(batch_name, actor=self.admin_user)
		self.assertEqual(resp["status"], "QUEUED")
		import_worker.run_batch(batch_name)
		return frappe.get_doc("CSKH Import Batch", batch_name)

	def test_mapped_name_and_source_apply_to_new_leads_and_placeholder_only_when_missing(self):
		p_named = self.unique_phone()
		p_empty = self.unique_phone()
		p_html_blank = self.unique_phone()
		p_phone_as_name = self.unique_phone()

		csv_bytes = build_csv(
			[
				(p_named, "Nguyễn Thị Mai", "Zalo Official"),
				(p_empty, "", "Facebook"),
				(p_html_blank, "  <b>   </b>  ", "TikTok Shop"),
				(p_phone_as_name, p_phone_as_name, "Website"),
			],
			header=("Cột ĐT", "Cột Họ Tên", "Cột Kênh"),
		)
		file_name = self._upload(csv_bytes, file_name="l25-mapped.csv")
		mapping = {"phone": "Cột ĐT", "name": "Cột Họ Tên", "source": "Cột Kênh"}
		preview = create_preview(
			file_name,
			mapping=mapping,
			actor=self.admin_user,
			idempotency_key=self.unique_key("l25-map-1"),
		)
		self.assertEqual(preview["resolved_mapping"], mapping)
		batch_doc = self._confirm_and_run(preview["batch"])
		self.assertEqual(batch_doc.status, "COMPLETED")
		self.assertEqual(int(batch_doc.created_rows), 4)
		self.assertEqual(int(batch_doc.reused_rows), 0)
		self.assertEqual(int(batch_doc.error_rows), 0)

		lead_named = frappe.db.get_value(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p_named)}, "canonical_lead"
		)
		named_doc = frappe.db.get_value(
			"CRM Lead",
			lead_named,
			["first_name", "lead_name", "cskh_is_placeholder_name", "source"],
			as_dict=True,
		)
		self.assertEqual(named_doc.first_name, "Nguyễn Thị Mai")
		self.assertEqual(named_doc.lead_name, "Nguyễn Thị Mai")
		self.assertEqual(int(named_doc.cskh_is_placeholder_name or 0), 0)
		self.assertEqual(named_doc.source, "Zalo Official")

		for raw_p, exp_src in (
			(p_empty, "Facebook"),
			(p_html_blank, "TikTok Shop"),
			(p_phone_as_name, "Website"),
		):
			lead_id = frappe.db.get_value(
				"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(raw_p)}, "canonical_lead"
			)
			row_doc = frappe.db.get_value(
				"CRM Lead",
				lead_id,
				["first_name", "lead_name", "cskh_is_placeholder_name", "source"],
				as_dict=True,
			)
			self.assertEqual(row_doc.first_name, import_worker.PLACEHOLDER_FIRST_NAME)
			self.assertEqual(row_doc.lead_name, import_worker.PLACEHOLDER_FIRST_NAME)
			self.assertEqual(int(row_doc.cskh_is_placeholder_name or 0), 1)
			self.assertEqual(row_doc.source, exp_src)

		# Sparse XLSX where the first data row has `None` in name & source, second row has values.
		openpyxl = __import__("openpyxl")
		wb = openpyxl.Workbook()
		sheet = wb.active
		sheet.append(["Số điện thoại", "Họ và tên", "Nguồn"])
		p_xlsx_1 = self.unique_phone()
		p_xlsx_2 = self.unique_phone()
		sheet.append([p_xlsx_1, None, None])
		sheet.append([p_xlsx_2, "Lê Văn Hùng", "Hội thảo VIP"])
		buf = io.BytesIO()
		wb.save(buf)
		xlsx_file = self._upload(buf.getvalue(), file_name="l25-sparse.xlsx")
		xlsx_prev = create_preview(
			xlsx_file, actor=self.admin_user, idempotency_key=self.unique_key("l25-xlsx")
		)
		self._confirm_and_run(xlsx_prev["batch"])

		l_x1 = frappe.db.get_value(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p_xlsx_1)}, "canonical_lead"
		)
		l_x2 = frappe.db.get_value(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p_xlsx_2)}, "canonical_lead"
		)
		self.assertEqual(int(frappe.db.get_value("CRM Lead", l_x1, "cskh_is_placeholder_name") or 0), 1)
		x2_doc = frappe.db.get_value(
			"CRM Lead", l_x2, ["first_name", "cskh_is_placeholder_name", "source"], as_dict=True
		)
		self.assertEqual(x2_doc.first_name, "Lê Văn Hùng")
		self.assertEqual(int(x2_doc.cskh_is_placeholder_name or 0), 0)
		self.assertEqual(x2_doc.source, "Hội thảo VIP")

	def test_formula_and_html_sanitization_and_mapping_conflict_guard(self):
		from crm_cskh.services.errors import Conflict

		p_formula = self.unique_phone()
		p_xss = self.unique_phone()
		csv_bytes = build_csv(
			[
				(p_formula, "=cmd|'/c calc'!A0", "@SUM(A1:A5)"),
				(p_xss, "<script>alert(1)</script>Trần Quốc Bảo", "<img src=x onerror=1>Landing Page"),
			],
			header=("PhoneCol", "NameCol", "SourceCol"),
		)
		file_name = self._upload(csv_bytes, file_name="l25-sanitize.csv")
		idem_key = self.unique_key("l25-san")
		mapping = {"phone": "PhoneCol", "name": "NameCol", "source": "SourceCol"}
		preview = create_preview(file_name, mapping=mapping, actor=self.admin_user, idempotency_key=idem_key)

		# Reusing the same idempotency_key with a different mapping MUST raise 409 CONFLICT (P1 §6.4).
		with self.assertRaises(Conflict) as ctx:
			create_preview(
				file_name,
				mapping={"phone": "PhoneCol", "name": "SourceCol"},
				actor=self.admin_user,
				idempotency_key=idem_key,
			)
		self.assertEqual(ctx.exception.code, "CONFLICT")

		batch_doc = self._confirm_and_run(preview["batch"])
		self.assertEqual(batch_doc.status, "COMPLETED")
		self.assertEqual(int(batch_doc.created_rows), 2)

		lead_f = frappe.db.get_value(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p_formula)}, "canonical_lead"
		)
		f_doc = frappe.db.get_value(
			"CRM Lead", lead_f, ["first_name", "cskh_is_placeholder_name", "source"], as_dict=True
		)
		self.assertEqual(f_doc.first_name, "'=cmd|'/c calc'!A0")
		self.assertFalse(f_doc.first_name.startswith("="))
		self.assertEqual(int(f_doc.cskh_is_placeholder_name or 0), 0)
		self.assertEqual(f_doc.source, "'@SUM(A1:A5)")
		self.assertFalse(f_doc.source.startswith("@"))

		lead_x = frappe.db.get_value(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p_xss)}, "canonical_lead"
		)
		x_doc = frappe.db.get_value(
			"CRM Lead", lead_x, ["first_name", "cskh_is_placeholder_name", "source"], as_dict=True
		)
		self.assertNotIn("<script>", x_doc.first_name)
		self.assertIn("Trần Quốc Bảo", x_doc.first_name)
		self.assertEqual(int(x_doc.cskh_is_placeholder_name or 0), 0)
		self.assertEqual(x_doc.source, "Landing Page")

	def test_existing_canonical_lead_is_never_overwritten_and_links_across_multiple_batches(self):
		from crm_cskh.api import customers as customers_api
		from crm_cskh.services.errors import Forbidden

		p_shared = self.unique_phone()
		p_only_b1 = self.unique_phone()
		p_only_b2 = self.unique_phone()
		p_existing_placeholder = self.unique_phone()

		# Batch 1 creates `lead_shared` (named), `lead_only_b1` (named), and `lead_ph` (placeholder).
		f_b1 = self._upload(
			build_csv(
				[
					(p_shared, "Phạm Minh Tuấn", "Facebook"),
					(p_only_b1, "Vũ Thu Hà", "Zalo L25"),
					(p_existing_placeholder, "", ""),
				],
				header=("SDT", "HoTen", "Nguon"),
			),
			file_name="l25-b1.csv",
		)
		map_cols = {"phone": "SDT", "name": "HoTen", "source": "Nguon"}
		prev_b1 = create_preview(
			f_b1, mapping=map_cols, actor=self.admin_user, idempotency_key=self.unique_key("l25-b1")
		)
		b1_doc = self._confirm_and_run(prev_b1["batch"])
		self.assertEqual(int(b1_doc.created_rows), 3)
		self.assertEqual(int(b1_doc.reused_rows), 0)

		lead_shared = frappe.db.get_value(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p_shared)}, "canonical_lead"
		)
		lead_only_b1 = frappe.db.get_value(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p_only_b1)}, "canonical_lead"
		)
		lead_ph = frappe.db.get_value(
			"CSKH Identity Index",
			{"normalized_phone": normalize_vn_phone(p_existing_placeholder)},
			"canonical_lead",
		)

		# Attach business state (`lead_owner`, `status`, `job_title`) to `lead_shared` and `lead_ph`.
		frappe.db.set_value(
			"CRM Lead",
			lead_shared,
			{"lead_owner": self.staff_a, "status": "Converted", "job_title": "Giám đốc mua hàng"},
		)
		frappe.db.set_value("CRM Lead", lead_ph, {"lead_owner": self.staff_b})

		# Batch 2 re-imports `p_shared` and `p_existing_placeholder` with conflicting name/source + 1 new phone.
		f_b2 = self._upload(
			build_csv(
				[
					(p_shared, "TÊN GHI ĐÈ SAI", "NGUỒN GHI ĐÈ SAI"),
					(p_existing_placeholder, "TÊN ĐÈ PLACEHOLDER", "NGUỒN ĐÈ PLACEHOLDER"),
					(p_only_b2, "Đặng Hoàng Nam", "TikTok L25"),
				],
				header=("SDT", "HoTen", "Nguon"),
			),
			file_name="l25-b2.csv",
		)
		prev_b2 = create_preview(
			f_b2, mapping=map_cols, actor=self.admin_user, idempotency_key=self.unique_key("l25-b2")
		)
		b2_doc = self._confirm_and_run(prev_b2["batch"])
		self.assertEqual(b2_doc.status, "COMPLETED")
		self.assertEqual(int(b2_doc.created_rows), 1)
		self.assertEqual(int(b2_doc.reused_rows), 2)
		self.assertEqual(int(b2_doc.skipped_rows), 0)
		self.assertEqual(int(b2_doc.error_rows), 0)

		lead_only_b2 = frappe.db.get_value(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p_only_b2)}, "canonical_lead"
		)

		# Business fields on existing canonical leads MUST NOT be overwritten!
		shared_after = frappe.db.get_value(
			"CRM Lead",
			lead_shared,
			[
				"first_name",
				"lead_name",
				"cskh_is_placeholder_name",
				"source",
				"lead_owner",
				"status",
				"job_title",
			],
			as_dict=True,
		)
		self.assertEqual(shared_after.first_name, "Phạm Minh Tuấn")
		self.assertEqual(shared_after.lead_name, "Phạm Minh Tuấn")
		self.assertEqual(int(shared_after.cskh_is_placeholder_name or 0), 0)
		self.assertEqual(shared_after.source, "Facebook")
		self.assertEqual(shared_after.lead_owner, self.staff_a)
		self.assertEqual(shared_after.status, "Converted")
		self.assertEqual(shared_after.job_title, "Giám đốc mua hàng")

		ph_after = frappe.db.get_value(
			"CRM Lead",
			lead_ph,
			["first_name", "cskh_is_placeholder_name", "source", "lead_owner"],
			as_dict=True,
		)
		self.assertEqual(ph_after.first_name, import_worker.PLACEHOLDER_FIRST_NAME)
		self.assertEqual(int(ph_after.cskh_is_placeholder_name or 0), 1)
		self.assertFalse(ph_after.source)
		self.assertEqual(ph_after.lead_owner, self.staff_b)

		# Both `Import Batch ↔ Lead` links exist (`created` in B1, `reused` in B2) with ZERO Work Items!
		self.assertEqual(
			frappe.db.get_value(
				"CSKH Import Batch Lead",
				{"import_batch": prev_b1["batch"], "lead": lead_shared},
				"outcome",
			),
			"created",
		)
		self.assertEqual(
			frappe.db.get_value(
				"CSKH Import Batch Lead",
				{"import_batch": prev_b2["batch"], "lead": lead_shared},
				"outcome",
			),
			"reused",
		)
		self.assertEqual(frappe.db.count("CSKH Work Item", {"lead": lead_only_b1}), 0)
		self.assertEqual(frappe.db.count("CSKH Work Item", {"lead": lead_only_b2}), 0)

		# Pool filtering by `batch` works WITHOUT Work Items and ACROSS reused batches!
		frappe.set_user(self.admin_user)
		pool_b1 = customers_api.customer_pool(batch=prev_b1["batch"], page_size=50)
		b1_leads = {item["lead"]: item for item in pool_b1["items"]}
		self.assertEqual(set(b1_leads), {lead_shared, lead_only_b1, lead_ph})
		self.assertEqual(pool_b1["total"], 3)
		self.assertIsNone(b1_leads[lead_only_b1]["work_item"])
		self.assertEqual(b1_leads[lead_only_b1]["state"], "eligible")
		self.assertEqual(b1_leads[lead_only_b1]["display_name"], "Vũ Thu Hà")
		self.assertEqual(b1_leads[lead_only_b1]["source"], "Zalo L25")

		pool_b2 = customers_api.customer_pool(batch=prev_b2["batch"], page_size=50)
		b2_leads = {item["lead"]: item for item in pool_b2["items"]}
		self.assertEqual(set(b2_leads), {lead_shared, lead_ph, lead_only_b2})
		self.assertEqual(pool_b2["total"], 3)
		self.assertIsNone(b2_leads[lead_only_b2]["work_item"])
		self.assertEqual(b2_leads[lead_only_b2]["state"], "eligible")

		# BOD sees counts without PII; Staff is forbidden; Leader sees only team-scoped items.
		frappe.set_user(self.bod_user)
		bod_b1 = customers_api.customer_pool(batch=prev_b1["batch"], page_size=50)
		self.assertEqual(bod_b1["total"], 3)
		for row in bod_b1["items"]:
			self.assertIsNone(row["phone"])
			self.assertEqual(row["display_name"], "")
		with self.assertRaises(Forbidden):
			customers_api.customer_pool(batch=prev_b1["batch"], search="09")

		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden):
			customers_api.customer_pool(batch=prev_b1["batch"])

		frappe.set_user(self.leader_b)
		self.assertEqual(customers_api.customer_pool(batch=prev_b2["batch"])["total"], 0)

		frappe.set_user(self.leader_a)
		leader_b2_before = customers_api.customer_pool(batch=prev_b2["batch"], page_size=50)
		self.assertEqual({item["lead"] for item in leader_b2_before["items"]}, {lead_shared, lead_ph})
		self.assertNotIn(lead_only_b2, {item["lead"] for item in leader_b2_before["items"]})
		camp_a = self.make_campaign(team_scope=self.team_a)
		self.make_work_item(camp_a, lead_only_b2, status="queued")
		leader_b2_after = customers_api.customer_pool(batch=prev_b2["batch"], page_size=50)
		self.assertEqual(
			{item["lead"] for item in leader_b2_after["items"]},
			{lead_shared, lead_ph, lead_only_b2},
		)

	def test_retry_and_concurrent_multi_batch_race_keep_non_duplicate_links_and_counters(self):
		from crm_cskh.tests.test_p1_concurrency import _run_in_thread

		# Part A: Retry after mid-batch failure on mapped real-name rows (`cskh_is_placeholder_name = 0`).
		retry_phones = [self.unique_phone() for _ in range(6)]
		retry_canonicals = [normalize_vn_phone(p) for p in retry_phones]
		retry_rows = [
			(retry_phones[0], "Khách Retry 1", "Facebook"),
			(retry_phones[1], "Khách Retry 2", "Zalo Retry"),
			(retry_phones[2], "", "Website"),
			(retry_phones[3], "Khách Retry 4", "TikTok Retry"),
			(retry_phones[4], "Khách Retry 5", "Facebook"),
			(retry_phones[5], "", ""),
			(retry_phones[0], "Duplicate In File", "Other"),
		]
		f_retry = self._upload(
			build_csv(retry_rows, header=("Phone", "Name", "Source")),
			file_name="l25-retry.csv",
		)
		prev_retry = create_preview(
			f_retry,
			mapping={"phone": "Phone", "name": "Name", "source": "Source"},
			actor=self.admin_user,
			idempotency_key=self.unique_key("l25-retry"),
		)
		batch_retry = prev_retry["batch"]
		with mock.patch("frappe.enqueue"):
			import_service.confirm(batch_retry, actor=self.admin_user)

		real_ensure = import_worker._ensure_lead
		failed_once = {"done": False}
		fail_target = retry_canonicals[1]  # Row 3 in Chunk 1 (`CHUNK_SIZE = 2`)

		def fail_once(canonical_phone, row):
			if canonical_phone == fail_target and not failed_once["done"]:
				failed_once["done"] = True
				raise RuntimeError("transient L-25 row failure")
			return real_ensure(canonical_phone, row)

		with mock.patch.object(import_worker, "CHUNK_SIZE", 2):
			with mock.patch.object(import_worker, "_ensure_lead", side_effect=fail_once):
				import_worker.run_batch(batch_retry)

		first_doc = frappe.get_doc("CSKH Import Batch", batch_retry)
		self.assertEqual(first_doc.status, "COMPLETED_WITH_ERRORS")
		self.assertEqual(int(first_doc.created_rows), 5)
		self.assertEqual(int(first_doc.reused_rows), 0)
		self.assertEqual(int(first_doc.error_rows), 1)
		self.assertEqual(int(first_doc.progress), 2)

		# Re-confirm and retry, then call `run_batch` an extra time to verify post-completion no-op.
		with mock.patch("frappe.enqueue"):
			import_service.confirm(batch_retry, actor=self.admin_user)
		with mock.patch.object(import_worker, "CHUNK_SIZE", 2):
			import_worker.run_batch(batch_retry)
			import_worker.run_batch(batch_retry)

		final_retry = frappe.get_doc("CSKH Import Batch", batch_retry)
		self.assertEqual(final_retry.status, "COMPLETED")
		self.assertEqual(int(final_retry.created_rows), 6)
		self.assertEqual(int(final_retry.reused_rows), 0)
		self.assertEqual(int(final_retry.error_rows), 0)
		self.assertEqual(int(final_retry.skipped_rows), 0)
		self.assertEqual(frappe.db.count("CSKH Import Batch Lead", {"import_batch": batch_retry}), 6)
		self.assertEqual(
			frappe.db.count("CSKH Import Batch Lead", {"import_batch": batch_retry, "outcome": "created"}),
			6,
		)

		# Part B: Real MariaDB multi-thread race across 2 batches sharing 3 phones + 1 distinct phone each.
		shared_race = [self.unique_phone() for _ in range(3)]
		only_r1 = self.unique_phone()
		only_r2 = self.unique_phone()

		f_r1 = self._upload(
			build_csv(
				[
					(shared_race[0], "Winner R1-0", "Source R1"),
					(shared_race[1], "Winner R1-1", "Source R1"),
					(shared_race[2], "Winner R1-2", "Source R1"),
					(only_r1, "Unique R1", "Source R1"),
				],
				header=("Phone", "Name", "Source"),
			),
			file_name="l25-race-1.csv",
		)
		f_r2 = self._upload(
			build_csv(
				[
					(shared_race[0], "Contender R2-0", "Source R2"),
					(shared_race[1], "Contender R2-1", "Source R2"),
					(shared_race[2], "Contender R2-2", "Source R2"),
					(only_r2, "Unique R2", "Source R2"),
				],
				header=("Phone", "Name", "Source"),
			),
			file_name="l25-race-2.csv",
		)
		prev_r1 = create_preview(
			f_r1,
			mapping={"phone": "Phone", "name": "Name", "source": "Source"},
			actor=self.admin_user,
			idempotency_key=self.unique_key("l25-r1"),
		)
		prev_r2 = create_preview(
			f_r2,
			mapping={"phone": "Phone", "name": "Name", "source": "Source"},
			actor=self.admin_user,
			idempotency_key=self.unique_key("l25-r2"),
		)
		with mock.patch("frappe.enqueue"):
			import_service.confirm(prev_r1["batch"], actor=self.admin_user)
			import_service.confirm(prev_r2["batch"], actor=self.admin_user)
		frappe.db.commit()

		results = {}
		t1 = _run_in_thread(lambda: import_worker.run_batch(prev_r1["batch"]), results, 0)
		t2 = _run_in_thread(lambda: import_worker.run_batch(prev_r2["batch"]), results, 1)
		t1.join()
		t2.join()
		frappe.db.commit()

		errors = [val for val in results.values() if "error" in val]
		self.assertEqual(errors, [], f"parallel multi-batch import race must not error: {results}")

		r1_doc = frappe.get_doc("CSKH Import Batch", prev_r1["batch"])
		r2_doc = frappe.get_doc("CSKH Import Batch", prev_r2["batch"])
		self.assertEqual(r1_doc.status, "COMPLETED")
		self.assertEqual(r2_doc.status, "COMPLETED")
		self.assertEqual(int(r1_doc.error_rows), 0)
		self.assertEqual(int(r2_doc.error_rows), 0)

		# 5 distinct canonical phones total across R1 (4) and R2 (4):
		# sum(created_rows) == 5 and sum(reused_rows) == 3 (zero double-counting!).
		self.assertEqual(int(r1_doc.created_rows) + int(r2_doc.created_rows), 5)
		self.assertEqual(int(r1_doc.reused_rows) + int(r2_doc.reused_rows), 3)
		self.assertEqual(frappe.db.count("CSKH Import Batch Lead", {"import_batch": prev_r1["batch"]}), 4)
		self.assertEqual(frappe.db.count("CSKH Import Batch Lead", {"import_batch": prev_r2["batch"]}), 4)

		for p in [*shared_race, only_r1, only_r2]:
			c = normalize_vn_phone(p)
			self.assertEqual(frappe.db.count("CSKH Identity Index", {"normalized_phone": c}), 1)
			self.assertEqual(frappe.db.count("CRM Lead", {"mobile_no": c}), 1)

		# Every shared lead is linked to BOTH R1 and R2 (once as `created`, once as `reused`).
		for idx, p in enumerate(shared_race):
			lead_id = frappe.db.get_value(
				"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(p)}, "canonical_lead"
			)
			outcomes = sorted(
				frappe.get_all(
					"CSKH Import Batch Lead",
					filters={"import_batch": ["in", [prev_r1["batch"], prev_r2["batch"]]], "lead": lead_id},
					pluck="outcome",
				)
			)
			self.assertEqual(outcomes, ["created", "reused"])
			lead_info = frappe.db.get_value("CRM Lead", lead_id, ["first_name", "source"], as_dict=True)
			# Winner's coherent (name, source) pair survived without partial overwrite.
			self.assertIn(
				(lead_info.first_name, lead_info.source),
				{
					(f"Winner R1-{idx}", "Source R1"),
					(f"Contender R2-{idx}", "Source R2"),
				},
			)


class TestL23BackendCustomerPoolCrossTeamIsolation(P1TestCase):
	"""L-23 — `reporting.customer_pool` and `customers.customer_pool` must never
	let historical `revoked`/`closed` Work Items in Team A leak a lead currently
	active, queued in another team's private campaign, claimed, or owned outside
	Team A (matching `pool_ui.list` policy 100%).
	"""

	def test_revoked_or_closed_team_a_history_never_leaks_foreign_active_lead_in_reporting_or_api(self):
		from crm_cskh.api import customers as customers_api
		from crm_cskh.services import ownership as ownership_service
		from crm_cskh.services.errors import Forbidden

		frappe.set_user("Administrator")
		campaign_a = self.make_campaign(title="Chiến dịch Đội A L23", team_scope=self.team_a)
		campaign_b = self.make_campaign(title="Chiến dịch Riêng Đội B L23", team_scope=self.team_b)
		campaign_shared = self.make_campaign(title="Chiến dịch Chung L23", team_scope=None)

		# Baseline Team A positive leads: 1 assigned (`staff_a`), 1 queued (`campaign_a`), 1 revoked-only (`campaign_a`).
		lead_active_a = self.make_canonical_lead()
		wi_active_a = self.make_work_item(campaign_a, lead_active_a, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_active_a,
				"campaign": campaign_a,
				"work_item": wi_active_a,
			}
		).insert(ignore_permissions=True)
		ownership_service.assign(
			wi_active_a,
			self.staff_a,
			actor=self.admin_user,
			reason="L-23 baseline Team A",
			request_id=self.unique_key("l23-base-a"),
		)

		lead_queued_a = self.make_canonical_lead()
		wi_queued_a = self.make_work_item(campaign_a, lead_queued_a, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_queued_a,
				"campaign": campaign_a,
				"work_item": wi_queued_a,
			}
		).insert(ignore_permissions=True)

		lead_revoked_stay_a = self.make_canonical_lead()
		stay_a_phone = frappe.db.get_value("CRM Lead", lead_revoked_stay_a, "mobile_no")
		self.make_work_item(campaign_a, lead_revoked_stay_a, status="revoked")

		frappe.set_user(self.leader_a)
		baseline_a = customers_api.customer_pool(page=1, page_size=100)
		baseline_total_a = baseline_a["total"]
		baseline_wi_a = baseline_a["work_items"]

		# 1. T1C exact reproduction: assigned in Team A -> permanently revoked (`return_to_pool=False`)
		#    -> assigned to `staff_c` in Team B's private campaign `campaign_b`, plus linked to an Import Batch.
		frappe.set_user("Administrator")
		src_b = self.unique_key("L23SrcTeamB")
		name_b = f"Khách Bí Mật Đội B {self.unique_key('nm')}"
		lead_revoked_to_b = self.make_canonical_lead()
		raw_phone_b = frappe.db.get_value("CRM Lead", lead_revoked_to_b, "mobile_no")
		alt_phone_b = "0" + raw_phone_b[3:] if raw_phone_b.startswith("+84") else raw_phone_b
		frappe.db.set_value(
			"CRM Lead",
			lead_revoked_to_b,
			{"first_name": name_b, "lead_name": name_b, "source": src_b},
			update_modified=False,
		)
		l23_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"l23-{self.unique_key('csv')}.csv",
				"is_private": 1,
				"content": f"phone\n{raw_phone_b}\n".encode(),
			}
		).insert(ignore_permissions=True)
		batch_doc = frappe.get_doc(
			{
				"doctype": "CSKH Import Batch",
				"source_file": l23_file.name,
				"checksum": self.unique_key("l23-sha"),
				"status": "COMPLETED",
				"idempotency_key": self.unique_key("l23-batch"),
				"total_rows": 1,
				"valid_rows": 1,
				"created_rows": 1,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "CSKH Import Batch Lead",
				"import_batch": batch_doc.name,
				"lead": lead_revoked_to_b,
				"outcome": "created",
			}
		).insert(ignore_permissions=True)

		wi_a_old = self.make_work_item(campaign_a, lead_revoked_to_b, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_revoked_to_b,
				"campaign": campaign_a,
				"work_item": wi_a_old,
			}
		).insert(ignore_permissions=True)
		ownership_service.assign(
			wi_a_old,
			self.staff_a,
			actor=self.admin_user,
			reason="Initial Team A",
			request_id=self.unique_key("l23-asg-a"),
		)
		ownership_service.revoke(
			wi_a_old,
			expected_owner=self.staff_a,
			actor=self.admin_user,
			reason="Permanent revoke from Team A",
			request_id=self.unique_key("l23-rev-a"),
			return_to_pool=False,
		)
		wi_b_now = self.make_work_item(campaign_b, lead_revoked_to_b, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_revoked_to_b,
				"campaign": campaign_b,
				"work_item": wi_b_now,
			}
		).insert(ignore_permissions=True)
		ownership_service.assign(
			wi_b_now,
			self.staff_c,
			actor=self.admin_user,
			reason="Assign to Team B",
			request_id=self.unique_key("l23-asg-b"),
		)

		# 2. Lead closed in Team A, now queued with Active Claim in Team B's private campaign.
		lead_closed_to_b_queued = self.make_canonical_lead()
		self.make_work_item(campaign_a, lead_closed_to_b_queued, status="closed")
		wi_b_queued = self.make_work_item(campaign_b, lead_closed_to_b_queued, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_closed_to_b_queued,
				"campaign": campaign_b,
				"work_item": wi_b_queued,
			}
		).insert(ignore_permissions=True)

		# 3. Lead revoked in Team A, now assigned to `staff_c` in a shared campaign (`team_scope=None`).
		lead_revoked_to_shared_b = self.make_canonical_lead()
		self.make_work_item(campaign_a, lead_revoked_to_shared_b, status="revoked")
		wi_shared_b = self.make_work_item(
			campaign_shared, lead_revoked_to_shared_b, status="assigned", assigned_to=self.staff_c
		)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_revoked_to_shared_b,
				"campaign": campaign_shared,
				"work_item": wi_shared_b,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"CRM Lead", lead_revoked_to_shared_b, "lead_owner", self.staff_c, update_modified=False
		)

		# 4. Lead revoked in Team A whose `lead_owner` was set externally to `staff_c`.
		lead_revoked_ext_owner = self.make_canonical_lead()
		self.make_work_item(campaign_a, lead_revoked_ext_owner, status="revoked")
		frappe.db.set_value(
			"CRM Lead", lead_revoked_ext_owner, "lead_owner", self.staff_c, update_modified=False
		)

		# 5. Lead returned to pool (`return_to_pool=True` -> `queued`) in Team A, then assigned in Team B (`pool_list` L-23).
		lead_pool_return_to_b = self.make_canonical_lead()
		raw_phone_pool_b = str(frappe.db.get_value("CRM Lead", lead_pool_return_to_b, "mobile_no") or "")
		wi_a_pool_return = self.make_work_item(campaign_a, lead_pool_return_to_b, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_pool_return_to_b,
				"campaign": campaign_a,
				"work_item": wi_a_pool_return,
			}
		).insert(ignore_permissions=True)
		ownership_service.assign(
			wi_a_pool_return,
			self.staff_a,
			actor=self.admin_user,
			reason="Initial Team A before return_to_pool",
			request_id=self.unique_key("l23-poolret-asg-a"),
		)
		ownership_service.revoke(
			wi_a_pool_return,
			expected_owner=self.staff_a,
			actor=self.admin_user,
			reason="Return to pool in Team A",
			request_id=self.unique_key("l23-poolret-rev-a"),
			return_to_pool=True,
		)
		wi_b_pool_claim = self.make_work_item(campaign_b, lead_pool_return_to_b, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_pool_return_to_b,
				"campaign": campaign_b,
				"work_item": wi_b_pool_claim,
			}
		).insert(ignore_permissions=True)
		ownership_service.assign(
			wi_b_pool_claim,
			self.staff_c,
			actor=self.admin_user,
			reason="Claimed by Team B",
			request_id=self.unique_key("l23-poolret-asg-b"),
		)

		# --- Verify BOTH `reporting.customer_pool` AND `customers_api.customer_pool` as Leader A ---
		frappe.set_user(self.leader_a)
		for pool_a in (
			reporting.customer_pool(self.leader_a, page=1, page_size=100),
			customers_api.customer_pool(page=1, page_size=100),
		):
			leads_a = {row["lead"]: row for row in pool_a["items"]}
			self.assertNotIn(lead_revoked_to_b, leads_a)
			self.assertNotIn(lead_closed_to_b_queued, leads_a)
			self.assertNotIn(lead_revoked_to_shared_b, leads_a)
			self.assertNotIn(lead_revoked_ext_owner, leads_a)
			self.assertNotIn(lead_pool_return_to_b, leads_a)

			serialized_a = json.dumps(pool_a, default=str)
			self.assertNotIn(raw_phone_b, serialized_a)
			self.assertNotIn(raw_phone_pool_b, serialized_a)
			self.assertNotIn(alt_phone_b, serialized_a)
			self.assertNotIn(self.staff_c, serialized_a)
			self.assertNotIn(campaign_b, serialized_a)
			self.assertNotIn("Chiến dịch Riêng Đội B L23", serialized_a)

			# Positive controls for Leader A remain intact and exact.
			self.assertEqual(leads_a[lead_active_a]["state"], "assigned")
			self.assertEqual(leads_a[lead_active_a]["owner"], self.staff_a)
			self.assertEqual(leads_a[lead_queued_a]["state"], "queued")
			self.assertEqual(leads_a[lead_revoked_stay_a]["state"], "revoked")
			self.assertEqual(pool_a["total"], baseline_total_a)
			self.assertEqual(pool_a["unique_customers"], baseline_total_a)
			self.assertEqual(pool_a["work_items"], baseline_wi_a)

		for qpool_a in (
			reporting.pool_list(self.leader_a, page=1, page_size=100, campaign=campaign_a),
			customers_api.pool_list(page=1, page_size=100, campaign=campaign_a),
		):
			qpool_leads_a = {row["lead"]: row for row in qpool_a["items"]}
			self.assertIn(lead_queued_a, qpool_leads_a)
			self.assertNotIn(lead_pool_return_to_b, qpool_leads_a)
			self.assertEqual(qpool_a["total"], 1)
			self.assertEqual(qpool_a["unique_customers"], 2)
			qpool_serialized = json.dumps(qpool_a, default=str)
			self.assertNotIn(raw_phone_pool_b, qpool_serialized)
			self.assertNotIn(self.staff_c, qpool_serialized)

		camp_a_pool = customers_api.customer_pool(page=1, page_size=100, campaign=campaign_a)
		self.assertEqual(camp_a_pool["total"], 3)
		self.assertEqual(camp_a_pool["unique_customers"], 3)
		self.assertEqual(camp_a_pool["work_items"], 3)

		# Search & filter oracles as Leader A (`search`, `campaign`, `source`, `batch`) all return 0.
		for q in (raw_phone_b, alt_phone_b, name_b, lead_revoked_to_b):
			res_q = customers_api.customer_pool(page=1, page_size=20, search=q)
			self.assertEqual(res_q["items"], [])
			self.assertEqual(res_q["total"], 0)
			self.assertEqual(res_q["unique_customers"], 0)
			self.assertEqual(res_q["work_items"], 0)

		for kwargs in (
			{"campaign": campaign_b},
			{"source": src_b},
			{"campaign": campaign_a, "source": src_b},
			{"batch": batch_doc.name},
		):
			res_f = customers_api.customer_pool(page=1, page_size=20, **kwargs)
			self.assertEqual(res_f["items"], [])
			self.assertEqual(res_f["total"], 0)
			self.assertEqual(res_f["unique_customers"], 0)
			self.assertEqual(res_f["work_items"], 0)

		stay_search = customers_api.customer_pool(page=1, page_size=20, search=stay_a_phone)
		self.assertEqual([r["lead"] for r in stay_search["items"]], [lead_revoked_stay_a])
		self.assertEqual(stay_search["total"], 1)

		# --- Positive controls for Leader B, CRM Admin, BOD Viewer, and Staff denial ---
		frappe.set_user(self.leader_b)
		pool_b = customers_api.customer_pool(page=1, page_size=100)
		leads_b = {row["lead"]: row for row in pool_b["items"]}
		self.assertEqual(leads_b[lead_revoked_to_b]["state"], "assigned")
		self.assertEqual(leads_b[lead_revoked_to_b]["owner"], self.staff_c)
		self.assertEqual(leads_b[lead_revoked_to_b]["campaign"], campaign_b)
		self.assertEqual(leads_b[lead_closed_to_b_queued]["state"], "queued")
		self.assertEqual(leads_b[lead_closed_to_b_queued]["campaign"], campaign_b)
		self.assertEqual(leads_b[lead_revoked_to_shared_b]["state"], "assigned")
		self.assertEqual(leads_b[lead_revoked_to_shared_b]["owner"], self.staff_c)
		self.assertNotIn(lead_active_a, leads_b)
		self.assertNotIn(lead_revoked_stay_a, leads_b)
		pool_b_batch = customers_api.customer_pool(batch=batch_doc.name)
		self.assertEqual(pool_b_batch["total"], 1)
		self.assertEqual(pool_b_batch["work_items"], 1)

		frappe.set_user(self.admin_user)
		pool_admin = customers_api.customer_pool(page=1, page_size=100, source=src_b)
		self.assertEqual(pool_admin["total"], 1)
		self.assertEqual(pool_admin["items"][0]["lead"], lead_revoked_to_b)
		self.assertEqual(pool_admin["items"][0]["state"], "assigned")
		self.assertEqual(pool_admin["items"][0]["campaign"], campaign_b)
		self.assertEqual(pool_admin["items"][0]["owner"], self.staff_c)
		self.assertEqual(pool_admin["items"][0]["phone"], raw_phone_b)

		frappe.set_user(self.bod_user)
		pool_bod = customers_api.customer_pool(page=1, page_size=100, source=src_b)
		self.assertEqual(pool_bod["total"], 1)
		self.assertEqual(pool_bod["items"][0]["state"], "assigned")
		self.assertIsNone(pool_bod["items"][0]["phone"])
		self.assertEqual(pool_bod["items"][0]["display_name"], "")
		with self.assertRaises(Forbidden):
			customers_api.customer_pool(search=raw_phone_b)

		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden):
			customers_api.customer_pool()
		self.assertEqual(reporting.customer_pool(self.staff_a)["total"], 0)


class TestL26BackendPaginationAndLimitValidation(P1TestCase):
	"""L-26 — Backend endpoints `ownership.history`, `customers.pool_list`,
	`customers.customer_pool`, and `customers.my_list` (plus `campaigns.save_rule`
	and `distribution.enqueue_run`) must validate integer parameters strictly and
	return HTTP 400 `INVALID_REQUEST`, never HTTP 500 `ValueError`.
	"""

	def test_invalid_pagination_and_limit_raise_400_invalid_request_on_backend_endpoints(self):
		from crm_cskh.api import campaigns as campaigns_api
		from crm_cskh.api import customers as customers_api
		from crm_cskh.api import ownership as ownership_api
		from crm_cskh.services.errors import InvalidRequest

		frappe.set_user("Administrator")
		campaign = self.make_campaign(team_scope=self.team_a)
		lead = self.make_canonical_lead()
		wi = self.make_work_item(campaign, lead, status="queued")
		frappe.get_doc(
			{"doctype": "CSKH Active Claim", "lead": lead, "campaign": campaign, "work_item": wi}
		).insert(ignore_permissions=True)
		ownership_assign(
			wi,
			self.staff_a,
			actor=self.admin_user,
			reason="L-26 history seed",
			request_id=self.unique_key("l26-hist"),
		)

		frappe.set_user(self.admin_user)
		invalid_pagination_cases = [
			{"page": "abc", "page_size": 20},
			{"page": 1, "page_size": "xyz"},
			{"page": 0, "page_size": 20},
			{"page": -1, "page_size": 20},
			{"page": 1, "page_size": 0},
			{"page": 1, "page_size": -5},
			{"page": 1, "page_size": 101},
			{"page": 1_000_001, "page_size": 20},
			{"page": "1.5", "page_size": 20},
			{"page": True, "page_size": 20},
			{"page": 1, "page_size": False},
		]

		for fn in (
			customers_api.pool_list,
			customers_api.customer_pool,
			customers_api.my_list,
			lambda **kw: reporting.pool_list(self.admin_user, **kw),
			lambda **kw: reporting.customer_pool(self.admin_user, **kw),
			lambda **kw: reporting.my_list(self.staff_a, **kw),
		):
			for bad_kwargs in invalid_pagination_cases:
				with self.assertRaises(InvalidRequest) as ctx:
					fn(**bad_kwargs)
				self.assertEqual(ctx.exception.http_status, 400)
				self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

			ok_res = fn(page="1", page_size="5")
			self.assertEqual(ok_res["page"], 1)
			self.assertEqual(ok_res["page_size"], 5)
			empty_res = fn(page="", page_size="")
			self.assertEqual(empty_res["page"], 1)
			self.assertEqual(empty_res["page_size"], 20)

		# Direct `ownership.history` validation on `limit` and `work_item`.
		for bad_limit in ("abc", "1.5", 0, "0", -1, "-5", 201, "201", True, False, [10]):
			with self.assertRaises(InvalidRequest) as lim_ctx:
				ownership_api.history(work_item=wi, limit=bad_limit)
			self.assertEqual(lim_ctx.exception.http_status, 400)
			self.assertEqual(lim_ctx.exception.code, "INVALID_REQUEST")

		for bad_wi in (None, "", "   ", 123):
			with self.assertRaises(InvalidRequest) as wi_ctx:
				ownership_api.history(work_item=bad_wi, limit=50)
			self.assertEqual(wi_ctx.exception.http_status, 400)
			self.assertEqual(wi_ctx.exception.code, "INVALID_REQUEST")

		ok_hist = ownership_api.history(work_item=wi, limit="10")
		self.assertEqual(ok_hist["work_item"], wi)
		self.assertGreaterEqual(ok_hist["total"], 1)
		default_hist = ownership_api.history(work_item=wi, limit="")
		self.assertEqual(default_hist["total"], ok_hist["total"])

		# Verify P1 HTTP response normalizer produces HTTP 400 + stable `INVALID_REQUEST` envelope.
		clear_p1_error_context()
		with self.assertRaises(InvalidRequest) as http_ctx:
			ownership_api.history(work_item=wi, limit="abc")
		exc = http_ctx.exception
		resp = _DummyResponse(
			exc.http_status_code,
			{
				"exc_type": type(exc).__name__,
				"_error_message": str(exc),
			},
		)
		req = _DummyRequest(path="/api/method/crm_cskh.api.ownership.history")
		out = normalize_p1_error_response(normalize_error_response(resp, req), req)
		self.assertEqual(out.status_code, 400)
		set_body = json.loads(
			out.data.decode("utf-8") if isinstance(out.data, (bytes, bytearray)) else out.data
		)
		self.assertEqual(set_body["code"], "INVALID_REQUEST")
		self.assertEqual(set_body["p1_code"], "INVALID_REQUEST")
		self.assertEqual(set_body["exc_type"], "INVALID_REQUEST")

		# Bonus hardening: `campaigns.save_rule` rejects booleans and invalid `version`.
		for bad_rule_kwargs in (
			{"campaign": campaign, "user": self.staff_a, "weight": True},
			{"campaign": campaign, "user": self.staff_a, "weight": 10, "capacity_limit": True},
			{"campaign": campaign, "user": self.staff_a, "weight": 10, "version": "abc"},
			{"campaign": campaign, "user": self.staff_a, "weight": 10, "version": 0},
		):
			with self.assertRaises(InvalidRequest) as rule_ctx:
				campaigns_api.save_rule(**bad_rule_kwargs)
			self.assertEqual(rule_ctx.exception.http_status, 400)
			self.assertEqual(rule_ctx.exception.code, "INVALID_REQUEST")


class TestL29GenericFrappeApiPermissionAndRawRunProtection(P1TestCase):
	"""L-29 — Generic Frappe API (`frappe.client.*`, Desk `getdoc` / `reportview.get` /
	`search_link` / `search_widget` / `validate_link`) and direct `has_permission`
	must enforce document-level team scope on `CSKH Campaign`, `CSKH Assignment Rule`,
	and `CSKH Distribution Run`, and must protect raw distribution/selection JSON
	(`preview_json`, `actual_json`, `state_json`, `progress`, `error`,
	`lease_expires_at`, `selection_json`) via `permlevel = 1` while preserving
	permitted projection APIs (`distribution.get_run`, `campaigns.list`, `campaigns.rules`,
	`dashboard.distribution`).
	"""

	RAW_RUN_FIELDS = (
		"preview_json",
		"actual_json",
		"state_json",
		"progress",
		"error",
		"lease_expires_at",
	)

	def _build_fixture(self):
		frappe.set_user("Administrator")
		lead_a = self.make_canonical_lead()
		lead_b = self.make_canonical_lead()
		lead_sh_a = self.make_canonical_lead()
		lead_sh_b = self.make_canonical_lead()

		camp_a = self.make_campaign(
			title="L29 Private Team A",
			team_scope=self.team_a,
			selection={"leads": [lead_a], "marker": "L29_SEL_A_SECRET"},
		)
		camp_b = self.make_campaign(
			title="L29 Private Team B",
			team_scope=self.team_b,
			selection={"leads": [lead_b], "marker": "L29_SEL_B_SECRET"},
		)
		camp_shared = self.make_campaign(
			title="L29 Shared Both Teams",
			team_scope=None,
			selection={"leads": [lead_sh_a, lead_sh_b], "marker": "L29_SEL_SHARED_SECRET"},
		)

		rule_a = self.make_rule(camp_a, self.staff_a, weight=50, team=self.team_a)
		rule_b = self.make_rule(camp_b, self.staff_c, weight=50, team=self.team_b)
		rule_shared_a = self.make_rule(camp_shared, self.staff_a, weight=60, team=self.team_a)
		rule_shared_b = self.make_rule(camp_shared, self.staff_c, weight=40, team=self.team_b)

		def _insert_run(campaign, tag, preview_obj, actual_obj, state_obj, err_text=None):
			doc = frappe.get_doc(
				{
					"doctype": "CSKH Distribution Run",
					"campaign": campaign,
					"idempotency_key": self.unique_key(f"l29-{tag}"),
					"rule_version": 1,
					"selection_fingerprint": f"fp-{tag}",
					"status": "completed",
					"progress": 2,
					"preview_json": json.dumps(preview_obj, ensure_ascii=False),
					"actual_json": json.dumps(actual_obj, ensure_ascii=False),
					"state_json": json.dumps(state_obj, ensure_ascii=False),
					"error": err_text,
				}
			)
			doc.insert(ignore_permissions=True)
			return doc.name

		run_a = _insert_run(
			camp_a,
			"run-a",
			{
				"eligible_count": 1,
				"excluded_count": 0,
				"targets": [{"user": self.staff_a, "weight": 50, "planned": 1}],
				"simulated": {self.staff_a: 1},
				"leads": [lead_a],
				"secret": "L29_RAW_PREVIEW_A",
			},
			{
				"total": 1,
				"created": 1,
				"errors": 0,
				"assigned": {self.staff_a: 1},
				"skipped": [],
				"secret": "L29_RAW_ACTUAL_A",
			},
			{"epoch": 1, "total": 1, "secret": "L29_RAW_STATE_A"},
		)
		run_b = _insert_run(
			camp_b,
			"run-b",
			{
				"eligible_count": 1,
				"excluded_count": 0,
				"targets": [{"user": self.staff_c, "weight": 50, "planned": 1}],
				"simulated": {self.staff_c: 1},
				"leads": [lead_b],
				"secret": "L29_RAW_PREVIEW_B",
			},
			{
				"total": 1,
				"created": 1,
				"errors": 0,
				"assigned": {self.staff_c: 1},
				"skipped": [],
				"secret": "L29_RAW_ACTUAL_B",
			},
			{"epoch": 1, "total": 1, "secret": "L29_RAW_STATE_B"},
			err_text="L29_RAW_ERR_B",
		)
		run_shared = _insert_run(
			camp_shared,
			"run-shared",
			{
				"eligible_count": 2,
				"excluded_count": 0,
				"targets": [
					{"user": self.staff_a, "weight": 60, "planned": 1},
					{"user": self.staff_c, "weight": 40, "planned": 1},
				],
				"simulated": {self.staff_a: 1, self.staff_c: 1},
				"leads": [lead_sh_a, lead_sh_b],
				"secret": "L29_RAW_PREVIEW_SHARED",
			},
			{
				"total": 2,
				"created": 2,
				"errors": 0,
				"assigned": {self.staff_a: 1, self.staff_c: 1},
				"skipped": [{"lead": lead_b, "reason": "already_claimed"}],
				"secret": "L29_RAW_ACTUAL_SHARED",
			},
			{"epoch": 1, "total": 2, "secret": "L29_RAW_STATE_SHARED"},
			err_text="L29_RAW_ERR_SHARED",
		)
		frappe.db.commit()
		return {
			"lead_a": lead_a,
			"lead_b": lead_b,
			"lead_sh_a": lead_sh_a,
			"lead_sh_b": lead_sh_b,
			"camp_a": camp_a,
			"camp_b": camp_b,
			"camp_shared": camp_shared,
			"rule_a": rule_a,
			"rule_b": rule_b,
			"rule_shared_a": rule_shared_a,
			"rule_shared_b": rule_shared_b,
			"run_a": run_a,
			"run_b": run_b,
			"run_shared": run_shared,
		}

	def test_doctype_and_document_level_has_permission_across_all_actors(self):
		from frappe.permissions import get_doc_permissions

		fx = self._build_fixture()
		doctypes = ("CSKH Campaign", "CSKH Assignment Rule", "CSKH Distribution Run")

		# DocType-level read (`doc=None`): Admin, BOD, Leader A/B -> True; Staff & Guest -> False.
		for dt in doctypes:
			for allowed_actor in (self.admin_user, self.bod_user, self.leader_a, self.leader_b):
				self.assertTrue(
					frappe.has_permission(dt, "read", doc=None, user=allowed_actor, throw=False),
					f"{allowed_actor} must pass DocType read on {dt}",
				)
			for denied_actor in (self.staff_a, "Guest"):
				self.assertFalse(
					frappe.has_permission(dt, "read", doc=None, user=denied_actor, throw=False),
					f"{denied_actor} must be denied DocType read on {dt}",
				)
			# Write/create/delete/export/share denied for ALL non-system business roles.
			for actor in (
				self.admin_user,
				self.bod_user,
				self.leader_a,
				self.leader_b,
				self.staff_a,
				"Guest",
			):
				for ptype in ("write", "create", "delete", "export", "share"):
					self.assertFalse(
						frappe.has_permission(dt, ptype, doc=None, user=actor, throw=False),
						f"{actor} must be denied {ptype} on {dt}",
					)

		# Document-level checks (both string docname AND loaded `Document`, plus `get_doc_permissions`).
		expectations = [
			# (doctype, docname, allowed_users, denied_users)
			(
				"CSKH Campaign",
				fx["camp_a"],
				(self.admin_user, self.bod_user, self.leader_a),
				(self.leader_b, self.staff_a, "Guest"),
			),
			(
				"CSKH Campaign",
				fx["camp_b"],
				(self.admin_user, self.bod_user, self.leader_b),
				(self.leader_a, self.staff_a, "Guest"),
			),
			(
				"CSKH Campaign",
				fx["camp_shared"],
				(self.admin_user, self.bod_user, self.leader_a, self.leader_b),
				(self.staff_a, "Guest"),
			),
			(
				"CSKH Assignment Rule",
				fx["rule_a"],
				(self.admin_user, self.bod_user, self.leader_a),
				(self.leader_b, self.staff_a, "Guest"),
			),
			(
				"CSKH Assignment Rule",
				fx["rule_b"],
				(self.admin_user, self.bod_user, self.leader_b),
				(self.leader_a, self.staff_a, "Guest"),
			),
			(
				"CSKH Assignment Rule",
				fx["rule_shared_a"],
				(self.admin_user, self.bod_user, self.leader_a),
				(self.leader_b, self.staff_a, "Guest"),
			),
			(
				"CSKH Assignment Rule",
				fx["rule_shared_b"],
				(self.admin_user, self.bod_user, self.leader_b),
				(self.leader_a, self.staff_a, "Guest"),
			),
			(
				"CSKH Distribution Run",
				fx["run_a"],
				(self.admin_user, self.bod_user, self.leader_a),
				(self.leader_b, self.staff_a, "Guest"),
			),
			(
				"CSKH Distribution Run",
				fx["run_b"],
				(self.admin_user, self.bod_user, self.leader_b),
				(self.leader_a, self.staff_a, "Guest"),
			),
			(
				"CSKH Distribution Run",
				fx["run_shared"],
				(self.admin_user, self.bod_user, self.leader_a, self.leader_b),
				(self.staff_a, "Guest"),
			),
		]
		for dt, docname, allowed_users, denied_users in expectations:
			frappe.set_user("Administrator")
			doc_obj = frappe.get_doc(dt, docname)
			for actor in allowed_users:
				self.assertTrue(
					frappe.has_permission(dt, "read", doc=docname, user=actor, throw=False),
					f"{actor} must read {dt} {docname} by name",
				)
				self.assertTrue(
					frappe.has_permission(dt, "read", doc=doc_obj, user=actor, throw=False),
					f"{actor} must read {dt} {docname} by Document",
				)
				self.assertEqual(get_doc_permissions(doc_obj, user=actor).get("read"), 1)
				self.assertFalse(frappe.has_permission(dt, "write", doc=doc_obj, user=actor, throw=False))
			for actor in denied_users:
				self.assertFalse(
					frappe.has_permission(dt, "read", doc=docname, user=actor, throw=False),
					f"{actor} must NOT read {dt} {docname} by name",
				)
				self.assertFalse(
					frappe.has_permission(dt, "read", doc=doc_obj, user=actor, throw=False),
					f"{actor} must NOT read {dt} {docname} by Document",
				)
				self.assertFalse(get_doc_permissions(doc_obj, user=actor).get("read"))

	def test_frappe_client_and_desk_paths_enforce_team_scope_and_strip_raw_json(self):
		import frappe.client as frappe_client
		import frappe.desk.form.load as desk_load
		import frappe.desk.reportview as desk_reportview

		from crm_cskh.api import campaigns as campaigns_api
		from crm_cskh.api import dashboard as dashboard_api
		from crm_cskh.api import distribution as distribution_api
		from crm_cskh.api import guarded_crm
		from crm_cskh.p1_permissions import validate_p1_filter_fields
		from crm_cskh.services.errors import Forbidden

		fx = self._build_fixture()
		explicit_run_fields = ["name", "campaign", "status", *self.RAW_RUN_FIELDS]
		explicit_camp_fields = ["name", "code", "title", "team_scope", "selection_json"]
		explicit_rule_fields = ["name", "campaign", "user", "team", "weight", "capacity_limit"]

		def _desk_getdoc_dict(dt, name):
			frappe.local.response = frappe._dict({"docs": []})
			desk_load.getdoc(dt, name)
			return frappe.local.response.docs[-1].as_dict()

		def _desk_reportview_rows(dt, fields, filters):
			prev = dict(getattr(frappe.local, "form_dict", None) or {})
			try:
				frappe.local.form_dict = frappe._dict(
					{
						"doctype": dt,
						"fields": json.dumps(fields),
						"filters": json.dumps(filters),
						"limit_page_length": 100,
					}
				)
				compressed = desk_reportview.get()
				if not compressed:
					return []
				keys = compressed["keys"]
				return [dict(zip(keys, vals, strict=False)) for vals in compressed["values"]]
			finally:
				frappe.local.form_dict = frappe._dict(prev)

		# --- 1. Leader A: positive own/shared metadata, negative foreign docs + zero raw JSON leak ---
		frappe.set_user(self.leader_a)
		for dt, denied_name in (
			("CSKH Campaign", fx["camp_b"]),
			("CSKH Assignment Rule", fx["rule_b"]),
			("CSKH Assignment Rule", fx["rule_shared_b"]),
			("CSKH Distribution Run", fx["run_b"]),
		):
			with self.assertRaises(frappe.PermissionError):
				frappe_client.get(dt, denied_name)
			with self.assertRaises(frappe.PermissionError):
				_desk_getdoc_dict(dt, denied_name)
			self.assertEqual(frappe_client.get_value(dt, ["name"], denied_name), {})
			self.assertEqual(guarded_crm.validate_link(dt, denied_name, fields=["status"]), {})

		# Leader A positive `frappe.client.get` + Desk `getdoc` on own & shared campaign/run/rule:
		for camp_id in (fx["camp_a"], fx["camp_shared"]):
			c_doc = frappe_client.get("CSKH Campaign", camp_id)
			self.assertEqual(c_doc["name"], camp_id)
			self.assertIsNone(c_doc.get("selection_json"))
			d_doc = _desk_getdoc_dict("CSKH Campaign", camp_id)
			self.assertEqual(d_doc["name"], camp_id)
			self.assertIsNone(d_doc.get("selection_json"))
			self.assertNotIn("L29_SEL_", json.dumps({"c": c_doc, "d": d_doc}, default=str))

		for rule_id in (fx["rule_a"], fx["rule_shared_a"]):
			r_doc = frappe_client.get("CSKH Assignment Rule", rule_id)
			self.assertEqual(r_doc["name"], rule_id)
			self.assertEqual(r_doc["user"], self.staff_a)
			dr_doc = _desk_getdoc_dict("CSKH Assignment Rule", rule_id)
			self.assertEqual(dr_doc["name"], rule_id)

		non_int_raw_fields = ("preview_json", "actual_json", "state_json", "error", "lease_expires_at")
		for run_id in (fx["run_a"], fx["run_shared"]):
			run_doc = frappe_client.get("CSKH Distribution Run", run_id)
			self.assertEqual(run_doc["name"], run_id)
			for f in non_int_raw_fields:
				self.assertIsNone(
					run_doc.get(f), f"Leader A must NOT receive {f} value via frappe.client.get"
				)
			self.assertIn(run_doc.get("progress"), (None, 0))
			d_run = _desk_getdoc_dict("CSKH Distribution Run", run_id)
			for f in non_int_raw_fields:
				self.assertIsNone(d_run.get(f), f"Leader A must NOT receive {f} value via Desk getdoc")
			self.assertIn(d_run.get("progress"), (None, 0))
			raw_str = json.dumps({"c": run_doc, "d": d_run}, default=str)
			self.assertNotIn(self.staff_c, raw_str)
			self.assertNotIn(fx["lead_b"], raw_str)
			self.assertNotIn(fx["lead_sh_b"], raw_str)
			self.assertNotIn("L29_RAW_", raw_str)

		# Leader A `frappe.client.get_list` & Desk `reportview.get` with explicit `fields=[...]`:
		camp_list_a = frappe_client.get_list(
			"CSKH Campaign",
			fields=explicit_camp_fields,
			filters={"name": ["in", [fx["camp_a"], fx["camp_b"], fx["camp_shared"]]]},
			limit_page_length=20,
		)
		self.assertEqual({r["name"] for r in camp_list_a}, {fx["camp_a"], fx["camp_shared"]})
		for row in camp_list_a:
			self.assertNotIn("selection_json", row)

		rule_list_a = frappe_client.get_list(
			"CSKH Assignment Rule",
			fields=explicit_rule_fields,
			filters={"name": ["in", [fx["rule_a"], fx["rule_b"], fx["rule_shared_a"], fx["rule_shared_b"]]]},
			limit_page_length=20,
		)
		self.assertEqual({r["name"] for r in rule_list_a}, {fx["rule_a"], fx["rule_shared_a"]})

		run_list_a = frappe_client.get_list(
			"CSKH Distribution Run",
			fields=explicit_run_fields,
			filters={"name": ["in", [fx["run_a"], fx["run_b"], fx["run_shared"]]]},
			limit_page_length=20,
		)
		self.assertEqual({r["name"] for r in run_list_a}, {fx["run_a"], fx["run_shared"]})
		for row in run_list_a:
			for f in self.RAW_RUN_FIELDS:
				self.assertNotIn(f, row)

		rv_runs_a = _desk_reportview_rows(
			"CSKH Distribution Run",
			explicit_run_fields,
			[["CSKH Distribution Run", "name", "in", [fx["run_a"], fx["run_b"], fx["run_shared"]]]],
		)
		self.assertEqual({r["name"] for r in rv_runs_a}, {fx["run_a"], fx["run_shared"]})
		for row in rv_runs_a:
			for f in self.RAW_RUN_FIELDS:
				self.assertNotIn(f, row)

		# Desk `search_link` / `search_widget` with `ignore_user_permissions=True` cannot bypass scope!
		sl_camps = guarded_crm.search_link(
			"CSKH Campaign",
			"",
			filters={"name": ["in", [fx["camp_a"], fx["camp_b"], fx["camp_shared"]]]},
			ignore_user_permissions=True,
		)
		self.assertEqual({r["value"] for r in sl_camps}, {fx["camp_a"], fx["camp_shared"]})
		sw_runs = guarded_crm.search_widget(
			"CSKH Distribution Run",
			"",
			filters={"name": ["in", [fx["run_a"], fx["run_b"], fx["run_shared"]]]},
			as_dict=True,
			ignore_user_permissions=True,
		)
		self.assertEqual({r["name"] for r in sw_runs}, {fx["run_a"], fx["run_shared"]})

		# Filter oracle on `permlevel = 1` fields rejected with `PermissionError`:
		for dt, flt in (
			("CSKH Distribution Run", [["actual_json", "like", "%staff%"]]),
			("CSKH Distribution Run", {"preview_json": ["like", "%"]}),
			("CSKH Distribution Run", [["state_json", "like", "%epoch%"]]),
			("CSKH Campaign", [["selection_json", "like", "%L29%"]]),
		):
			with self.assertRaises(frappe.PermissionError):
				validate_p1_filter_fields(dt, filters=flt, user=self.leader_a)
			with self.assertRaises(frappe.PermissionError):
				guarded_crm.search_link(dt, "", filters=flt)

		# `campaigns.rules`: Leader A denied foreign `camp_b`, allowed `camp_a`, and on `camp_shared` sees only own team!
		with self.assertRaises(Forbidden):
			campaigns_api.rules(campaign=fx["camp_b"])
		shared_rules_a = campaigns_api.rules(campaign=fx["camp_shared"])
		self.assertEqual([r["user"] for r in shared_rules_a["eligible"]], [self.staff_a])

		# --- 2. Leader B: symmetric checks ---
		frappe.set_user(self.leader_b)
		for dt, denied_name in (
			("CSKH Campaign", fx["camp_a"]),
			("CSKH Assignment Rule", fx["rule_a"]),
			("CSKH Assignment Rule", fx["rule_shared_a"]),
			("CSKH Distribution Run", fx["run_a"]),
		):
			with self.assertRaises(frappe.PermissionError):
				frappe_client.get(dt, denied_name)
			with self.assertRaises(frappe.PermissionError):
				_desk_getdoc_dict(dt, denied_name)
		self.assertEqual(frappe_client.get("CSKH Campaign", fx["camp_b"])["name"], fx["camp_b"])
		self.assertEqual(frappe_client.get("CSKH Assignment Rule", fx["rule_b"])["name"], fx["rule_b"])
		run_shared_b = frappe_client.get("CSKH Distribution Run", fx["run_shared"])
		self.assertEqual(run_shared_b["name"], fx["run_shared"])
		for f in non_int_raw_fields:
			self.assertIsNone(run_shared_b.get(f))
		self.assertIn(run_shared_b.get("progress"), (None, 0))
		self.assertNotIn(self.staff_a, json.dumps(run_shared_b, default=str))
		self.assertNotIn(fx["lead_sh_a"], json.dumps(run_shared_b, default=str))
		shared_rules_b = campaigns_api.rules(campaign=fx["camp_shared"])
		self.assertEqual([r["user"] for r in shared_rules_b["eligible"]], [self.staff_c])

		# --- 3. BOD Viewer: reads metadata across teams, raw JSON stripped on generic APIs, projection APIs work ---
		frappe.set_user(self.bod_user)
		bod_runs = frappe_client.get_list(
			"CSKH Distribution Run",
			fields=explicit_run_fields,
			filters={"name": ["in", [fx["run_a"], fx["run_b"], fx["run_shared"]]]},
			limit_page_length=20,
		)
		self.assertEqual({r["name"] for r in bod_runs}, {fx["run_a"], fx["run_b"], fx["run_shared"]})
		for row in bod_runs:
			for f in self.RAW_RUN_FIELDS:
				self.assertNotIn(f, row)
		bod_getdoc = _desk_getdoc_dict("CSKH Distribution Run", fx["run_shared"])
		for f in non_int_raw_fields:
			self.assertIsNone(bod_getdoc.get(f))
		self.assertIn(bod_getdoc.get("progress"), (None, 0))
		self.assertNotIn("L29_RAW_", json.dumps(bod_getdoc, default=str))
		with self.assertRaises(frappe.PermissionError):
			validate_p1_filter_fields(
				"CSKH Distribution Run",
				filters=[["preview_json", "like", "%L29%"]],
				user=self.bod_user,
			)
		# Permitted projection APIs continue to work for BOD:
		bod_proj = distribution_api.get_run(run_id=fx["run_shared"])
		self.assertEqual(bod_proj["run_id"], fx["run_shared"])
		self.assertEqual(bod_proj["assigned"], {self.staff_a: 1, self.staff_c: 1})
		self.assertNotIn("preview_json", bod_proj)
		self.assertNotIn("state_json", bod_proj)
		self.assertEqual(dashboard_api.distribution(campaign=fx["camp_shared"])["scope"], "all")

		# --- 4. CRM-ADMIN: reads all docs + `permlevel = 1` read-only, write still denied ---
		frappe.set_user(self.admin_user)
		admin_run_b = frappe_client.get("CSKH Distribution Run", fx["run_b"])
		self.assertIn("L29_RAW_ACTUAL_B", admin_run_b["actual_json"])
		self.assertIn("L29_RAW_PREVIEW_B", admin_run_b["preview_json"])
		self.assertEqual(admin_run_b["progress"], 2)
		admin_camp_b = _desk_getdoc_dict("CSKH Campaign", fx["camp_b"])
		self.assertIn("L29_SEL_B_SECRET", admin_camp_b["selection_json"])
		with self.assertRaises(frappe.PermissionError):
			frappe_client.set_value("CSKH Campaign", fx["camp_a"], "title", "Tampered")

		# --- 5. Staff A & Guest: denied across all generic client & Desk read paths ---
		for denied_actor in (self.staff_a, "Guest"):
			frappe.set_user(denied_actor)
			for dt, docname in (
				("CSKH Campaign", fx["camp_a"]),
				("CSKH Assignment Rule", fx["rule_a"]),
				("CSKH Distribution Run", fx["run_a"]),
			):
				with self.assertRaises(frappe.PermissionError):
					frappe_client.get(dt, docname)
				with self.assertRaises(frappe.PermissionError):
					frappe_client.get_list(dt, fields=["name"])
				with self.assertRaises(frappe.PermissionError):
					_desk_getdoc_dict(dt, docname)
				with self.assertRaises(frappe.PermissionError):
					guarded_crm.validate_link(dt, docname)


class TestDirectOwnershipMutationEligibility(P1TestCase):
	"""Direct mutation eligibility on `ownership.assign` and `ownership.reassign`
	(never inferred from UI `assignees` dropdown; P1 §7.1, §7.2, §7.6).
	"""

	def test_direct_assign_and_reassign_enforce_team_scope_rule_state_capacity_and_claims(self):
		from crm_cskh.api import ownership as ownership_api
		from crm_cskh.services.errors import Conflict, InvalidRequest

		frappe.set_user("Administrator")
		camp_a = self.make_campaign(title="Direct Mut Team A", team_scope=self.team_a, status="Active")
		camp_b = self.make_campaign(title="Direct Mut Team B", team_scope=self.team_b, status="Active")
		camp_closed = self.make_campaign(title="Direct Mut Closed", team_scope=self.team_a, status="Closed")

		lead_1 = self.make_canonical_lead()
		lead_2 = self.make_canonical_lead()
		lead_3 = self.make_canonical_lead()
		lead_closed = self.make_canonical_lead()
		lead_claimed_b = self.make_canonical_lead()

		wi_1 = self.make_work_item(camp_a, lead_1, status="queued")
		wi_2 = self.make_work_item(camp_a, lead_2, status="queued")
		wi_3 = self.make_work_item(camp_a, lead_3, status="queued")
		wi_closed = self.make_work_item(camp_closed, lead_closed, status="queued")

		# Rules on `camp_a`:
		# - `staff_a`: v1 capacity=3 -> v2 capacity=1 (latest version lowers capacity to 1!)
		# - `staff_b`: v1 enabled=1 -> v2 enabled=0 (latest version disables `staff_b`!)
		self.make_rule(camp_a, self.staff_a, weight=50, capacity=3, version=1, enabled=1, team=self.team_a)
		self.make_rule(camp_a, self.staff_a, weight=50, capacity=1, version=2, enabled=1, team=self.team_a)
		self.make_rule(camp_a, self.staff_b, weight=50, capacity=5, version=1, enabled=1, team=self.team_a)
		rule_b_v2 = self.make_rule(
			camp_a, self.staff_b, weight=50, capacity=5, version=2, enabled=0, team=self.team_a
		)

		frappe.set_user(self.admin_user)

		# 1. Direct `assign` rejects out-of-team Sales Users (`staff_c` of Team B, `staff_outside`).
		for wrong_user in (self.staff_c, self.staff_outside):
			with self.assertRaises(InvalidRequest) as wt_ctx:
				ownership_api.assign(
					work_item=wi_1,
					target_user=wrong_user,
					reason="wrong team assign",
					request_id=self.unique_key("mut-wt-asg"),
				)
			self.assertEqual(wt_ctx.exception.code, "INVALID_REQUEST")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi_1, "status"), "queued")

		# 2. Direct `assign` rejects target whose latest rule version is disabled (`staff_b` v2 enabled=0).
		with self.assertRaises(InvalidRequest) as dis_ctx:
			ownership_api.assign(
				work_item=wi_1,
				target_user=self.staff_b,
				reason="disabled rule assign",
				request_id=self.unique_key("mut-dis-asg"),
			)
		self.assertEqual(dis_ctx.exception.code, "INVALID_REQUEST")

		# 3. Direct `assign` positive control: `staff_a` has capacity=1 in v2 and used=0 -> succeeds.
		ok_asg = ownership_api.assign(
			work_item=wi_1,
			target_user=self.staff_a,
			reason="valid assign",
			request_id=self.unique_key("mut-ok-asg"),
		)
		self.assertEqual(ok_asg["status"], "assigned")
		self.assertEqual(ok_asg["owner"], self.staff_a)

		# 4. Direct `assign` rejects `staff_a` on `wi_2` with `CAPACITY_EXCEEDED` (v2 capacity=1 is full,
		#    historical v1 capacity=3 must NOT bypass v2!).
		with self.assertRaises(Conflict) as cap_asg_ctx:
			ownership_api.assign(
				work_item=wi_2,
				target_user=self.staff_a,
				reason="over v2 capacity assign",
				request_id=self.unique_key("mut-cap-asg"),
			)
		self.assertEqual(cap_asg_ctx.exception.code, "CAPACITY_EXCEEDED")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi_2, "status"), "queued")

		# 5. Direct `reassign` from `staff_a` (`wi_1`) rejects out-of-team `staff_c` and disabled-rule `staff_b`.
		with self.assertRaises(InvalidRequest):
			ownership_api.reassign(
				work_item=wi_1,
				expected_owner=self.staff_a,
				target_user=self.staff_c,
				reason="wrong team reassign",
				request_id=self.unique_key("mut-wt-reasg"),
			)
		with self.assertRaises(InvalidRequest):
			ownership_api.reassign(
				work_item=wi_1,
				expected_owner=self.staff_a,
				target_user=self.staff_b,
				reason="disabled rule reassign",
				request_id=self.unique_key("mut-dis-reasg"),
			)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi_1, "assigned_to"), self.staff_a)

		# 6. Re-enable `staff_b` v2 with capacity=1, assign `wi_2` to `staff_b`, then verify direct `reassign`
		#    of `wi_1` -> `staff_b` hits `CAPACITY_EXCEEDED`, while `wi_3` -> `leader_a` (in `team_a`, no rule row) succeeds!
		frappe.db.set_value(
			"CSKH Assignment Rule", rule_b_v2, {"enabled": 1, "capacity_limit": 1}, update_modified=False
		)
		ok_asg_b = ownership_api.assign(
			work_item=wi_2,
			target_user=self.staff_b,
			reason="fill staff_b capacity",
			request_id=self.unique_key("mut-ok-asg-b"),
		)
		self.assertEqual(ok_asg_b["owner"], self.staff_b)

		with self.assertRaises(Conflict) as cap_reasg_ctx:
			ownership_api.reassign(
				work_item=wi_1,
				expected_owner=self.staff_a,
				target_user=self.staff_b,
				reason="over v2 capacity reassign",
				request_id=self.unique_key("mut-cap-reasg"),
			)
		self.assertEqual(cap_reasg_ctx.exception.code, "CAPACITY_EXCEEDED")

		# Positive reassign to `leader_a` (enabled `Sales User` inside `team_a` subtree):
		ok_reasg = ownership_api.reassign(
			work_item=wi_1,
			expected_owner=self.staff_a,
			target_user=self.leader_a,
			reason="escalate to team A leader",
			request_id=self.unique_key("mut-ok-reasg"),
		)
		self.assertEqual(ok_reasg["status"], "reassigned")
		self.assertEqual(ok_reasg["owner"], self.leader_a)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead_1, "lead_owner"), self.leader_a)

		# Also verify `wi_3` -> `leader_a` fails if `leader_a` gets a rule pointing to foreign `team_b`:
		bad_team_rule = self.make_rule(
			camp_a, self.leader_a, weight=10, capacity=0, version=2, enabled=1, team=self.team_b
		)
		try:
			with self.assertRaises(InvalidRequest):
				ownership_api.assign(
					work_item=wi_3,
					target_user=self.leader_a,
					reason="rule team mismatch",
					request_id=self.unique_key("mut-rule-team"),
				)
		finally:
			frappe.delete_doc("CSKH Assignment Rule", bad_team_rule, force=True, ignore_permissions=True)

		# 7. Closed campaign rejects both `assign` and `reassign` with `Conflict` (`CONFLICT`).
		with self.assertRaises(Conflict) as closed_asg_ctx:
			ownership_api.assign(
				work_item=wi_closed,
				target_user=self.staff_a,
				reason="closed campaign assign",
				request_id=self.unique_key("mut-closed-asg"),
			)
		self.assertEqual(closed_asg_ctx.exception.code, "CONFLICT")

		frappe.db.set_value("CSKH Campaign", camp_a, "status", "Closed", update_modified=False)
		try:
			with self.assertRaises(Conflict) as closed_reasg_ctx:
				ownership_api.reassign(
					work_item=wi_2,
					expected_owner=self.staff_b,
					target_user=self.staff_a,
					reason="closed campaign reassign",
					request_id=self.unique_key("mut-closed-reasg"),
				)
			self.assertEqual(closed_reasg_ctx.exception.code, "CONFLICT")
		finally:
			frappe.db.set_value("CSKH Campaign", camp_a, "status", "Active", update_modified=False)

		# 8. Cross-campaign Active Claim guard on `ownership.assign`:
		#    `wi_claim_a` returned to pool (`queued`) in `camp_a`, then `lead_claimed_b` claimed & assigned in `camp_b`.
		wi_claim_a = self.make_work_item(camp_a, lead_claimed_b, status="queued")
		wi_claim_b = self.make_work_item(camp_b, lead_claimed_b, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_claimed_b,
				"campaign": camp_b,
				"work_item": wi_claim_b,
			}
		).insert(ignore_permissions=True)
		ownership_api.assign(
			work_item=wi_claim_b,
			target_user=self.staff_c,
			reason="claimed in camp_b",
			request_id=self.unique_key("mut-claim-b"),
		)
		with self.assertRaises(Conflict) as cross_claim_ctx:
			ownership_api.assign(
				work_item=wi_claim_a,
				target_user=self.staff_a,
				reason="must not steal active claim from camp_b",
				request_id=self.unique_key("mut-steal-a"),
			)
		self.assertEqual(cross_claim_ctx.exception.code, "ALREADY_ASSIGNED")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead_claimed_b, "lead_owner"), self.staff_c)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi_claim_a, "status"), "queued")


if __name__ == "__main__":
	unittest.main()
