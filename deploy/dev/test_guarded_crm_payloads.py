"""Executable redaction regressions; separate from the 99-method site inventory."""

import copy
import unittest
from unittest.mock import patch

from crm_cskh.api import guarded_crm


class PrivateRecordingURLTests(unittest.TestCase):
	def filter_call(self, recording_url):
		payload = {
			"calls": [
				{
					"name": "TEST-CALL",
					"reference_doctype": "CRM Lead",
					"reference_docname": "TEST-LEAD",
					"recording_url": recording_url,
				}
			],
			"notes": [],
			"tasks": [],
		}
		original = copy.deepcopy(payload)
		with patch.object(guarded_crm, "_link_target_is_readable", return_value=True):
			result = guarded_crm._filter_linked_calls_for_scope(payload)
		self.assertEqual(payload, original, "Filtering must not mutate the upstream payload")
		self.assertEqual(len(result["calls"]), 1, "An in-scope call must remain visible")
		return result["calls"][0]

	def test_relative_private_recording_is_removed(self):
		self.assertNotIn("recording_url", self.filter_call("/private/files/recording.mp3"))

	def test_absolute_private_recordings_are_removed(self):
		for url in (
			"http://dev.localhost:8091/private/files/recording.mp3",
			"https://crm.example.test/private/files/recording.mp3?download=1",
		):
			with self.subTest(url=url):
				self.assertNotIn("recording_url", self.filter_call(url))

	def test_protocol_relative_and_case_insensitive_private_paths_are_removed(self):
		for url in (
			"//crm.example.test/private/files/recording.mp3",
			"https://crm.example.test/PRIVATE/FILES/recording.mp3",
		):
			with self.subTest(url=url):
				self.assertNotIn("recording_url", self.filter_call(url))

	def test_public_and_external_recordings_are_preserved(self):
		for url in ("/files/public.mp3", "https://media.example.test/recording.mp3"):
			with self.subTest(url=url):
				self.assertEqual(self.filter_call(url)["recording_url"], url)

	def test_out_of_scope_calls_remain_denied(self):
		with patch.object(guarded_crm, "_link_target_is_readable", return_value=False):
			result = guarded_crm._filter_linked_calls_for_scope(
				{
					"calls": [
						{
							"reference_doctype": "CRM Lead",
							"reference_docname": "OUTSIDE-SCOPE",
							"recording_url": "https://crm.example.test/private/files/private.mp3",
						}
					],
				}
			)
		self.assertEqual(result, {"calls": [], "notes": [], "tasks": []})

	def test_orphan_and_dynamic_link_calls_remain_denied(self):
		with patch.object(guarded_crm, "_link_target_is_readable", return_value=True):
			result = guarded_crm._filter_linked_calls_for_scope(
				{
					"calls": [
						{"name": "ORPHAN"},
						{
							"reference_doctype": "CRM Lead",
							"reference_docname": "TEST-LEAD",
							"link_doctype": "CRM Lead",
							"link_name": "OTHER-LEAD",
						},
					],
				}
			)
		self.assertEqual(result, {"calls": [], "notes": [], "tasks": []})


if __name__ == "__main__":
	unittest.main()
