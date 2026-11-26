"""Host regressions: execute the real aggregate SQL against isolated synthetic rows.

Frappe and the existing permission resolver are stubbed at the boundary. The
separate site tests exercise real Frappe authorization and ownership services.
Run: python -m unittest discover -s deploy/release -p test_worker_overview.py -v
"""

import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "apps/crm_cskh/crm_cskh/services/worker_overview.py"
)


class SqliteDB:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.queries = []
        self.connection.executescript("""
		create table `tabCSKH Work Item` (name text, lead text, status text,
		 assigned_to text, attempt_count int, connected_attempt_count int, open_callback_task text);
		create table `tabCRM Task` (name text, cskh_work_item text, cskh_is_followup int, status text, due_date text);
		create table `tabCRM Lead` (name text, cskh_do_not_contact int);
		create table `tabCSKH SLA Obligation` (work_item text, owner_user text,
		 status text, is_paused int, informational_only int, effective_due_at text);
		""")

    def sql(self, query, params=(), as_dict=False):
        self.queries.append((query, params))
        bound = tuple(
            str(value) if isinstance(value, datetime) else value
            for value in (params or ())
        )
        rows = self.connection.execute(query.replace("%s", "?"), bound).fetchall()
        return [dict(row) if as_dict else tuple(row) for row in rows]

    def count(self, doctype, filters):
        return self.connection.execute(
            "select count(*) from `tabCRM Lead` where cskh_do_not_contact=1"
        ).fetchone()[0]

    def work(self, name, user, attempts=0, connected=0, status="assigned", dnc=0):
        self.connection.execute(
            "insert into `tabCSKH Work Item` values (?,?,?,?,?,?,null)",
            (name, "lead-" + name, status, user, attempts, connected),
        )
        self.connection.execute(
            "insert into `tabCRM Lead` values (?,?)", ("lead-" + name, dnc)
        )

    def callback(self, work, name, due, status="Todo", current=True):
        self.connection.execute(
            "insert into `tabCRM Task` values (?,?,1,?,?)",
            (name, work, status, str(due)),
        )
        if current:
            self.connection.execute(
                "update `tabCSKH Work Item` set open_callback_task=? where name=?",
                (name, work),
            )


