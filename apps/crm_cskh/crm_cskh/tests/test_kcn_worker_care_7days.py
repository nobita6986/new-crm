"""Unit & service tests for KCN Marketing Self-Sourced Worker Intake & 7-Workday Care Lifecycle.

Runs standalone via `python -m unittest apps/crm_cskh/crm_cskh/tests/test_kcn_worker_care_7days.py`
(installing lightweight in-memory Frappe stubs when executed outside a live bench) as well as inside
`bench run-tests`.
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

# Ensure `apps/crm_cskh` is importable when executed directly via `python -m unittest`
_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _APP_ROOT not in sys.path:
	sys.path.insert(0, _APP_ROOT)


def _ensure_frappe_stub_if_missing() -> None:
	"""Provide a minimal in-memory Frappe stub if `frappe` is not installed in the host Python."""
	try:
		import frappe

		return
	except ImportError:
		pass

	class _Dict(dict):
		def __getattr__(self, key):
			try:
				return self[key]
			except KeyError as exc:
				raise AttributeError(key) from exc

		def __setattr__(self, key, value):
			self[key] = value

	frappe_mod = types.ModuleType("frappe")
	frappe_mod._dict = _Dict
	frappe_mod._ = lambda msg, lang=None: msg

	class PermissionError(Exception):
		pass

	class ValidationError(Exception):
		pass

	class DuplicateEntryError(Exception):
		pass

	frappe_mod.PermissionError = PermissionError
	frappe_mod.ValidationError = ValidationError
	frappe_mod.DuplicateEntryError = DuplicateEntryError
	frappe_mod.session = _Dict(user="Administrator", sid="sid-test")
	frappe_mod.local = types.SimpleNamespace(
		lang="vi",
		form_dict=_Dict(),
		request=types.SimpleNamespace(method="POST"),
		session=types.SimpleNamespace(data=types.SimpleNamespace(csrf_token="csrf-test")),
	)
	frappe_mod.response = _Dict()
	frappe_mod.conf = _Dict()

	def _whitelist(*_args, **_kwargs):
		def _wrap(fn):
			return fn

		return _wrap

	frappe_mod.whitelist = _whitelist
	frappe_mod.generate_hash = lambda length=10: "h" * length

	def _throw(msg, exc=ValidationError):
		raise exc(msg)

	frappe_mod.throw = _throw
	frappe_mod.get_roles = lambda user=None: ["Sales User"]

	utils_mod = types.ModuleType("frappe.utils")
	utils_mod.cint = lambda v: int(v or 0)
	utils_mod.flt = lambda v: float(v or 0.0)
	utils_mod.now_datetime = lambda: datetime(2026, 11, 28, 17, 0, 0)
	utils_mod.get_datetime = lambda v: (
		v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace(" ", "T")) if v else None
	)
	utils_mod.getdate = lambda v=None: (
		v.date()
		if isinstance(v, datetime)
		else v
		if isinstance(v, date)
		else date.fromisoformat(str(v)[:10])
		if v
		else date(2026, 11, 28)
	)
	utils_mod.add_to_date = lambda dt, days=0, hours=0, minutes=0, seconds=0, **_kw: (
		(dt or utils_mod.now_datetime()) + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
	)
	utils_mod.get_system_timezone = lambda: "Asia/Ho_Chi_Minh"
	frappe_mod.utils = utils_mod

	sessions_mod = types.ModuleType("frappe.sessions")
	sessions_mod.get_csrf_token = lambda: "csrf-test"
	frappe_mod.sessions = sessions_mod

	model_mod = types.ModuleType("frappe.model")
	doc_mod = types.ModuleType("frappe.model.document")

	class Document:
		def __init__(self, *args, **kwargs):
			pass

	doc_mod.Document = Document
	model_mod.document = doc_mod
	frappe_mod.model = model_mod

	sys.modules["frappe"] = frappe_mod
	sys.modules["frappe.utils"] = utils_mod
	sys.modules["frappe.sessions"] = sessions_mod
	sys.modules["frappe.model"] = model_mod
	sys.modules["frappe.model.document"] = doc_mod


_ensure_frappe_stub_if_missing()

import frappe

from crm_cskh.services import runtime_cache, worker_intake, worker_lifecycle
from crm_cskh.services.errors import Conflict, Forbidden, OwnershipChanged
from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone
from crm_cskh.www import cskh_context


class _InMemoryFrappeEnv:
	"""Self-contained fake DB & Doc store exercising `worker_intake` and `worker_lifecycle` logic."""

	def __init__(self):
		self.seq = 0
		self.now = datetime(2026, 11, 28, 17, 0, 0)
		self.roles_by_user = {
			"Administrator": {"System Manager", "CSKH CRM Admin"},
			"admin@kcn.vn": {"CSKH CRM Admin"},
			"bod@kcn.vn": {"CSKH BOD Viewer"},
			"leader@kcn.vn": {"Sales Manager", "Sales User"},
			"mkt.a@kcn.vn": {"Sales User"},
			"mkt.b@kcn.vn": {"Sales User"},
		}
		self.users = {u: frappe._dict(name=u, enabled=1, full_name=f"Name {u}") for u in self.roles_by_user}
		self.tables: dict[str, dict[str, frappe._dict]] = {
			"User": self.users,
			"CRM Lead": {},
			"CSKH Identity Index": {},
			"CSKH Campaign": {},
			"CSKH Work Item": {},
			"CSKH Active Claim": {},
			"CSKH Assignment History": {},
			"CSKH Contact Attempt": {},
			"ToDo": {},
			"DocShare": {},
		}
		self.columns = {
			"CRM Lead": {
				"name",
				"first_name",
				"lead_name",
				"mobile_no",
				"source",
				"status",
				"lead_owner",
				"cskh_is_placeholder_name",
				"cskh_do_not_contact",
				"cskh_birth_year",
				"cskh_gender",
				"cskh_current_area",
				"cskh_id_card_status",
				"cskh_worker_tags",
				"cskh_zalo_phone",
			},
			"CSKH Campaign": {
				"name",
				"code",
				"title",
				"status",
				"team_scope",
				"rule_version",
				"factory_name",
				"industrial_zone",
				"recruitment_criteria",
				"target_workdays",
				"allow_self_sourced",
			},
			"CSKH Work Item": {
				"name",
				"campaign",
				"lead",
				"status",
				"revision",
				"assigned_to",
				"assigned_at",
				"attempt_count",
				"connected_attempt_count",
				"open_callback_task",
				"owner_at_close",
				"closed_at",
				"closed_by",
				"completion_reason",
				"source_type",
				"acquisition_channel",
				"target_factory",
				"target_industrial_zone",
				"worker_stage",
				"interview_date",
				"onboard_date",
				"workdays_completed",
				"target_workdays",
				"last_workday_logged_on",
				"completed_7d_at",
				"dropout_reason",
				"last_activity_by",
			},
		}

	def next_id(self, prefix: str) -> str:
		self.seq += 1
		return f"{prefix}-{self.seq:04d}"

	def has_column(self, doctype: str, col: str) -> bool:
		return col in self.columns.get(doctype, set())

	def exists(self, doctype: str, filters) -> str | None:
		row = self.get_value(doctype, filters, "name")
		return row if isinstance(row, str) else None

	def _match(self, doc: dict, filters) -> bool:
		if isinstance(filters, str):
			return doc.get("name") == filters
		if isinstance(filters, dict):
			for k, cond in filters.items():
				val = doc.get(k)
				if isinstance(cond, list) and len(cond) == 2:
					op, target = cond[0].lower(), cond[1]
					if op == "in" and val not in target:
						return False
					if op == "!=" and val == target:
						return False
				elif val != cond:
					return False
			return True
		return False

	def get_value(self, doctype: str, filters, fieldname="name", as_dict=False):
		store = self.tables.get(doctype, {})
		matched = None
		for doc in store.values():
			if self._match(doc, filters):
				matched = doc
				break
		if not matched:
			return None
		if isinstance(fieldname, (list, tuple)):
			out = frappe._dict({f: matched.get(f) for f in fieldname})
			return out if as_dict else tuple(out.values())
		if as_dict:
			return frappe._dict({fieldname: matched.get(fieldname)})
		return matched.get(fieldname)

	def delete(self, doctype: str, filters=None):
		store = self.tables.get(doctype, {})
		for k in [k for k, doc in store.items() if filters is None or self._match(doc, filters)]:
			del store[k]

	def set_value(self, doctype: str, name: str, fieldname, value=None, **_kwargs):
		doc = self.tables[doctype][name]
		if isinstance(fieldname, dict):
			doc.update(fieldname)
		else:
			doc[fieldname] = value
		doc["modified"] = self.now

	def get_list(self, doctype: str, filters=None, fields=None, **_kwargs):
		out = []
		for doc in self.tables.get(doctype, {}).values():
			if filters is None or self._match(doc, filters):
				if fields:
					out.append(frappe._dict({f: doc.get(f) for f in fields}))
				else:
					out.append(frappe._dict(doc))
		return out

	def make_doc(self, payload: dict):
		env = self

		class _FakeDoc(frappe._dict):
			def insert(self, ignore_permissions=False):
				dt = self["doctype"]
				if dt == "CSKH Identity Index":
					key_val = self.get("phone_e164") or self.get("normalized_phone") or self.get("phone")
					for ex in env.tables[dt].values():
						ex_key = ex.get("phone_e164") or ex.get("normalized_phone") or ex.get("phone")
						if key_val and ex_key == key_val:
							raise frappe.DuplicateEntryError("Duplicate phone_e164")
				if dt == "CSKH Active Claim":
					for ex in env.tables[dt].values():
						if ex.get("lead") == self.get("lead"):
							raise frappe.DuplicateEntryError("Duplicate active claim")
				if not self.get("name"):
					prefix = dt.upper().replace(" ", "-")
					self["name"] = env.next_id(prefix)
				self.setdefault("creation", env.now)
				self.setdefault("modified", env.now)
				env.tables.setdefault(dt, {})[self["name"]] = self
				return self

			def save(self, ignore_permissions=False):
				dt = self["doctype"]
				self["modified"] = env.now
				env.tables.setdefault(dt, {})[self["name"]] = self
				return self

		return _FakeDoc(payload)


class TestKcnWorkerCare7Days(unittest.TestCase):
	def setUp(self):
		self.env = _InMemoryFrappeEnv()
		self.patchers = []

		fake_db = types.SimpleNamespace(
			table_exists=lambda dt: dt in self.env.tables,
			has_column=self.env.has_column,
			exists=self.env.exists,
			get_value=self.env.get_value,
			get_all=self.env.get_list,
			set_value=self.env.set_value,
			delete=self.env.delete,
			savepoint=lambda name: name,
			rollback=lambda **_kw: None,
			commit=lambda: None,
			sql=self._fake_sql,
		)
		self.patchers.append(mock.patch.object(frappe, "db", fake_db, create=True))
		self.patchers.append(
			mock.patch.object(
				frappe, "session", frappe._dict(user="Administrator", sid="sid-test"), create=True
			)
		)
		self.patchers.append(mock.patch.object(frappe, "conf", frappe._dict(), create=True))
		self.patchers.append(mock.patch.object(frappe, "get_list", self.env.get_list, create=True))
		self.patchers.append(
			mock.patch.object(
				frappe,
				"get_doc",
				lambda arg1, arg2=None: (
					self.env.make_doc(arg1) if isinstance(arg1, dict) else self.env.tables[arg1][arg2]
				),
				create=True,
			)
		)
		self.patchers.append(
			mock.patch.object(
				frappe,
				"get_roles",
				lambda user=None: list(self.env.roles_by_user.get(user or "Guest", set())),
				create=True,
			)
		)
		self.patchers.append(
			mock.patch.object(
				runtime_cache,
				"get_roles_set",
				lambda user=None: set(self.env.roles_by_user.get(user or "Guest", set())),
			)
		)
		self.patchers.append(mock.patch.object(runtime_cache, "bump_cache_domain", lambda *_a, **_kw: None))
		self.patchers.append(
			mock.patch.object(runtime_cache, "get_cached_system_timezone", lambda: "Asia/Ho_Chi_Minh")
		)
		self.patchers.append(mock.patch.object(worker_intake, "now_datetime", lambda: self.env.now))
		self.patchers.append(mock.patch.object(worker_lifecycle, "now_datetime", lambda: self.env.now))
		# Stub ownership helpers that execute raw SQL in production
		self.patchers.append(
			mock.patch.object(
				worker_intake.ownership,
				"_sync_lead_owner",
				lambda lead, user: self.env.set_value("CRM Lead", lead, "lead_owner", user),
			)
		)
		self.patchers.append(
			mock.patch.object(worker_intake.ownership, "_cancel_stale_lead_todos", lambda *_a, **_kw: None)
		)
		self.patchers.append(
			mock.patch.object(worker_intake.ownership, "_sync_docshares", lambda *_a, **_kw: None)
		)
		self.patchers.append(mock.patch.object(worker_intake.ownership, "_todo_for", lambda *_a, **_kw: None))
		self.patchers.append(
			mock.patch.object(worker_intake.ownership, "_assert_final_owner", lambda *_a, **_kw: None)
		)
		self.patchers.append(
			mock.patch.object(worker_intake.ownership, "_close_open_interval", lambda *_a, **_kw: None)
		)
		self.patchers.append(
			mock.patch.object(
				worker_intake.ownership,
				"_find_history_event",
				lambda ev_id: self.env.get_value("CSKH Assignment History", {"event_id": ev_id}, "name"),
			)
		)
		self.patchers.append(
			mock.patch.object(
				worker_intake.ownership,
				"_create_todo",
				lambda lead, user, desc: self.env.make_doc(
					{"doctype": "ToDo", "reference_name": lead, "allocated_to": user, "description": desc}
				).insert(),
			)
		)
		self.patchers.append(
			mock.patch.object(
				worker_intake.ownership,
				"_insert_history",
				lambda payload: self.env.make_doc(payload).insert(),
			)
		)
		self.patchers.append(
			mock.patch.object(worker_lifecycle.contact_attempt, "record_attempt", self._fake_record_attempt)
		)

		for p in self.patchers:
			p.start()

	def tearDown(self):
		for p in reversed(self.patchers):
			p.stop()

	def _fake_record_attempt(
		self,
		*,
		work_item: str,
		channel: str,
		outcome_code: str,
		note: str | None = None,
		close_work_item: bool = False,
		completion_reason: str | None = None,
		actor: str | None = None,
		**_kwargs,
	) -> dict:
		wi = self.env.tables["CSKH Work Item"][work_item]
		att = self.env.make_doc(
			{
				"doctype": "CSKH Contact Attempt",
				"work_item": work_item,
				"channel": channel,
				"outcome_code": outcome_code,
				"note": note or "",
				"performed_by": actor,
			}
		).insert()
		wi["attempt_count"] = int(wi.get("attempt_count") or 0) + 1
		wi["connected_attempt_count"] = int(wi.get("connected_attempt_count") or 0) + 1
		wi["revision"] = int(wi.get("revision") or 1) + 1
		if close_work_item:
			old_owner = wi.get("assigned_to")
			wi["status"] = "closed"
			wi["owner_at_close"] = old_owner
			wi["closed_by"] = actor
			wi["closed_at"] = self.env.now
			wi["completion_reason"] = completion_reason
			wi["assigned_to"] = None
			for cname, cdoc in list(self.env.tables["CSKH Active Claim"].items()):
				if cdoc.get("work_item") == work_item:
					del self.env.tables["CSKH Active Claim"][cname]
		else:
			wi["status"] = "in_progress"
		worker_lifecycle.sync_stage_after_attempt_in_tx(
			work_item=work_item,
			outcome_code=outcome_code,
			should_close=bool(close_work_item),
			now_dt=self.env.now,
		)
		return {
			"status": "recorded",
			"idempotent_replay": False,
			"attempt_id": att.name,
			"work_item": work_item,
			"work_item_status": wi["status"],
			"revision": wi["revision"],
			"attempt_count": wi["attempt_count"],
			"connected_attempt_count": wi["connected_attempt_count"],
		}

	def _fake_sql(self, query: str, params=(), as_dict=False):
		q = " ".join(query.lower().split())
		wis = list(self.env.tables["CSKH Work Item"].values())

		# Filter helper for acceptance board queries
		def _active_wis(owner: str | None = None):
			return [
				w
				for w in wis
				if w.get("status") in ("assigned", "in_progress", "on_hold")
				and (owner is None or w.get("assigned_to") == owner)
			]

		if "count(*) as active_total" in q:
			day_d = params[0]
			owner = params[-1] if "wi.assigned_to = %s" in q else None
			act = _active_wis(owner)
			row = frappe._dict(
				active_total=len(act),
				unattempted=sum(1 for w in act if int(w.get("attempt_count") or 0) == 0),
				consulting=sum(1 for w in act if w.get("worker_stage") == "consulting"),
				interview_scheduled=sum(1 for w in act if w.get("worker_stage") == "interview_scheduled"),
				interview_due_today=sum(
					1
					for w in act
					if w.get("worker_stage") == "interview_scheduled"
					and (w.get("interview_date") is None or w.get("interview_date") <= day_d)
				),
				working_1_to_6=sum(1 for w in act if w.get("worker_stage") == "working_care"),
				workday_unlogged_today=sum(
					1
					for w in act
					if w.get("worker_stage") == "working_care"
					and (w.get("last_workday_logged_on") is None or w.get("last_workday_logged_on") < day_d)
				),
				dropped_active=sum(1 for w in act if w.get("worker_stage") == "dropped_early"),
			)
			return [row] if as_dict else [tuple(row.values())]

		if "self_sourced_period" in q:
			p_start, p_end = params[0], params[1]
			d_start, d_end = params[6], params[7]
			owner = params[-1] if "owner_at_close" in q else None
			scoped = [
				w for w in wis if owner is None or (w.get("owner_at_close") or w.get("closed_by")) == owner
			]
			row = frappe._dict(
				self_sourced_period=sum(
					1
					for w in scoped
					if p_start <= w["creation"] < p_end and w.get("source_type") == "self_sourced"
				),
				intake_period=sum(1 for w in scoped if p_start <= w["creation"] < p_end),
				completed_7d_period=sum(
					1
					for w in scoped
					if w.get("completed_7d_at")
					and p_start <= w["completed_7d_at"] < p_end
					and w.get("worker_stage") == "completed_7_days"
				),
				completed_7d_today=sum(
					1
					for w in scoped
					if w.get("completed_7d_at")
					and d_start <= w["completed_7d_at"] < d_end
					and w.get("worker_stage") == "completed_7_days"
				),
				dropped_closed_period=0,
			)
			return [row] if as_dict else [tuple(row.values())]

		if "sum(case when wi.source_type = 'self_sourced'" in q and "total_cnt" in q:
			p_start, p_end, actor = params[0], params[1], params[2]
			mine = [
				w
				for w in wis
				if p_start <= w["creation"] < p_end
				and (w.get("assigned_to") == actor or w.get("owner_at_close") == actor)
			]
			return [
				frappe._dict(
					self_cnt=sum(1 for w in mine if w.get("source_type") == "self_sourced"),
					total_cnt=len(mine),
				)
			]

		if "and wi.worker_stage = 'interview_scheduled'" in q:
			day_d = params[0]
			owner = params[1] if "wi.assigned_to = %s" in q else None
			out = []
			for w in _active_wis(owner):
				if w.get("worker_stage") == "interview_scheduled" and (
					not w.get("interview_date") or w.get("interview_date") <= day_d
				):
					lead = self.env.tables["CRM Lead"][w["lead"]]
					out.append(
						frappe._dict(
							work_item=w["name"],
							lead=w["lead"],
							campaign=w["campaign"],
							assigned_to=w["assigned_to"],
							target_factory=w.get("target_factory"),
							target_industrial_zone=w.get("target_industrial_zone"),
							interview_date=w.get("interview_date"),
							campaign_title="",
							factory_name="",
							industrial_zone="",
							lead_name=lead.get("lead_name"),
							first_name=lead.get("first_name"),
							mobile_no=lead.get("mobile_no"),
						)
					)
			return out

		if "and wi.worker_stage = 'working_care'" in q:
			owner = params[0] if "wi.assigned_to = %s" in q else None
			out = []
			for w in _active_wis(owner):
				if w.get("worker_stage") == "working_care":
					lead = self.env.tables["CRM Lead"][w["lead"]]
					out.append(
						frappe._dict(
							work_item=w["name"],
							lead=w["lead"],
							campaign=w["campaign"],
							assigned_to=w["assigned_to"],
							target_factory=w.get("target_factory"),
							target_industrial_zone=w.get("target_industrial_zone"),
							onboard_date=w.get("onboard_date"),
							workdays_completed=w.get("workdays_completed", 0),
							target_workdays=w.get("target_workdays", 7),
							last_workday_logged_on=w.get("last_workday_logged_on"),
							campaign_title="",
							factory_name="",
							industrial_zone="",
							lead_name=lead.get("lead_name"),
							first_name=lead.get("first_name"),
							mobile_no=lead.get("mobile_no"),
						)
					)
			return out

		if "where wi.worker_stage = 'completed_7_days'" in q:
			p_start, p_end = params[0], params[1]
			owner = params[2] if "owner_at_close" in q and len(params) >= 3 else None
			out = []
			for w in wis:
				eff_owner = w.get("owner_at_close") or w.get("closed_by") or w.get("assigned_to")
				if (
					w.get("worker_stage") == "completed_7_days"
					and w.get("completed_7d_at")
					and p_start <= w["completed_7d_at"] < p_end
					and (owner is None or eff_owner == owner)
				):
					lead = self.env.tables["CRM Lead"][w["lead"]]
					out.append(
						frappe._dict(
							work_item=w["name"],
							lead=w["lead"],
							campaign=w["campaign"],
							owner_user=eff_owner,
							target_factory=w.get("target_factory"),
							target_industrial_zone=w.get("target_industrial_zone"),
							onboard_date=w.get("onboard_date"),
							workdays_completed=w.get("workdays_completed", 7),
							completed_7d_at=w.get("completed_7d_at"),
							campaign_title="",
							factory_name="",
							industrial_zone="",
							lead_name=lead.get("lead_name"),
							first_name=lead.get("first_name"),
							mobile_no=lead.get("mobile_no"),
						)
					)
			return out

		if "group by wi.assigned_to" in q:
			grouped: dict[str, list] = {}
			for w in _active_wis():
				u = w.get("assigned_to")
				if u:
					grouped.setdefault(u, []).append(w)
			return [
				frappe._dict(
					u=u,
					active_cnt=len(rows),
					conn_cnt=sum(1 for r in rows if int(r.get("connected_attempt_count") or 0) > 0),
					iv_cnt=sum(1 for r in rows if r.get("worker_stage") == "interview_scheduled"),
					wk_cnt=sum(1 for r in rows if r.get("worker_stage") == "working_care"),
				)
				for u, rows in grouped.items()
			]

		if "group by u" in q:
			p_start, p_end, d_start, d_end = params[0], params[1], params[2], params[3]
			grouped = {}
			for w in wis:
				u = w.get("assigned_to") or w.get("owner_at_close") or w.get("closed_by")
				if u:
					grouped.setdefault(u, []).append(w)
			return [
				frappe._dict(
					u=u,
					self_cnt=sum(
						1
						for r in rows
						if p_start <= r["creation"] < p_end and r.get("source_type") == "self_sourced"
					),
					c7_today=sum(
						1
						for r in rows
						if r.get("completed_7d_at")
						and d_start <= r["completed_7d_at"] < d_end
						and r.get("worker_stage") == "completed_7_days"
					),
					c7_period=sum(
						1
						for r in rows
						if r.get("completed_7d_at")
						and p_start <= r["completed_7d_at"] < p_end
						and r.get("worker_stage") == "completed_7_days"
					),
					drop_cnt=0,
				)
				for u, rows in grouped.items()
			]

		return []

	# ------------------------------------------------------------------
	# Tests
	# ------------------------------------------------------------------

	def test_phone_normalization_and_national_format(self):
		self.assertEqual(normalize_vn_phone("0912 345 678"), "+84912345678")
		self.assertEqual(worker_intake.to_national_phone("+84912345678"), "0912345678")
		with self.assertRaises(InvalidPhone):
			normalize_vn_phone("02438222111")  # landline rejected

	def test_self_sourced_intake_binds_instant_protection_and_blocks_colleague_duplicate_with_zero_pii_leak(
		self,
	):
		res_a = worker_intake.add_self_sourced_worker(
			phone="0912 345 678",
			full_name="Nguyễn Văn Công Nhân",
			acquisition_channel="Zalo",
			birth_year=2001,
			gender="Nam",
			current_area="Trọ VSIP 1",
			id_card_status="Co CCCD goc",
			worker_tags="Đi được ca đêm",
			target_factory="Samsung",
			target_industrial_zone="KCN Yên Phong",
			request_id="req-intake-1",
			actor="mkt.a@kcn.vn",
		)
		self.assertEqual(res_a["status"], "claimed")
		self.assertEqual(res_a["national_phone"], "0912345678")
		self.assertEqual(res_a["assigned_to"], "mkt.a@kcn.vn")

		# Verify ToDo anchor `CSKH assignment (<work_item>)` is preserved
		todos = list(self.env.tables["ToDo"].values())
		self.assertEqual(len(todos), 1)
		self.assertEqual(todos[0]["description"], f"CSKH assignment ({res_a['work_item']})")

		# Colleague B checks phone -> blocked as `protected_other` with ZERO PII leak
		check_b = worker_intake.check_phone_protection("0912345678", actor="mkt.b@kcn.vn")
		self.assertFalse(check_b["can_claim"])
		self.assertEqual(check_b["status"], "protected_other")
		dumped_check_b = json.dumps(check_b, ensure_ascii=False)
		self.assertNotIn("mkt.a@kcn.vn", dumped_check_b)
		self.assertNotIn("Nguyễn Văn Công Nhân", dumped_check_b)

		# Colleague B tries to add same phone -> raises ALREADY_ASSIGNED Conflict
		with self.assertRaises(Conflict) as ctx:
			worker_intake.add_self_sourced_worker(
				phone="+84912345678",
				full_name="Tên Khác",
				request_id="req-intake-b",
				actor="mkt.b@kcn.vn",
			)
		self.assertEqual(ctx.exception.code, "ALREADY_ASSIGNED")
		self.assertNotIn("mkt.a@kcn.vn", str(ctx.exception))

	def test_bulk_add_mixed_lines_protects_valid_and_isolates_conflicts(self):
		worker_intake.add_self_sourced_worker(
			phone="0911222333",
			full_name="Người của A",
			request_id="seed-a",
			actor="mkt.a@kcn.vn",
		)
		worker_intake.add_self_sourced_worker(
			phone="0988777666",
			full_name="Người cũ của B",
			request_id="seed-b",
			actor="mkt.b@kcn.vn",
		)

		bulk_res = worker_intake.bulk_add_self_sourced_workers(
			lines_text="\n".join(
				[
					"0933444555 | Trần Thị Mới | 2002 | KCN Quế Võ",
					"0911222333 | Trùng với A | 2000 | VSIP",
					"0988777666 | Đã thuộc B | 1999 | Yên Phong",
					"12345 | Sai SĐT",
				]
			),
			acquisition_channel="Facebook",
			request_id="bulk-b-1",
			actor="mkt.b@kcn.vn",
		)
		self.assertEqual(bulk_res["protected_count"], 1)
		self.assertEqual(bulk_res["conflict_count"], 1)
		self.assertEqual(bulk_res["already_mine_count"], 1)
		self.assertEqual(bulk_res["invalid_count"], 1)

	def test_two_tier_rbac_blocks_team_leader_and_bod_mutations(self):
		res = worker_intake.add_self_sourced_worker(
			phone="0944555666",
			full_name="Lê Văn Lao Động",
			request_id="rbac-1",
			actor="mkt.a@kcn.vn",
		)
		wi_id = res["work_item"]

		# Unassigned Sales Manager (`leader@kcn.vn`) has NO team-leader override in 2-tier KCN model
		with self.assertRaises(OwnershipChanged):
			worker_lifecycle.update_worker_profile(
				work_item=wi_id,
				full_name="Sửa Trái Phép",
				actor="leader@kcn.vn",
			)

		# BOD (`bod@kcn.vn`) is strictly read-only
		with self.assertRaises(Forbidden):
			worker_lifecycle.record_worker_progress(
				work_item=wi_id,
				action_type="schedule_interview",
				actor="bod@kcn.vn",
			)

		# Assigned owner (`mkt.a@kcn.vn`) and CRM Admin (`admin@kcn.vn`) succeed
		upd = worker_lifecycle.update_worker_profile(
			work_item=wi_id,
			full_name="Lê Văn Chính Thức",
			birth_year=1998,
			target_factory="Foxconn",
			target_industrial_zone="KCN Quang Châu",
			actor="mkt.a@kcn.vn",
		)
		self.assertEqual(upd["display_name"], "Lê Văn Chính Thức")
		self.assertEqual(upd["target_factory"], "Foxconn")

	def test_full_7_workday_lifecycle_only_closes_on_day_7_and_feeds_acceptance_board(self):
		intake = worker_intake.add_self_sourced_worker(
			phone="0977888999",
			full_name="Phạm Thị Chăm Chỉ",
			target_factory="Canon",
			target_industrial_zone="KCN Thăng Long",
			request_id="life-7d",
			actor="mkt.a@kcn.vn",
		)
		wi_id = intake["work_item"]

		# 1. Schedule interview -> active (`interview_scheduled`)
		iv_res = worker_lifecycle.record_worker_progress(
			work_item=wi_id,
			action_type="schedule_interview",
			interview_date="2026-11-28",
			actor="mkt.a@kcn.vn",
		)
		self.assertEqual(iv_res["worker_stage"], "interview_scheduled")
		self.assertEqual(iv_res["work_item_status"], "in_progress")
		self.assertFalse(iv_res["completed_7_days"])

		# 2. Onboard Day 1 -> active (`working_care`, 1/7 workdays)
		self.env.now = datetime(2026, 11, 29, 18, 0, 0)
		ob_res = worker_lifecycle.record_worker_progress(
			work_item=wi_id,
			action_type="onboard",
			onboard_date="2026-11-29",
			workdays_completed=1,
			actor="mkt.a@kcn.vn",
		)
		self.assertEqual(ob_res["worker_stage"], "working_care")
		self.assertEqual(ob_res["workdays_completed"], 1)
		self.assertEqual(ob_res["work_item_status"], "in_progress")

		# Double check-in on the SAME day is blocked
		with self.assertRaises(Conflict):
			worker_lifecycle.record_worker_progress(
				work_item=wi_id,
				action_type="checkin_workday",
				increment_workday=True,
				actor="mkt.a@kcn.vn",
			)

		# 3. Days 2..6 -> still active (`working_care`)
		for day_offset, expected_days in enumerate(range(2, 7), start=1):
			self.env.now = datetime(2026, 11, 29, 18, 0, 0) + timedelta(days=day_offset)
			step_res = worker_lifecycle.record_worker_progress(
				work_item=wi_id,
				action_type="checkin_workday",
				increment_workday=True,
				actor="mkt.a@kcn.vn",
			)
			self.assertEqual(step_res["workdays_completed"], expected_days)
			self.assertEqual(step_res["worker_stage"], "working_care")
			self.assertEqual(step_res["work_item_status"], "in_progress")
			self.assertFalse(step_res["completed_7_days"])

		# 4. Day 7 -> automatically closes as `completed_7_days`!
		self.env.now = datetime(2026, 12, 5, 18, 30, 0)
		day7_res = worker_lifecycle.record_worker_progress(
			work_item=wi_id,
			action_type="checkin_workday",
			increment_workday=True,
			actor="mkt.a@kcn.vn",
		)
		self.assertTrue(day7_res["completed_7_days"])
		self.assertEqual(day7_res["workdays_completed"], 7)
		self.assertEqual(day7_res["worker_stage"], "completed_7_days")
		self.assertEqual(day7_res["work_item_status"], "closed")

		# 5. Verify End-of-Day Acceptance Board & 0-PII BOD isolation + formula-safe CSV
		with mock.patch.object(
			worker_lifecycle.reporting,
			"_scope",
			side_effect=lambda u: {"scope": "all"}
			if u in ("admin@kcn.vn", "bod@kcn.vn")
			else {"scope": "self"},
		):
			staff_board = worker_lifecycle.get_daily_acceptance_board(
				target_date="2026-12-05",
				actor="mkt.a@kcn.vn",
			)
			self.assertTrue(staff_board["allow_pii"])
			self.assertEqual(staff_board["summary"]["completed_7_days_today"], 1)
			self.assertEqual(len(staff_board["completed_7d_items"]), 1)
			self.assertEqual(staff_board["completed_7d_items"][0]["national_phone"], "0977888999")

			bod_board = worker_lifecycle.get_daily_acceptance_board(
				target_date="2026-12-05",
				actor="bod@kcn.vn",
			)
			self.assertFalse(bod_board["allow_pii"])
			self.assertEqual(bod_board["pending_interviews"], [])
			self.assertEqual(bod_board["pending_workdays"], [])
			self.assertEqual(bod_board["completed_7d_items"], [])
			bod_json = json.dumps(bod_board, ensure_ascii=False, default=str)
			self.assertNotIn("0977888999", bod_json)
			self.assertNotIn("Phạm Thị Chăm Chỉ", bod_json)
			self.assertEqual(bod_board["per_staff"][0]["completed_7_days_today"], 1)

			# Formula injection guard in CSV export
			self.env.tables["User"]["mkt.a@kcn.vn"]["full_name"] = "=CMD|' /C calc'!A0"
			bod_csv = worker_lifecycle.export_acceptance_csv(
				date_from="2026-12-05",
				date_to="2026-12-05",
				actor="bod@kcn.vn",
			)
			self.assertIn("'=CMD|' /C calc'!A0", bod_csv["csv_content"])
			self.assertNotIn("0977888999", bod_csv["csv_content"])

	def test_streamlined_navigation_hides_crm_links_and_team_leader_tier(self):
		self.assertEqual(cskh_context.NAV_BY_ROLE["Sales User"], {"my-customers", "callbacks", "progress"})
		self.assertEqual(cskh_context.NAV_BY_ROLE["Sales Manager"], {"my-customers", "callbacks", "progress"})
		frappe.session.user = "mkt.a@kcn.vn"
		vis_staff = cskh_context._visible_keys()
		self.assertEqual(vis_staff, {"my-customers", "callbacks", "progress"})

		frappe.session.user = "admin@kcn.vn"
		vis_admin = cskh_context._visible_keys()
		self.assertEqual(
			vis_admin,
			{"my-customers", "callbacks", "progress", "dashboard", "campaigns", "pool", "import", "settings"},
		)


if __name__ == "__main__":
	unittest.main()
