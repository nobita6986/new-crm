"""QA sampling & review service (`P3-06`, `docs/p3/qa-checklist-and-sampling.md`).

Honesty & Security Invariants:
  * Default `CSKH Policy.qa_enabled == 0` (or site kill switch `cskh_p3_enabled=false` / `cskh_qa_enabled=false`):
    - `0` fake QA reviews or fake `verified` flags are ever generated.
    - Write endpoints (`create_sample`, `submit_review`, `create_review`) fail closed with `409 QA_DISABLED`
      (and `400 INVALID_REQUEST` when enabled with missing required fields — never `500 TypeError`).
    - `list_reviews` returns `{"ok": True, "qa_enabled": False, "status": "disabled", "rows": []}`.
  * When `qa_enabled == 1` in active policy:
    - Enforces `reviewer != reviewed_staff` (`403 QA_CONFLICT` — `S06`) for ALL actors including Admin/Leader.
    - Blocks `Staff` & `BOD` writes (`403 FORBIDDEN_SCOPE`); restricts `Leader` to authorized `team-as-of` subtree.
    - Rejects `voided` contact attempts.
    - Schedules post-commit `CSKH Dirty Scope` invalidation so `QA_PASS_RATE` & `PROCESS_SCORE` update cleanly.
"""

from __future__ import annotations

import json
import math

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime

from crm_cskh.api._params import parse_optional_str, parse_pagination
from crm_cskh.p1_permissions import ROLE_ADMIN, ROLE_BOD, _is_system, _roles
from crm_cskh.p2_permissions import _in_team, _is_leader, _is_staff
from crm_cskh.p3_models import authorized_qa_write
from crm_cskh.p3_permissions import _leader_allowed_nodes
from crm_cskh.services import p3_flags
from crm_cskh.services.attribution import resolve_team_for_user_at
from crm_cskh.services.calendar import format_calendar_iso
from crm_cskh.services.errors import (
	ForbiddenScope,
	InvalidRequest,
	NotFound,
	QaConflict,
	QaDisabled,
)
from crm_cskh.services.kpi_runs import schedule_post_commit_dirty_invalidation
from crm_cskh.services.read_model import compute_effective_attempts
from crm_cskh.services.sanitizer import sanitize_note_text

VALID_QA_COHORT_TYPES = frozenset(("random_sample", "targeted_coaching", "dispute_audit"))


def is_qa_enabled(campaign: str | None = None, owner_user: str | None = None) -> bool:
	return p3_flags.is_qa_enabled(campaign=campaign, owner_user=owner_user)


def _require_qa_reviewer(actor: str) -> tuple[bool, bool]:
	if not actor or actor == "Guest":
		raise ForbiddenScope("Yêu cầu đăng nhập để thực hiện kiểm định QA.")
	roles = _roles(actor)
	is_admin = _is_system(actor) or ROLE_ADMIN in roles
	is_leader = _is_leader(actor)
	if not (is_admin or is_leader):
		raise ForbiddenScope("Chỉ Team Leader hoặc CSKH CRM Admin được phép chấm điểm QA.")
	return is_admin, is_leader


def _parse_cohort_type(cohort_type: str | None) -> str:
	val = (parse_optional_str(cohort_type, "Tham số cohort_type không hợp lệ.") or "random_sample").lower()
	if val not in VALID_QA_COHORT_TYPES:
		raise InvalidRequest(
			"Tham số cohort_type phải thuộc ('random_sample', 'targeted_coaching', 'dispute_audit')."
		)
	return val


