import json, random, time
import frappe

PREFIX = "P0WL20261027-"

def run():
    db = frappe.db.sql("SELECT VERSION(), @@character_set_server, @@collation_server, DATABASE()")[0]
    db_collation = frappe.db.sql("SELECT DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE()")[0][0]
    table_collation = frappe.db.sql("SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabCRM Lead'")[0][0]
    mobile_indexes = frappe.db.sql("SHOW INDEX FROM `tabCRM Lead` WHERE Column_name='mobile_no'", as_dict=True)
    before = frappe.db.sql("SELECT COUNT(*) FROM `tabCRM Lead` WHERE first_name LIKE %s", (PREFIX + "%",))[0][0]
    if before:
        raise RuntimeError(f"refusing to overwrite existing workload rows: {before}")
    owners = [u.name for u in frappe.get_all("User", filters={"name": ["in", ["staff.a@dev.local", "staff.b@dev.local", "staff.c@dev.local", "team1.leader@dev.local", "team2.leader@dev.local"]]}, fields=["name"])]
    if not owners:
        owners = ["Administrator"]
    sources = [s.name for s in frappe.get_all("CRM Lead Source", fields=["name"], limit_page_length=100)]
    rng = random.Random(42)
    t0 = time.perf_counter()
    ok = errors = 0
    first_errors = []
    try:
        for i in range(1, 5001):
            values = {
                "doctype": "CRM Lead",
                "naming_series": "CRM-LEAD-",
                "first_name": f"{PREFIX}{i:05d}",
                "lead_name": f"P0 Workload {i:05d}",
                "mobile_no": f"0900{i:06d}",
                "status": rng.choice(["New", "Contacted", "Nurture", "Qualified", "Converted"]),
                "lead_owner": rng.choice(owners),
            }
            if sources:
                values["source"] = rng.choice(sources)
            try:
                frappe.get_doc(values).insert(ignore_permissions=True)
                ok += 1
                if ok % 1000 == 0:
                    print(f"progress inserted={ok} errors={errors}", flush=True)
            except Exception as exc:
                errors += 1
                if len(first_errors) < 3:
                    first_errors.append(type(exc).__name__ + ": " + str(exc)[:180])
        frappe.db.commit()
        elapsed = round(time.perf_counter() - t0, 2)
        count = frappe.db.sql("SELECT COUNT(*) FROM `tabCRM Lead` WHERE first_name LIKE %s", (PREFIX + "%",))[0][0]
        explain = frappe.db.sql("EXPLAIN SELECT name FROM `tabCRM Lead` WHERE mobile_no=%s", ("09000001010",), as_dict=True)
        accent_cmp = frappe.db.sql("SELECT STRCMP(%s, %s)", ("Khach hang", "Khách hàng"))[0][0]
        result = {
            "site": frappe.local.site,
            "db_version": db[0],
            "character_set_server": db[1],
            "collation_server": db[2],
            "database": db[3],
            "database_collation": db_collation,
            "crm_lead_table_collation": table_collation,
            "mobile_no_indexes_before": len(mobile_indexes),
            "inserted_ok": ok,
            "insert_errors": errors,
            "first_error_types": first_errors,
            "observed_workload_rows": count,
            "insert_seconds": elapsed,
            "explain_mobile_no": explain,
            "accent_insensitive_strcmp": accent_cmp,
            "cleaned_rows": None,
        }
    finally:
        frappe.db.sql("DELETE FROM `tabCRM Lead` WHERE first_name LIKE %s", (PREFIX + "%",))
        frappe.db.commit()
    result["cleaned_rows"] = frappe.db.sql("SELECT COUNT(*) FROM `tabCRM Lead` WHERE first_name LIKE %s", (PREFIX + "%",))[0][0]
    print(json.dumps(result, ensure_ascii=False, default=str, sort_keys=True))
