"""P1 — import pipeline (ADR-P1-03, ADR-P1-08, P1 §6).

The invariants under test:

* the pipeline reads a **private** file only, CSV and XLSX, and enforces the
  documented size / row limits;
* the preview classifies every row (valid / duplicate in file / duplicate in
  CRM / invalid) and never trusts a client-supplied preview;
* the confirm step walks the legal state machine (`DRY_RUN -> CONFIRMED ->
  QUEUED`) on the **first** call, enqueues the RQ worker, and the worker
  re-reads and re-normalizes the full file;
* the worker is idempotent: running it twice does not create a second lead;
* an existing lead is reused as-is: the import never changes its owner or
  status;
* a formula / HTML cell is neutralized (SEC-08);
* the failed rows (including rows beyond the 200-row preview truncation) are
  written into a **private** error artifact without double-counting.
"""

import csv
import io
import unittest
from unittest import mock

import frappe

from crm_cskh.jobs import import_worker
from crm_cskh.services import identity as identity_service
from crm_cskh.services import import_service
from crm_cskh.services.errors import InvalidRequest, TooManyRows, UnsupportedFormat
from crm_cskh.services.import_service import (
	confirm,
	create_preview,
	looks_like_formula,
	sanitize_cell,
)
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


class TestImportSanitizer(unittest.TestCase):
	"""The pure sanitizer (SEC-08)."""

	def test_a_formula_is_neutralized(self):
		for raw in ("=1+1", "+1", "-1+1", "@SUM(A1)"):
			with self.subTest(raw=raw):
				self.assertTrue(looks_like_formula(raw))
				self.assertTrue(sanitize_cell(raw).startswith("'"))

	def test_html_is_removed(self):
		self.assertEqual(sanitize_cell("<script>alert(1)</script>"), "alert(1)")

	def test_a_plain_value_is_untouched(self):
		self.assertEqual(sanitize_cell("0912345678"), "0912345678")
		self.assertEqual(sanitize_cell(""), "")
		self.assertEqual(sanitize_cell(None), "")


class TestImportPreview(P1TestCase):
	"""The preview step of the pipeline."""

	def upload(self, content, file_name="import.csv", is_private=1):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"is_private": is_private,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": content,
			}
		)
		file_doc.insert(ignore_permissions=True)
		return file_doc.name

	def test_a_public_file_is_rejected(self):
		file_name = self.upload(build_csv([("0911111001",)]), file_name="public.csv", is_private=0)
		with self.assertRaises(InvalidRequest):
			create_preview(file_name, actor=self.admin_user)

	def test_an_unsupported_format_is_rejected(self):
		file_name = self.upload(b"whatever", file_name="import.txt")
		with self.assertRaises(UnsupportedFormat):
			create_preview(file_name, actor=self.admin_user)

	def test_the_preview_classifies_every_row(self):
		existing = self.make_canonical_lead()
		existing_phone = frappe.db.get_value("CRM Lead", existing, "mobile_no")
		new_phone = self.next_phone()
		file_name = self.upload(
			build_csv(
				[
					(new_phone,),
					(new_phone,),  # duplicate in file
					(existing_phone,),  # duplicate in CRM
					("09abc",),  # invalid
					("",),  # empty
				]
			)
		)
		response = create_preview(
			file_name, actor=self.admin_user, idempotency_key=self.unique_key("preview-class")
		)
		self.assertEqual(response["status"], "DRY_RUN")
		self.assertEqual(response["totals"]["total_rows"], 4)
		self.assertEqual(response["totals"]["valid_rows"], 1)
		self.assertEqual(response["totals"]["duplicate_rows"], 2)
		self.assertEqual(response["totals"]["error_rows"], 1)

		classes = {row["class"] for row in response["preview"]}
		self.assertIn("valid", classes)
		self.assertIn("duplicate_in_file", classes)
		self.assertIn("duplicate_in_crm", classes)
		self.assertIn("invalid", classes)

	def test_the_preview_never_creates_a_lead(self):
		file_name = self.upload(build_csv([(self.next_phone(),)]))
		before = frappe.db.count("CRM Lead")
		response = create_preview(
			file_name, actor=self.admin_user, idempotency_key=self.unique_key("preview-nolead")
		)
		self.assertEqual(response["totals"]["new_rows"], 1)
		self.assertEqual(frappe.db.count("CRM Lead"), before)

	def test_the_same_file_and_key_returns_the_same_batch(self):
		key = self.unique_key("same-key")
		file_name = self.upload(build_csv([(self.next_phone(),)]))
		first = create_preview(file_name, actor=self.admin_user, idempotency_key=key)
		second = create_preview(file_name, actor=self.admin_user, idempotency_key=key)
		self.assertEqual(first["batch"], second["batch"])
		self.assertEqual(frappe.db.count("CSKH Import Batch", {"idempotency_key": key}), 1)

	def test_the_same_file_with_a_different_key_creates_a_second_batch(self):
		file_name = self.upload(build_csv([(self.next_phone(),)]))
		first = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("diff-a"))
		second = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("diff-b"))
		self.assertNotEqual(first["batch"], second["batch"])

	def test_the_row_limit_is_enforced(self):
		rows = [(f"0912{i:06d}",) for i in range(20)]
		file_name = self.upload(build_csv(rows))
		original = import_service._settings
		import_service._settings = lambda: {"max_import_file_bytes": 10 * 1024 * 1024, "max_import_rows": 5}
		self.addCleanup(setattr, import_service, "_settings", original)
		with self.assertRaises(TooManyRows):
			create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("limit"))

	def test_an_xlsx_file_is_parsed(self):
		openpyxl = __import__("openpyxl")
		workbook = openpyxl.Workbook()
		sheet = workbook.active
		sheet.append(["Số điện thoại"])
		sheet.append([self.next_phone()])
		sheet.append([None])  # an empty cell is skipped
		sheet.append([self.next_phone()])
		buffer = io.BytesIO()
		workbook.save(buffer)
		file_name = self.upload(buffer.getvalue(), file_name="import.xlsx")
		response = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("xlsx"))
		self.assertEqual(response["totals"]["total_rows"], 2)
		self.assertEqual(response["totals"]["valid_rows"], 2)


