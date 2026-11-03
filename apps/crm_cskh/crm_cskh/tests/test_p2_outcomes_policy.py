"""P2 Outcome Catalog, Operational Policy Inheritance, Snapshots & Vietnamese Audit (`T2-02`, `T2-03`, `T2-04`, `T2-18`, `T2-24`)."""

from __future__ import annotations

import frappe

from crm_cskh.p2_models import DEFAULT_OUTCOME_CODES, UNREACHED_DISCONNECTED_CODES
from crm_cskh.services import contact_attempt, policies, read_model
from crm_cskh.services.errors import (
	FollowupRequired,
	Forbidden,
	InvalidChannel,
	InvalidOutcome,
	InvalidRequest,
	NoteRequired,
	OutcomeNotCompatible,
	PolicyChanged,
)
from crm_cskh.tests.p2_base import P2TestCase


class TestP2OutcomesAndChannelSemantics(P2TestCase):
	"""`T2-02`, `T2-03`, `T2-04`, `T2-24`: 12 Vietnamese Outcome Codes, `is_connected` integrity, and channel rules."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=100)

	def test_seeded_vietnamese_outcome_catalog_and_unreached_invariant(self):
		"""`T2-02`, `T2-24`: All 12 seeded codes exist in Vietnamese and unreached codes never count as connected."""
		cat = policies.get_outcome_codes_for_workflow(actor=self.staff_a)
		by_code = {item["code"]: item for item in cat["items"]}
		expected_codes = {row["code"] for row in DEFAULT_OUTCOME_CODES}
		self.assertTrue(expected_codes.issubset(set(by_code.keys())))

		for code in expected_codes:
			item = by_code[code]
			self.assertTrue(item["label_vi"].strip(), f"missing Vietnamese label for {code}")

		for unreached in UNREACHED_DISCONNECTED_CODES:
			self.assertFalse(by_code[unreached]["is_connected"], f"{unreached} must not be connected")

		# Admin cannot corrupt `no_answer` into `is_connected=True` (`G2-06`)
		with self.assertRaises(OutcomeNotCompatible):
			policies.save_outcome(
				code="no_answer",
				label_vi="Không bắt máy",
				result_group="unreached",
				is_connected=True,
				idempotency_key=self.unique_key("bad-out"),
				actor=self.admin_user,
			)

	def test_unreached_vs_connected_outcomes_and_mandatory_fields(self):
		"""`T2-02`, `T2-03`, `T2-04`: `no_answer`/`busy`/`message_sent_no_reply` vs `connected_interested`, `NOTE_REQUIRED`, `FOLLOWUP_REQUIRED`."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)

		for idx, (ch, code) in enumerate(
			[
				("phone_call", "no_answer"),
				("phone_call", "busy"),
				("phone_call", "unreachable"),
				("zalo", "message_sent_no_reply"),
			],
			start=1,
		):
			r = contact_attempt.record_attempt(
				work_item=wi,
				channel=ch,
				outcome_code=code,
				note=f"Thử liên hệ lần {idx}",
				idempotency_key=self.unique_key(f"unr-{idx}"),
				actor=self.staff_a,
			)
			self.assertEqual(r["attempt_count"], idx)
			self.assertEqual(r["connected_attempt_count"], 0)
			self.assertEqual(r["work_item_status"], "in_progress")

		# `other` requires non-empty `note`
		with self.assertRaises(NoteRequired):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="other",
				outcome_code="other",
				note="   ",
				idempotency_key=self.unique_key("other-nonote"),
				actor=self.staff_a,
			)

		# `callback_requested` requires `followup.due_at` when no open callback exists
		with self.assertRaises(FollowupRequired):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="callback_requested",
				note="Khách hẹn gọi lại nhưng quên chọn giờ",
				idempotency_key=self.unique_key("cb-nofu"),
				actor=self.staff_a,
			)

		# `wrong_number` (`allows_followup=False`) rejects `followup.due_at`
		with self.assertRaises(OutcomeNotCompatible):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="wrong_number",
				note="Sai số nhưng cố đặt lịch hẹn",
				followup={"due_at": self.future_due_str(hours=2)},
				idempotency_key=self.unique_key("wn-fu"),
				actor=self.staff_a,
			)

		# `connected_interested` increments `connected_attempt_count` to 1 without closing Work Item
		ok_conn = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Khách nghe máy và quan tâm gói nâng cao",
			idempotency_key=self.unique_key("conn-1"),
			actor=self.staff_a,
		)
		self.assertEqual(ok_conn["attempt_count"], 5)
		self.assertEqual(ok_conn["connected_attempt_count"], 1)
		self.assertEqual(ok_conn["work_item_status"], "in_progress")

	def test_channel_compatibility_and_policy_enabled_channels(self):
		"""`T2-04`: Rejects incompatible channel/outcome pairs and channels disabled by Policy."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)

		with self.assertRaises(InvalidChannel):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="fax",
				outcome_code="other",
				note="Kênh không tồn tại",
				idempotency_key=self.unique_key("bad-ch"),
				actor=self.staff_a,
			)

		# `message_sent_no_reply` is text-only (`sms,zalo,facebook,other`), NOT `phone_call`
		with self.assertRaises(OutcomeNotCompatible):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="message_sent_no_reply",
				note="Gọi điện nhưng chọn đã nhắn tin",
				idempotency_key=self.unique_key("incompat-1"),
				actor=self.staff_a,
			)

		# `no_answer` is `phone_call` only, NOT `sms`
		with self.assertRaises(OutcomeNotCompatible):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="sms",
				outcome_code="no_answer",
				note="SMS nhưng chọn không bắt máy",
				idempotency_key=self.unique_key("incompat-2"),
				actor=self.staff_a,
			)


class TestP2PolicyInheritanceAndSnapshotVersioning(P2TestCase):
	"""`T2-18`, `SEC2-11`: `Campaign > Team > Global` inheritance, `POLICY_CHANGED`, and historical snapshot immutability."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=100)

	def test_campaign_overrides_team_overrides_global_policy_and_guards_stale_policy_version(self):
		"""`T2-18`: Resolves `Global -> Team -> Campaign` precedence and enforces `expected_policy_version`."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)

		# Staff / Leader cannot mutate Policy
		with self.assertRaises(Forbidden):
			policies.save_policy(
				policy_code="HACK-POL",
				title="Trái phép",
				scope_type="Global",
				idempotency_key=self.unique_key("hack-pol"),
				actor=self.leader_a,
			)

		# Initially resolves `GLOBAL-DEFAULT`
		p_global = policies.resolve_effective_policy(work_item=wi)
		self.assertEqual(p_global["scope_type"], "Global")

		# Create Team A policy -> overrides Global
		team_pol_code = f"POL-TEAM-{frappe.generate_hash(length=6)}"
		policies.save_policy(
			policy_code=team_pol_code,
			title="Chính sách Team A",
			scope_type="Team",
			team=self.team_a,
			allowed_backdate_minutes=60,
			enabled_channels="phone_call,zalo",
			idempotency_key=self.unique_key("team-pol"),
			actor=self.admin_user,
		)
		p_team = policies.resolve_effective_policy(work_item=wi)
		self.assertEqual(p_team["policy_code"], team_pol_code)
		self.assertEqual(p_team["allowed_backdate_minutes"], 60)

		# Create Campaign policy -> overrides Team A
		camp_pol_code = f"POL-CAMP-{frappe.generate_hash(length=6)}"
		policies.save_policy(
			policy_code=camp_pol_code,
			title="Chính sách Chiến dịch riêng",
			scope_type="Campaign",
			campaign=self.campaign,
			allowed_backdate_minutes=30,
			enabled_channels="phone_call",
			idempotency_key=self.unique_key("camp-pol-v1"),
			actor=self.admin_user,
		)
		p_camp_v1 = policies.resolve_effective_policy(work_item=wi)
		self.assertEqual(p_camp_v1["policy_code"], camp_pol_code)
		self.assertEqual(p_camp_v1["version"], 1)
		self.assertEqual(p_camp_v1["enabled_channels"], ["phone_call"])

		# Zalo is disabled in Campaign policy -> `InvalidChannel`
		with self.assertRaises(InvalidChannel):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="zalo",
				outcome_code="other",
				note="Thử kênh bị tắt theo chiến dịch",
				idempotency_key=self.unique_key("zalo-blocked"),
				actor=self.staff_a,
			)

		# Update Campaign policy -> `version` bumps to 2; caller passing `expected_policy_version=1` gets `409 POLICY_CHANGED`
		policies.save_policy(
			policy_code=camp_pol_code,
			title="Chính sách Chiến dịch v2",
			scope_type="Campaign",
			campaign=self.campaign,
			allowed_backdate_minutes=45,
			enabled_channels="phone_call,zalo",
			idempotency_key=self.unique_key("camp-pol-v2"),
			actor=self.admin_user,
		)
		with self.assertRaises(PolicyChanged):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="no_answer",
				expected_policy_version=1,
				idempotency_key=self.unique_key("stale-pol"),
				actor=self.staff_a,
			)

	def test_outcome_rename_or_disable_never_drifts_historical_attempt_snapshots(self):
		"""`T2-18`: Custom Outcome v1 snapshot stays frozen on historical attempt after rename (v2) and disable (v3)."""
		custom_code = f"custom_p2_{frappe.generate_hash(length=6)}".lower()
		policies.save_outcome(
			code=custom_code,
			label_vi="Khách hẹn gửi hồ sơ v1",
			result_group="positive",
			is_contact_attempt=True,
			is_connected=True,
			requires_note=True,
			allowed_channels="phone_call,zalo",
			idempotency_key=self.unique_key("out-v1"),
			actor=self.admin_user,
		)

		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		rec = contact_attempt.record_attempt(
			work_item=wi,
			channel="zalo",
			outcome_code=custom_code,
			note="Khách chụp CCCD gửi qua Zalo",
			idempotency_key=self.unique_key("att-snap"),
			actor=self.staff_a,
		)

		# Admin renames label (`version` -> 2) and then disables outcome (`version` -> 3)
		v2 = policies.save_outcome(
			code=custom_code,
			label_vi="Khách bổ sung chứng từ v2",
			result_group="positive",
			is_contact_attempt=True,
			is_connected=True,
			requires_note=True,
			allowed_channels="phone_call,zalo",
			enabled=True,
			idempotency_key=self.unique_key("out-v2"),
			actor=self.admin_user,
		)
		self.assertEqual(v2["outcome"]["version"], 2)

		v3 = policies.save_outcome(
			code=custom_code,
			label_vi="Khách bổ sung chứng từ v2",
			result_group="positive",
			is_contact_attempt=True,
			is_connected=True,
			requires_note=True,
			allowed_channels="phone_call,zalo",
			enabled=False,
			idempotency_key=self.unique_key("out-v3"),
			actor=self.admin_user,
		)
		self.assertEqual(v3["outcome"]["version"], 3)

		# Disabled outcome is rejected on new attempts
		with self.assertRaises(InvalidOutcome):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="zalo",
				outcome_code=custom_code,
				note="Thử ghi mã đã tắt",
				idempotency_key=self.unique_key("att-disabled"),
				actor=self.staff_a,
			)

		# Historical attempt timeline still displays exact v1 snapshot!
		timeline = read_model.list_attempts_timeline(wi, actor=self.staff_a)
		att_entry = next(x for x in timeline["items"] if x["attempt_id"] == rec["attempt_id"])
		self.assertEqual(att_entry["original"]["outcome_label"], "Khách hẹn gửi hồ sơ v1")
		self.assertEqual(att_entry["original"]["outcome_version"], 1)
		self.assertEqual(att_entry["effective"]["outcome_label"], "Khách hẹn gửi hồ sơ v1")