def create_sample(
	contact_attempt: str | None = None,
	*,
	cohort_type: str = "random_sample",
	rubric_version: str = "qa-rubric-v1",
	actor: str | None = None,
) -> dict:
	"""Create a `draft` `CSKH QA Review` sample when `qa_enabled == 1` (`409 QA_DISABLED` otherwise)."""
	user = actor or frappe.session.user
	is_admin, is_leader = _require_qa_reviewer(user)
	att_id = parse_optional_str(contact_attempt, "Mã nhật ký liên hệ không hợp lệ.")
	if not att_id:
		if not is_qa_enabled():
			raise QaDisabled("Chức năng chấm điểm QA hiện đang tắt theo chính sách (qa_enabled=false).")
		raise InvalidRequest("Tham số contact_attempt là bắt buộc.")

	att = frappe.db.get_value(
		"CSKH Contact Attempt",
		att_id,
		["name", "work_item", "campaign", "performed_by", "owner_at_attempt", "occurred_at"],
		as_dict=True,
	)
	if not is_qa_enabled(
		campaign=att.campaign if att else None,
		owner_user=(att.performed_by or att.owner_at_attempt) if att else None,
	):
		raise QaDisabled("Chức năng chấm điểm QA hiện đang tắt theo chính sách (qa_enabled=false).")

	cohort_val = _parse_cohort_type(cohort_type)
	if not att:
		if is_admin:
			raise NotFound("Không tìm thấy bản ghi CSKH Contact Attempt.")
		raise ForbiddenScope("Bạn không có quyền truy cập bản ghi liên hệ này.")

	staff_user = att.performed_by or att.owner_at_attempt
	if user == staff_user:
		raise QaConflict("Người chấm QA không được phép tự chọn mẫu hoặc tự chấm bản ghi của chính mình.")

	team_node, _ = resolve_team_for_user_at(
		staff_user,
		get_datetime(att.occurred_at),
		allow_live_capture_for_current_tx=False,
	)
	if is_leader and not is_admin:
		allowed_nodes = _leader_allowed_nodes(user)
		if not ((team_node and team_node in allowed_nodes) or (not team_node and _in_team(user, staff_user))):
			raise ForbiddenScope(
				"Team Leader chỉ được lấy mẫu QA đối với bản ghi thuộc phạm vi team của mình."
			)

	projected = compute_effective_attempts(att.work_item)
	proj_att = next((a for a in projected if a["attempt_id"] == att.name), None)
	if not proj_att or proj_att["is_voided"]:
		raise QaConflict(
			"Không thể lấy mẫu QA trên bản ghi liên hệ đã bị hủy hiệu lực (voided).", http_status=409
		)

	existing = frappe.db.get_value("CSKH QA Review", {"contact_attempt": att.name}, "name")
	if existing:
		raise QaConflict("Bản ghi liên hệ này đã có phiếu kiểm định QA.", http_status=409)

	now_dt = now_datetime()
	doc_payload = {
		"doctype": "CSKH QA Review",
		"contact_attempt": att.name,
		"work_item": att.work_item,
		"campaign": att.campaign,
		"reviewed_staff": staff_user,
		"team_at_review": team_node,
		"reviewer": user,
		"status": "draft",
		"cohort_type": cohort_val,
		"rubric_version": str(rubric_version or "qa-rubric-v1")[:60],
		"points_achieved": 0.0,
		"max_points": 100.0,
		"passed": 0,
		"reviewed_at": now_dt,
	}
	with authorized_qa_write():
		doc = frappe.get_doc(doc_payload)
		doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True)

	frappe.db.commit()
	return {
		"ok": True,
		"qa_review_id": doc.name,
		"contact_attempt": att.name,
		"reviewed_staff": staff_user,
		"cohort_type": cohort_val,
		"status": "draft",
	}


