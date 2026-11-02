"""P1 — Regression suite for the T1C baseline findings L-01..L-15.

Every test in this module **reproduces one finding of the T1C verification
baseline** (`deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md`)
and pins the fixed behaviour. The module is deliberately separate from the
functional P1 suites so a regression in a *fix* cannot be masked by the
functional test that motivated it.

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
"""

import csv
import io
import os
import unittest

import frappe

from crm_cskh.jobs import import_worker
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
			     where lft >= (select lft from `tabCRM Sales Hierarchy` where `user` = %s)
			       and rgt <= (select rgt from `tabCRM Sales Hierarchy` where `user` = %s))""",
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
		# A claim of the other team.
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
		expected = frappe.db.sql(
			"""select count(*) from `tabCSKH Active Claim` ac
			   inner join `tabCSKH Work Item` wi on wi.name = ac.work_item
			   where wi.status in ('queued', 'assigned')
			     and wi.assigned_to in ({})""".format(",".join(["%s"] * len(team_a_users))),
			tuple(team_a_users),
		)[0][0]
		self.assertEqual(payload["cards"]["active_claims"], expected)
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
		frappe.db.set_value("CSKH Import Batch", batch["batch"], "status", "QUEUED")
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
		frappe.db.set_value("CSKH Import Batch", batch["batch"], "status", "QUEUED")
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
		frappe.db.set_value("CSKH Import Batch", batch["batch"], "status", "QUEUED")
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


if __name__ == "__main__":
	unittest.main()
