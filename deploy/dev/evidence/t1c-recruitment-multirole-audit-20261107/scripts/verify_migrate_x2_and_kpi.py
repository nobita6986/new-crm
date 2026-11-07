import json
import frappe
from frappe.utils import cint
from crm_cskh.p2_models import ensure_p2_seeds, DEFAULT_OUTCOME_CODES
from crm_cskh.p3_models import authorized_kpi_write
from crm_cskh.services.kpi_runs import unpack_snapshot_metrics
from crm_cskh.services.kpi_calculator import METRIC_LABELS_VI

def test_migration_and_snapshot_preservation():
    frappe.set_user("Administrator")
    results = {}

    # 1. Setup legacy test fixtures on CSKH Outcome Code
    frappe.db.set_value("CSKH Outcome Code", "connected_needs_followup", {
        "version": 1,
        "label_vi": "Đã trao đổi — Cần chăm sóc tiếp",
        "description": "Cuộc gọi đổ chuông hết hồi nhưng khách hàng không bắt máy."
    }, update_modified=False)

    frappe.db.set_value("CSKH Outcome Code", "connected_interested", {
        "version": 1,
        "label_vi": "Đã trao đổi — Quan tâm",
        "description": "Khách hàng phản hồi tích cực và thể hiện sự quan tâm rõ ràng."
    }, update_modified=False)

    frappe.db.set_value("CSKH Outcome Code", "callback_requested", {
        "version": 1,
        "label_vi": "Khách hẹn gọi / liên hệ lại",
        "description": "Khách xác nhận bận tại thời điểm liên hệ và chủ động đề xuất lịch gọi lại."
    }, update_modified=False)

    # Setup an admin customized code
    frappe.db.set_value("CSKH Outcome Code", "no_answer", {
        "version": 2, # Admin customized version
        "label_vi": "Khách không nghe máy (Admin Custom)",
        "description": "Custom description by admin"
    }, update_modified=False)

    # Pick an existing attempt to verify snapshot preservation
    sample_att = frappe.get_all("CSKH Contact Attempt", fields=["name", "outcome_label_snapshot"], limit=1)[0]
    test_att_id = sample_att["name"]
    legacy_snapshot_text = "Đã trao đổi — Cần chăm sóc tiếp (Historical Legacy Snapshot)"
    frappe.db.set_value("CSKH Contact Attempt", test_att_id, "outcome_label_snapshot", legacy_snapshot_text, update_modified=False)

    # Setup legacy KPI snapshot with old labels embedded in JSON
    existing_run = frappe.db.get_value("CSKH KPI Run", {}, "name")
    if not existing_run:
        with authorized_kpi_write():
            run_doc = frappe.get_doc({
                "doctype": "CSKH KPI Run",
                "period_type": "monthly",
                "period_key": "2026-11",
                "status": "completed"
            }).insert(ignore_permissions=True)
            existing_run = run_doc.name

    legacy_raw_metrics = {
        "metrics": {
            "ASSIGNED_INTERVALS": {
                "value": 15.0,
                "label_vi": "Lượt nhận khách (Khoảng trách nhiệm)",
                "status": "ok",
                "numerator": 15,
                "denominator": 15
            },
            "UNIQUE_ASSIGNED_ITEMS": {
                "value": 10.0,
                "label_vi": "Khách được giao duy nhất",
                "status": "ok",
                "numerator": 10,
                "denominator": 10
            },
            "FIRST_ATTEMPT_SLA_RATE": {
                "value": 90.0,
                "label_vi": "Tỷ lệ đạt SLA liên hệ lần đầu (Khoảng trách nhiệm)",
                "status": "ok",
                "numerator": 9,
                "denominator": 10
            }
        },
        "score_components": [
            {
                "component_code": "C1_FIRST_ATTEMPT_SLA",
                "label_vi": "SLA liên hệ lần đầu đúng hạn",
                "weight": 25,
                "score": 90.0
            },
            {
                "component_code": "FOLLOWUP_SLA_RATE",
                "label_vi": "SLA chăm sóc đúng lịch hẹn",
                "weight": 20,
                "score": 85.0
            }
        ],
        "attempt_breakdown": {
            "total": 20,
            "connected": 15,
            "verified": 5,
            "self_reported": 15
        }
    }

    legacy_json_str = json.dumps(legacy_raw_metrics)
    with authorized_kpi_write():
        snap_doc = frappe.get_doc({
            "doctype": "CSKH KPI Snapshot",
            "kpi_run": existing_run,
            "period_type": "monthly",
            "period_key": "2026-11",
            "from_date": "2026-11-01",
            "to_date": "2026-11-30",
            "as_of": "2026-11-07 10:00:00",
            "scope_type": "company",
            "scope_id": "__company__",
            "scope_label": "Toàn công ty CSKH",
            "metrics_json": legacy_json_str
        }).insert(ignore_permissions=True)
    snap_id = snap_doc.name
    frappe.db.commit()

    # Pre-migration snapshot state
    results["pre_migrate"] = {
        "connected_needs_followup_label": frappe.db.get_value("CSKH Outcome Code", "connected_needs_followup", "label_vi"),
        "connected_interested_label": frappe.db.get_value("CSKH Outcome Code", "connected_interested", "label_vi"),
        "callback_requested_label": frappe.db.get_value("CSKH Outcome Code", "callback_requested", "label_vi"),
        "no_answer_label": frappe.db.get_value("CSKH Outcome Code", "no_answer", "label_vi"),
        "historical_attempt_snapshot": frappe.db.get_value("CSKH Contact Attempt", test_att_id, "outcome_label_snapshot"),
        "kpi_snapshot_raw_db_json": frappe.db.get_value("CSKH KPI Snapshot", snap_id, "metrics_json")
    }

    # RUN MIGRATE #1
    ensure_p2_seeds()
    frappe.db.commit()

    results["after_migrate_1"] = {
        "connected_needs_followup_label": frappe.db.get_value("CSKH Outcome Code", "connected_needs_followup", "label_vi"),
        "connected_interested_label": frappe.db.get_value("CSKH Outcome Code", "connected_interested", "label_vi"),
        "callback_requested_label": frappe.db.get_value("CSKH Outcome Code", "callback_requested", "label_vi"),
        "no_answer_label": frappe.db.get_value("CSKH Outcome Code", "no_answer", "label_vi"),
        "historical_attempt_snapshot": frappe.db.get_value("CSKH Contact Attempt", test_att_id, "outcome_label_snapshot"),
        "kpi_snapshot_raw_db_json": frappe.db.get_value("CSKH KPI Snapshot", snap_id, "metrics_json")
    }

    # RUN MIGRATE #2 (Idempotency test)
    ensure_p2_seeds()
    frappe.db.commit()

    results["after_migrate_2"] = {
        "connected_needs_followup_label": frappe.db.get_value("CSKH Outcome Code", "connected_needs_followup", "label_vi"),
        "connected_interested_label": frappe.db.get_value("CSKH Outcome Code", "connected_interested", "label_vi"),
        "callback_requested_label": frappe.db.get_value("CSKH Outcome Code", "callback_requested", "label_vi"),
        "no_answer_label": frappe.db.get_value("CSKH Outcome Code", "no_answer", "label_vi"),
        "historical_attempt_snapshot": frappe.db.get_value("CSKH Contact Attempt", test_att_id, "outcome_label_snapshot"),
        "kpi_snapshot_raw_db_json": frappe.db.get_value("CSKH KPI Snapshot", snap_id, "metrics_json")
    }

    # Test KPI Snapshot unpack behavior (memory projection vs stored persistence)
    db_json = frappe.db.get_value("CSKH KPI Snapshot", snap_id, "metrics_json")
    unpacked_metrics, unpacked_comps, unpacked_breakdown = unpack_snapshot_metrics(db_json)

    results["kpi_snapshot_unpack_projection"] = {
        "assigned_intervals_projected_label": unpacked_metrics["ASSIGNED_INTERVALS"]["label_vi"],
        "assigned_intervals_value": unpacked_metrics["ASSIGNED_INTERVALS"]["value"],
        "unique_assigned_items_projected_label": unpacked_metrics["UNIQUE_ASSIGNED_ITEMS"]["label_vi"],
        "unique_assigned_items_value": unpacked_metrics["UNIQUE_ASSIGNED_ITEMS"]["value"],
        "first_attempt_sla_projected_label": unpacked_metrics["FIRST_ATTEMPT_SLA_RATE"]["label_vi"],
        "first_attempt_sla_value": unpacked_metrics["FIRST_ATTEMPT_SLA_RATE"]["value"],
        "followup_sla_component_projected_label": next(c["label_vi"] for c in unpacked_comps if c.get("component_code") == "FOLLOWUP_SLA_RATE"),
        "followup_sla_component_score": next(c["score"] for c in unpacked_comps if c.get("component_code") == "FOLLOWUP_SLA_RATE"),
        "db_raw_stored_assigned_intervals_label": json.loads(db_json)["metrics"]["ASSIGNED_INTERVALS"]["label_vi"],
        "raw_db_bytes_unmodified": (db_json == legacy_json_str)
    }

    # Validations:
    # 1. Untouched legacy defaults updated
    assert results["after_migrate_1"]["connected_needs_followup_label"] == "Đã trao đổi — Cần liên hệ tiếp", f"connected_needs_followup not updated! Got {results['after_migrate_1']['connected_needs_followup_label']}"
    assert results["after_migrate_1"]["connected_interested_label"] == "Đã trao đổi — Có quan tâm", f"connected_interested not updated! Got {results['after_migrate_1']['connected_interested_label']}"
    assert results["after_migrate_1"]["callback_requested_label"] == "Đã hẹn gọi / liên hệ lại", f"callback_requested not updated! Got {results['after_migrate_1']['callback_requested_label']}"

    # 2. Admin custom preserved
    assert results["after_migrate_1"]["no_answer_label"] == "Khách không nghe máy (Admin Custom)", f"Admin custom was overwritten! Got {results['after_migrate_1']['no_answer_label']}"

    # 3. Historical contact attempt snapshot preserved
    assert results["after_migrate_1"]["historical_attempt_snapshot"] == legacy_snapshot_text, f"Historical attempt snapshot mutated! Got {results['after_migrate_1']['historical_attempt_snapshot']}"

    # 4. Migrate 2 identical to Migrate 1
    assert results["after_migrate_1"] == results["after_migrate_2"], "Migrate not idempotent!"

    # 5. Raw DB JSON in snapshot untouched
    assert results["kpi_snapshot_unpack_projection"]["raw_db_bytes_unmodified"] is True, "DB JSON was mutated!"

    # 6. Unpacked projection shows canonical labels
    assert results["kpi_snapshot_unpack_projection"]["assigned_intervals_projected_label"] == "Lượt nhận hồ sơ (Khoảng phụ trách)", f"Projected label wrong! Got {results['kpi_snapshot_unpack_projection']['assigned_intervals_projected_label']}"
    assert results["kpi_snapshot_unpack_projection"]["unique_assigned_items_projected_label"] == "Hồ sơ liên hệ được giao duy nhất", f"Projected label wrong! Got {results['kpi_snapshot_unpack_projection']['unique_assigned_items_projected_label']}"
    assert results["kpi_snapshot_unpack_projection"]["followup_sla_component_projected_label"] == "SLA liên hệ lại đúng lịch hẹn", f"Projected component label wrong! Got {results['kpi_snapshot_unpack_projection']['followup_sla_component_projected_label']}"

    # 7. Unpacked numeric values strictly preserved
    assert results["kpi_snapshot_unpack_projection"]["assigned_intervals_value"] == 15.0
    assert results["kpi_snapshot_unpack_projection"]["unique_assigned_items_value"] == 10.0
    assert results["kpi_snapshot_unpack_projection"]["followup_sla_component_score"] == 85.0

    print("ALL MIGRATION AND KPI RECONCILIATION CHECKS PASSED!")
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    test_migration_and_snapshot_preservation()