class WorkerOverviewHostTests(unittest.TestCase):
    def setUp(self):
        self.db = SqliteDB()
        self.now = datetime(2026, 11, 26, 0, 5)
        self.scope = "self"
        self.user_timezone = "Asia/Ho_Chi_Minh"
        self.sla_enabled = True
        frappe = types.ModuleType("frappe")
        frappe.db = self.db
        utils = types.ModuleType("frappe.utils")
        utils.get_datetime = (
            lambda value: value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value))
        )
        utils.now_datetime = lambda: self.now
        permissions = types.ModuleType("crm_cskh.p2_permissions")

        def authorize(actor, allow_bod=False):
            if actor in ("Guest", "outsider"):
                raise PermissionError("Forbidden")
            return actor

        permissions.assert_workflow_actor = authorize
        permissions.leader_authorized_work_item_sql = (
            lambda actor, alias: f"{alias}.assigned_to in ('staff-a','staff-b')"
        )
        permissions.staff_authorized_work_item_sql = (
            lambda actor, alias: f"{alias}.assigned_to = 'staff-a'"
        )
        reporting = types.SimpleNamespace(
            _scope=lambda actor: {"scope": self.scope, "user": actor}
        )
        reporting._user_filter = (
            lambda scope, column: ("", [])
            if scope["scope"] in ("all", "system")
            else (
                (column + " in (%s,%s)", ["staff-a", "staff-b"])
                if scope["scope"] == "team"
                else (column + " = %s", [scope["user"]])
            )
        )
        cache = types.SimpleNamespace(
            get_cached_system_timezone=lambda: "Asia/Ho_Chi_Minh",
            get_user_auth_profile=lambda actor: {"time_zone": self.user_timezone},
            has_column=lambda *args: True,
            table_exists=lambda *args: True,
        )
        services = types.ModuleType("crm_cskh.services")
        services.reporting = reporting
        services.runtime_cache = cache
        flags = types.ModuleType("crm_cskh.services.p3_flags")
        flags.is_p3_sla_enabled = lambda: self.sla_enabled
        spec = importlib.util.spec_from_file_location(
            "worker_overview_under_test", SOURCE
        )
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(
            sys.modules,
            {
                "frappe": frappe,
                "frappe.utils": utils,
                "crm_cskh": types.ModuleType("crm_cskh"),
                "crm_cskh.p2_permissions": permissions,
                "crm_cskh.services": services,
                "crm_cskh.services.p3_flags": flags,
            },
        ):
            spec.loader.exec_module(self.module)

    def tearDown(self):
        self.db.connection.close()

    def test_full_totals_are_not_limited_to_first_fifty_records_and_team_scope(self):
        for index in range(61):
            self.db.work(f"a{index}", "staff-a", int(index > 39), int(index > 49))
        for index in range(3):
            self.db.work(f"b{index}", "staff-b")
        for index in range(8):
            self.db.work(f"foreign{index}", "staff-c")
        self.db.work("closed", "staff-a", status="closed")
        own = self.module.get_overview("staff-a")
        self.assertEqual(
            own["workload"],
            {
                "active": 61,
                "contact_records": 61,
                "unattempted": 40,
                "attempted": 21,
                "connected": 11,
                "attempted_unconnected": 10,
            },
        )
        self.scope = "team"
        self.assertEqual(self.module.get_overview("leader-a")["workload"]["active"], 64)
        self.scope = "all"
        self.assertEqual(self.module.get_overview("bod")["workload"]["active"], 72)

    def test_day_start_included_next_day_excluded_and_overdue_overlap_explicit(self):
        start = self.now.replace(hour=0, minute=0)
        for name, due in (
            ("old", start - timedelta(seconds=1)),
            ("start", start),
            ("end", start + timedelta(days=1)),
        ):
            self.db.work(name, "staff-a")
            self.db.callback(name, "task-" + name, due)
        self.db.callback("start", "canceled", start, status="Canceled", current=False)
        self.db.callback("start", "stale-open", start, current=False)
        result = self.module.get_overview("staff-a")
        self.assertEqual(result["callbacks"], {"open": 3, "today": 1, "overdue": 2})
        self.assertTrue(result["definitions"]["callbacks_today_includes_overdue_today"])

    def test_user_timezone_day_is_converted_to_site_database_time(self):
        self.user_timezone = "America/Los_Angeles"
        result = self.module.get_overview("staff-a")
        self.assertEqual(result["day"]["date"], "2026-11-25")
        self.assertEqual(result["day"]["from"], "2026-11-25T15:00:00")
        self.assertEqual(result["day"]["to_exclusive"], "2026-11-26T15:00:00")

    def test_dst_day_has_twenty_three_hours(self):
        start, end, local = self.module.day_window(
            datetime(2026, 3, 8, 18), "Asia/Ho_Chi_Minh", "America/New_York"
        )
        self.assertEqual((end - start).total_seconds(), 23 * 3600)
        self.assertEqual(str(local.date()), "2026-03-08")

    def test_bod_payload_is_aggregate_only_and_dnc_is_distinct(self):
        self.scope = "all"
        self.db.work("PRIVATE-WORK-RECORD", "staff-a", dnc=1)
        result = self.module.get_overview("bod")
        self.assertEqual(result["do_not_contact"], 1)
        serialized = json.dumps(result)
        for secret in (
            "PRIVATE-WORK-RECORD",
            "staff-a",
            "lead-",
            "mobile_no",
            "display_name",
        ):
            self.assertNotIn(secret, serialized)

    def test_dnc_contact_count_is_distinct_and_foreign_scope_excluded(self):
        self.db.work("own", "staff-a", dnc=1)
        self.db.work("foreign", "staff-c", dnc=1)
        self.db.connection.execute(
            "insert into `tabCSKH Work Item` values ('old','lead-own','closed','staff-a',1,1,null)"
        )
        self.assertEqual(self.module.get_overview("staff-a")["do_not_contact"], 1)

    def test_sla_excludes_paused_informational_foreign_and_previous_owner(self):
        self.db.work("a", "staff-a")
        self.db.work("c", "staff-c")
        for work, owner, paused, info in (
            ("a", "staff-a", 0, 0),
            ("a", "staff-a", 1, 0),
            ("a", "staff-a", 0, 1),
            ("a", "previous-owner", 0, 0),
            ("c", "staff-c", 0, 0),
        ):
            self.db.connection.execute(
                "insert into `tabCSKH SLA Obligation` values (?,?,'open',?,?,?)",
                (work, owner, paused, info, str(self.now - timedelta(minutes=1))),
            )
        self.assertEqual(
            self.module.get_overview("staff-a")["sla"], {"pending": 1, "overdue": 1}
        )
        self.sla_enabled = False
        self.assertIsNone(self.module.get_overview("staff-a")["sla"])

    def test_guest_and_unauthorized_roles_fail_before_any_query(self):
        for actor in ("Guest", "outsider"):
            with self.assertRaises(PermissionError):
                self.module.get_overview(actor)
        self.assertEqual(self.db.queries, [])

    def test_distinct_worker_records_separated_from_work_items_and_scoped_bottlenecks(
        self,
    ):
        self.db.work("w1", "staff-a", attempts=0, status="assigned")
        # Second active work item pointing to the same canonical worker lead
        self.db.connection.execute(
            "insert into `tabCSKH Work Item` values ('w1-second','lead-w1','on_hold','staff-a',1,0,null)"
        )
        self.db.work("w2", "staff-b", attempts=0, status="assigned")
        self.db.callback(
            "w1-second", "task-overdue-a", self.now - timedelta(minutes=10)
        )
        self.db.work("w-foreign", "staff-c", attempts=0, status="on_hold")
        self.db.callback(
            "w-foreign", "task-overdue-c", self.now - timedelta(minutes=10)
        )

        own = self.module.get_overview("staff-a")
        self.assertEqual(own["workload"]["active"], 2)
        self.assertEqual(own["workload"]["contact_records"], 1)
        self.assertEqual(
            own["bottlenecks"],
            {
                "on_hold": 1,
                "active_assignees": 1,
                "assignees_with_unattempted": 1,
                "assignees_with_overdue": 1,
            },
        )

        self.scope = "team"
        team = self.module.get_overview("leader-a")
        self.assertEqual(team["workload"]["active"], 3)
        self.assertEqual(team["workload"]["contact_records"], 2)
        self.assertEqual(
            team["bottlenecks"],
            {
                "on_hold": 1,
                "active_assignees": 2,
                "assignees_with_unattempted": 2,
                "assignees_with_overdue": 1,
            },
        )
        self.assertNotIn("staff-a", json.dumps(team))


if __name__ == "__main__":
    unittest.main()