class TestImportWorker(P1TestCase):
	"""The worker that commits a confirmed batch."""

	def upload(self, content, file_name="worker.csv"):
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

	def queue(self, batch_name):
		"""Confirm the preview once via the real service (`DRY_RUN -> CONFIRMED -> QUEUED`)."""
		with mock.patch("frappe.enqueue") as mocked_enqueue:
			response = confirm(batch_name, actor=self.admin_user)
			self.assertEqual(response["status"], "QUEUED")
			mocked_enqueue.assert_called_once()
			kwargs = mocked_enqueue.call_args.kwargs
			self.assertTrue(kwargs.get("enqueue_after_commit"))
			self.assertTrue(kwargs.get("deduplicate"))
			self.assertEqual(kwargs.get("batch_name"), batch_name)
		return frappe.get_doc("CSKH Import Batch", batch_name)

	def test_the_worker_creates_the_canonical_leads(self):
		phone_a = self.unique_phone()
		phone_b = self.unique_phone()
		file_name = self.upload(build_csv([(phone_a,), (phone_b,)]))
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("worker-1"))
		self.queue(batch["batch"])
		import_worker.run_batch(batch["batch"])

		batch_doc = frappe.get_doc("CSKH Import Batch", batch["batch"])
		self.assertEqual(batch_doc.status, "COMPLETED")
		self.assertEqual(batch_doc.created_rows, 2)
		self.assertEqual(batch_doc.error_rows, 0)

		canonical = normalize_vn_phone(phone_a)
		lead = identity_service.find_by_phone(canonical)
		self.assertTrue(lead)
		self.assertEqual(normalize_vn_phone(frappe.db.get_value("CRM Lead", lead, "mobile_no")), canonical)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "cskh_is_placeholder_name"), 1)
		self.assertEqual(
			frappe.db.get_value("CRM Lead", lead, "first_name"), import_worker.PLACEHOLDER_FIRST_NAME
		)

	def test_the_worker_is_idempotent(self):
		phone = self.unique_phone()
		file_name = self.upload(build_csv([(phone,)]))
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("worker-2"))
		self.queue(batch["batch"])
		import_worker.run_batch(batch["batch"])
		import_worker.run_batch(batch["batch"])
		self.assertEqual(
			frappe.db.count("CSKH Identity Index", {"normalized_phone": normalize_vn_phone(phone)}),
			1,
		)
		self.assertEqual(
			frappe.db.count("CRM Lead", {"mobile_no": ["in", (phone, "+84" + phone[1:])]}),
			1,
		)

	def test_an_existing_lead_is_reused_and_not_modified(self):
		phone = self.unique_phone()
		lead = self.make_lead(phone=phone, owner=self.staff_a)
		frappe.db.set_value("CRM Lead", lead, "status", "Converted")
		file_name = self.upload(build_csv([(phone,)]))
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("worker-3"))
		self.queue(batch["batch"])
		import_worker.run_batch(batch["batch"])

		batch_doc = frappe.get_doc("CSKH Import Batch", batch["batch"])
		self.assertEqual(batch_doc.reused_rows, 1)
		self.assertEqual(batch_doc.created_rows, 0)
		self.assertEqual(frappe.db.count("CRM Lead", {"mobile_no": phone}), 1)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "lead_owner"), self.staff_a)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), "Converted")

	def test_the_failed_rows_are_written_into_a_private_artifact(self):
		phone = self.unique_phone()
		file_name = self.upload(build_csv([(phone,), ("09abc",)]))
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("worker-4"))
		self.queue(batch["batch"])
		import_worker.run_batch(batch["batch"])

		batch_doc = frappe.get_doc("CSKH Import Batch", batch["batch"])
		self.assertEqual(batch_doc.status, "COMPLETED_WITH_ERRORS")
		self.assertTrue(batch_doc.error_artifact)
		artifact = frappe.get_doc("File", batch_doc.error_artifact)
		self.assertEqual(artifact.is_private, 1)

	def test_a_confirmed_batch_can_be_retried(self):
		phone = self.unique_phone()
		file_name = self.upload(build_csv([(phone,)]))
		batch = create_preview(file_name, actor=self.admin_user, idempotency_key=self.unique_key("worker-5"))
		batch_doc = frappe.get_doc("CSKH Import Batch", batch["batch"])
		batch_doc.status = "FAILED"
		batch_doc.save(ignore_permissions=True)

		with mock.patch("frappe.enqueue"):
			response = confirm(batch["batch"], actor=self.admin_user)
		self.assertEqual(response["status"], "QUEUED")
		import_worker.run_batch(batch["batch"])
		self.assertEqual(frappe.db.get_value("CSKH Import Batch", batch["batch"], "status"), "COMPLETED")

	def test_over_200_rows_imports_every_valid_row_and_records_every_error(self):
		valid_phones = [self.unique_phone() for _ in range(245)]
		rows = []
		valid_idx = 0
		invalid_row_numbers = set()
		# Total 250 CSV data rows (row_number 2..251): 3 invalid <= 200, 2 invalid > 200.
		for csv_idx in range(1, 251):
			row_number = csv_idx + 1
			if row_number in (15, 90, 180, 220, 251):
				rows.append((f"bad-phone-{row_number}",))
				invalid_row_numbers.add(str(row_number))
			else:
				rows.append((valid_phones[valid_idx],))
				valid_idx += 1

		file_name = self.upload(build_csv(rows), file_name="worker-250.csv")
		preview = create_preview(
			file_name, actor=self.admin_user, idempotency_key=self.unique_key("worker-250")
		)
		self.assertTrue(preview["preview_truncated"])
		self.assertEqual(len(preview["preview"]), 200)
		self.assertEqual(preview["totals"]["total_rows"], 250)
		self.assertEqual(preview["totals"]["valid_rows"], 245)
		self.assertEqual(preview["totals"]["error_rows"], 5)

		self.queue(preview["batch"])
		import_worker.run_batch(preview["batch"])

		batch_doc = frappe.get_doc("CSKH Import Batch", preview["batch"])
		self.assertEqual(batch_doc.status, "COMPLETED_WITH_ERRORS")
		self.assertEqual(int(batch_doc.created_rows), 245)
		self.assertEqual(int(batch_doc.error_rows), 5)
		self.assertTrue(batch_doc.error_artifact)

		canonicals = [normalize_vn_phone(p) for p in valid_phones]
		self.assertEqual(
			frappe.db.count("CSKH Identity Index", {"normalized_phone": ["in", canonicals]}),
			245,
		)

		artifact = frappe.get_doc("File", batch_doc.error_artifact)
		self.assertEqual(artifact.is_private, 1)
		raw = artifact.get_content()
		if isinstance(raw, bytes):
			raw = raw.decode("utf-8")
		parsed = list(csv.DictReader(io.StringIO(raw)))
		self.assertEqual(len(parsed), 5)
		self.assertEqual({entry["row_number"] for entry in parsed}, invalid_row_numbers)