def submit_review(
	contact_attempt: str | None = None,
	points_achieved: float | int | None = None,
	max_points: float | int = 100.0,
	*,
	passed: bool | int | None = None,
	status: str = "published",
	cohort_type: str = "random_sample",
	feedback_summary: str | None = None,
	internal_note: str | None = None,
	scores: dict | str | None = None,
	rubric_version: str = "qa-rubric-v1",
	reviewed_at: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Submit or update a `CSKH QA Review` when `qa_enabled == 1` (`409 QA_DISABLED` otherwise)."""
	user = actor or frappe.session.user
	is_admin, is_leader = _require_qa_reviewer(user)
	att_id = parse_optional_str(contact_attempt, "Mã nhật ký liên hệ không hợp lệ.")
	if not att_id:
		if not is_qa_enabled():
			raise QaDisabled("Chức năng chấm điểm QA hiện đang tắt theo chính sách (qa_enabled=false).")
		raise InvalidRequest("Tham số contact_attempt là bắt buộc.")

	att = frappe.db.get_value(
		"CSKH Contact Attempt",
		att_id,
		["name", "work_item", "campaign", "performed_by", "owner_at_attempt", "occurred_at"],
		as_dict=True,
	)
	if not is_qa_enabled(
		campaign=att.campaign if att else None,
		owner_user=(att.performed_by or att.owner_at_attempt) if att else None,
	):
		raise QaDisabled("Chức năng chấm điểm QA hiện đang tắt theo chính sách (qa_enabled=false).")

	if points_achieved is None or (isinstance(points_achieved, str) and not points_achieved.strip()):
		raise InvalidRequest("Tham số points_achieved là bắt buộc.")

	cohort_val = _parse_cohort_type(cohort_type)
	if not att:
		if is_admin:
			raise NotFound("Không tìm thấy bản ghi CSKH Contact Attempt.")
		raise ForbiddenScope("Bạn không có quyền truy cập bản ghi liên hệ này.")

	staff_user = att.performed_by or att.owner_at_attempt
	if user == staff_user:
		raise QaConflict("Người chấm QA không được phép tự chấm bản ghi liên hệ của chính mình.")

	team_node, _ = resolve_team_for_user_at(
		staff_user,
		get_datetime(att.occurred_at),
		allow_live_capture_for_current_tx=False,
	)
	if is_leader and not is_admin:
		allowed_nodes = _leader_allowed_nodes(user)
		if not ((team_node and team_node in allowed_nodes) or (not team_node and _in_team(user, staff_user))):
			raise ForbiddenScope("Team Leader chỉ được chấm điểm QA cho bản ghi thuộc phạm vi team của mình.")

	projected = compute_effective_attempts(att.work_item)
	proj_att = next((a for a in projected if a["attempt_id"] == att.name), None)
	if not proj_att or proj_att["is_voided"]:
		raise QaConflict(
			"Không thể chấm QA cho bản ghi liên hệ đã bị hủy hiệu lực (voided).", http_status=409
		)

	try:
		pts = float(points_achieved)
		max_pts = float(max_points)
	except (TypeError, ValueError) as exc:
		raise InvalidRequest("Điểm QA (points_achieved / max_points) phải là số hợp lệ.") from exc

	if max_pts <= 0 or pts < 0 or pts > max_pts:
		raise InvalidRequest(
			"Điểm QA không hợp lệ (cần 0 <= points_achieved <= max_points và max_points > 0)."
		)

	st_val = (status or "published").strip().lower()
	if st_val not in ("draft", "published", "disputed", "voided"):
		raise InvalidRequest("Trạng thái phiếu QA không hợp lệ.")

	passed_flag = int(bool(passed)) if passed is not None else (1 if (pts / max_pts) >= 0.8 else 0)
	rev_dt = get_datetime(reviewed_at) if reviewed_at else now_datetime()
	clean_feedback = sanitize_note_text(feedback_summary, max_length=1000) if feedback_summary else None
	clean_internal = sanitize_note_text(internal_note, max_length=1000) if internal_note else None
	scores_str = (
		json.dumps(scores, ensure_ascii=False)
		if isinstance(scores, dict)
		else (str(scores) if scores else None)
	)

	existing_name = frappe.db.get_value("CSKH QA Review", {"contact_attempt": att.name}, "name")
	with authorized_qa_write():
		if existing_name:
			doc = frappe.get_doc("CSKH QA Review", existing_name)
			doc.reviewer = user
			doc.status = st_val
			if frappe.db.has_column("CSKH QA Review", "cohort_type"):
				doc.cohort_type = cohort_val
			doc.rubric_version = str(rubric_version or "qa-rubric-v1")[:60]
			doc.points_achieved = pts
			doc.max_points = max_pts
			doc.passed = passed_flag
			doc.reviewed_at = rev_dt
			doc.feedback_summary = clean_feedback
			doc.internal_note = clean_internal
			doc.scores_json = scores_str
			doc.flags.ignore_links = True
			doc.save(ignore_permissions=True)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "CSKH QA Review",
					"contact_attempt": att.name,
					"work_item": att.work_item,
					"campaign": att.campaign,
					"reviewed_staff": staff_user,
					"team_at_review": team_node,
					"reviewer": user,
					"status": st_val,
					"cohort_type": cohort_val,
					"rubric_version": str(rubric_version or "qa-rubric-v1")[:60],
					"points_achieved": pts,
					"max_points": max_pts,
					"passed": passed_flag,
					"reviewed_at": rev_dt,
					"feedback_summary": clean_feedback,
					"internal_note": clean_internal,
					"scores_json": scores_str,
				}
			)
			doc.flags.ignore_links = True
			doc.insert(ignore_permissions=True)

	if frappe.db.has_column("CSKH Contact Attempt", "verification_status"):
		ver_state = (
			"verified" if st_val == "published" else ("disputed" if st_val == "disputed" else "self_reported")
		)
		frappe.db.set_value(
			"CSKH Contact Attempt",
			att.name,
			"verification_status",
			ver_state,
			update_modified=False,
		)

	schedule_post_commit_dirty_invalidation(
		work_item=att.work_item,
		campaign=att.campaign,
		owner_user=staff_user,
		team_node=team_node,
		event_dt=rev_dt,
		reason="qa_review_submitted",
		trigger_reference=doc.name,
	)
	schedule_post_commit_dirty_invalidation(
		work_item=att.work_item,
		campaign=att.campaign,
		owner_user=staff_user,
		team_node=team_node,
		event_dt=att.occurred_at,
		reason="qa_review_submitted",
		trigger_reference=doc.name,
	)
	frappe.db.commit()

	return {
		"ok": True,
		"qa_review_id": doc.name,
		"contact_attempt": att.name,
		"reviewed_staff": staff_user,
		"reviewer": user,
		"status": st_val,
		"cohort_type": cohort_val,
		"points_achieved": pts,
		"max_points": max_pts,
		"passed": bool(passed_flag),
		"reviewed_at": format_calendar_iso(rev_dt),
	}


def list_reviews(
	*,
	reviewed_staff: str | None = None,
	campaign: str | None = None,
	status: str | None = None,
	page=1,
	page_size=20,
	actor: str | None = None,
) -> dict:
	"""Scoped QA review listing returning explicit `disabled` state when `qa_enabled == 0`."""
	user = actor or frappe.session.user
	if not user or user == "Guest":
		raise ForbiddenScope("Yêu cầu đăng nhập để xem danh sách kiểm định QA.")
	roles = _roles(user)
	is_admin = _is_system(user) or ROLE_ADMIN in roles
	is_bod = ROLE_BOD in roles
	is_leader = _is_leader(user)
	is_staff = _is_staff(user)

	if not (is_admin or is_bod or is_leader or is_staff):
		raise ForbiddenScope("Bạn không có quyền truy cập danh sách kiểm định QA.")

	page_num, limit, offset = parse_pagination(
		page, page_size, default_page=1, default_page_size=20, max_page_size=100
	)

	if not is_qa_enabled(campaign=campaign):
		return {
			"ok": True,
			"qa_enabled": False,
			"status": "disabled",
			"rows": [],
			"pagination": {
				"page": page_num,
				"page_size": limit,
				"total": 0,
				"total_pages": 0,
			},
		}

	if is_bod and not (is_admin or is_leader):
		# BOD receives aggregate counts only (`0` raw attempt/lead records)
		total_pub = frappe.db.count("CSKH QA Review", {"status": "published"})
		passed_pub = frappe.db.count("CSKH QA Review", {"status": "published", "passed": 1})
		return {
			"ok": True,
			"qa_enabled": True,
			"status": "aggregate_only",
			"aggregate_summary": {
				"published_reviews": int(total_pub),
				"passed_reviews": int(passed_pub),
			},
			"rows": [],
			"pagination": {
				"page": page_num,
				"page_size": limit,
				"total": 0,
				"total_pages": 0,
			},
		}

	clauses = ["1=1"]
	params: list = []

	staff_filter = parse_optional_str(reviewed_staff, "Tham số reviewed_staff không hợp lệ.")
	camp_filter = parse_optional_str(campaign, "Tham số campaign không hợp lệ.")
	st_filter = parse_optional_str(status, "Tham số status không hợp lệ.")

	if is_admin:
		if staff_filter:
			clauses.append("reviewed_staff = %s")
			params.append(staff_filter)
	elif is_leader:
		allowed_nodes = sorted(_leader_allowed_nodes(user))
		if not allowed_nodes:
			raise ForbiddenScope("Team Leader chưa được gán nhánh cây phân cấp hợp lệ.")
		ph_nodes = ", ".join(["%s"] * len(allowed_nodes))
		clauses.append(f"team_at_review in ({ph_nodes})")
		params.extend(allowed_nodes)
		if staff_filter:
			has_hist_in_scope = bool(
				frappe.db.sql(
					f"select 1 from `tabCSKH QA Review` where reviewed_staff = %s and team_at_review in ({ph_nodes}) limit 1",
					(staff_filter, *allowed_nodes),
				)
			)
			if not (staff_filter == user or _in_team(user, staff_filter) or has_hist_in_scope):
				raise ForbiddenScope("Nhân viên được lọc nằm ngoài phạm vi team của bạn.")
			clauses.append("reviewed_staff = %s")
			params.append(staff_filter)
	else:
		# Staff: self only & `published` only
		if staff_filter and staff_filter != user:
			raise ForbiddenScope("Nhân viên chỉ được xem kết quả kiểm định QA của chính mình.")
		clauses.append("reviewed_staff = %s")
		params.append(user)
		clauses.append("status = 'published'")

	if camp_filter:
		clauses.append("campaign = %s")
		params.append(camp_filter)
	if st_filter:
		clauses.append("status = %s")
		params.append(st_filter)

	where_sql = " and ".join(clauses)
	total = int(
		frappe.db.sql(f"select count(*) from `tabCSKH QA Review` where {where_sql}", tuple(params))[0][0] or 0
	)
	has_cohort_col = frappe.db.has_column("CSKH QA Review", "cohort_type")
	cohort_sel = "cohort_type" if has_cohort_col else "'random_sample' as cohort_type"
	rows_raw = frappe.db.sql(
		f"""
		select name, contact_attempt, work_item, campaign, reviewed_staff, team_at_review,
		       reviewer, status, {cohort_sel}, rubric_version, points_achieved, max_points, passed,
		       reviewed_at, feedback_summary, internal_note
		from `tabCSKH QA Review`
		where {where_sql}
		order by reviewed_at desc, creation desc
		limit %s offset %s
		""",
		(*tuple(params), limit, offset),
		as_dict=True,
	)

	can_see_internal = is_admin or is_leader
	rows = []
	for r in rows_raw:
		item = {
			"qa_review_id": r.name,
			"contact_attempt": r.contact_attempt,
			"work_item": r.work_item,
			"campaign": r.campaign,
			"reviewed_staff": r.reviewed_staff,
			"team_at_review": r.team_at_review,
			"reviewer": r.reviewer,
			"status": r.status,
			"cohort_type": r.get("cohort_type") or "random_sample",
			"rubric_version": r.rubric_version,
			"points_achieved": flt(r.points_achieved),
			"max_points": flt(r.max_points),
			"passed": bool(cint(r.passed)),
			"reviewed_at": format_calendar_iso(r.reviewed_at) if r.reviewed_at else None,
			"feedback_summary": r.feedback_summary,
		}
		if can_see_internal:
			item["internal_note"] = r.internal_note
		rows.append(item)

	return {
		"ok": True,
		"qa_enabled": True,
		"status": "ok",
		"rows": rows,
		"pagination": {
			"page": page_num,
			"page_size": limit,
			"total": total,
			"total_pages": math.ceil(total / limit) if total > 0 else 0,
		},
	}
