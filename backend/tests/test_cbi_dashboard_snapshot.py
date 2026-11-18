import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main  # noqa: F401
from fastapi import HTTPException
from apps.dashboard.crud.dashboard_service import _freeze_adapted_views, load_resource


class DashboardSnapshotTest(unittest.TestCase):
    def test_snapshot_does_not_execute_primary_query(self):
        state = {
            "schema_version": 1,
            "steps": [{"kind": "summary", "status": "verified", "record_id": "81"}],
        }
        class Result:
            def mappings(self):
                return self

            def first(self):
                return {
                    "create_by": "1",
                    "workspace_id": "1",
                    "canvas_view_info": json.dumps(
                        {
                            "c1": {
                                "analysis_state": state,
                                "datasource": 2,
                                "sql": "SELECT 12",
                                "data": {
                                    "data": [{"amount": 12}]
                                },
                            }
                        }
                    ),
                }

        class Session:
            def execute(self, *args):
                return Result()

            def get(self, model, id_):
                if id_ == 81:
                    return SimpleNamespace(
                        id=id_, chat_id=12, datasource=2, analysis_state=state, sql="SELECT 9",
                        data=json.dumps({"data": [{"amount": 9, "definition": "comparison"}]}),
                    )

        class Owner:
            id = 1
            oid = 1

        class Dashboard:
            id = "fixture"

        with (
            patch("apps.dashboard.crud.dashboard_service.get_chart_data_ds") as execute,
            patch("apps.dashboard.crud.dashboard_service.check_saved_access") as check,
        ):
            result = load_resource(Session(), Dashboard(), Owner())
            self.assertEqual(check.call_args.args[-1].id, 81)
            check.side_effect = HTTPException(403)
            with self.assertRaises(HTTPException) as denied:
                load_resource(Session(), Dashboard(), Owner())
            self.assertEqual(denied.exception.status_code, 403)
        chart = json.loads(result["canvas_view_info"])["c1"]
        self.assertEqual(chart["record_id"], "81")
        self.assertEqual(
            chart["data"]["data"], [{"amount": 9, "definition": "comparison"}]
        )
        execute.assert_not_called()

    def test_funnel_presentation_comes_from_the_saved_run(self):
        marker = {
            "run_id": "saved", "task_id": "12", "method": "ordered_attempt_funnel",
            "record_id": "81", "window": "24h", "numerator": 1, "denominator": 3,
            "rate_pct": 33.333333, "status": "verified",
        }
        session = Mock()
        session.execute.return_value.mappings.return_value.first.return_value = {
            "create_by": "1", "workspace_id": "1",
            "canvas_view_info": json.dumps({"c1": {
                "datasource": 4, "analysis_state": {"multi": marker}, "method_result": {"counts": [8]},
            }}),
        }
        session.get.side_effect = [
            SimpleNamespace(id=81, chat_id=12, datasource=4, sql=None,
                            analysis_state={"multi": marker}, data='{"data": []}'),
            SimpleNamespace(state={"work": "newer", "runs": {"saved": {
                "definition": {"plan": {
                    "window_hours": 24, "entry_start": "2026-11-01", "entry_end": "2026-11-02",
                    "observation_asof": "2026-11-04", "unit": "attempt",
                }},
                "result": {"counts": [3, 2, 1], "each_of_entry_pct": [100, 66.666667, 33.333333], "immature": 0},
                "comparison": {"base_run": "earlier", "delta_pp": 12.345678},
            }, "earlier": {"definition": {"plan": {"window_hours": 12}}}}}),
        ]
        with (
            patch("apps.dashboard.crud.dashboard_service.get_chart_data_ds") as execute,
            patch("apps.dashboard.crud.dashboard_service.check_saved_access"),
        ):
            result = load_resource(session, SimpleNamespace(id="fixture"), SimpleNamespace(id=1, oid=1))
        card = json.loads(result["canvas_view_info"])["c1"]
        self.assertEqual(card["method_result"]["counts"], [3, 2, 1])
        self.assertEqual(card["method_result"]["window_hours"], 24)
        self.assertEqual(card["method_result"]["each_of_entry_pct"][-1], 33.333333)
        self.assertEqual(card["method_result"]["comparison"],
                         {"run_id": "earlier", "window_hours": 12, "delta_pp": 12.345678})
        self.assertEqual(card["chat_id"], "12")
        execute.assert_not_called()


    def test_fixed_canvas_uses_canonical_snapshot_and_keeps_it_during_layout_edit(self):
        state = {"task_status": "partial",
                 "steps": [{"kind": "trend", "status": "verified", "record_id": "81"}]}
        session = Mock()
        record = SimpleNamespace(id=81, chat_id=12, datasource=2, analysis_state=state,
                                 sql="SELECT 9", data='{"data": [{"amount": 9}]}')
        session.get.return_value = record
        owner = SimpleNamespace(id=1, oid=1)
        submitted = json.dumps({"c1": {
            "record_id": "81", "datasource": 2, "analysis_state": {"task_status": "done"},
            "snapshot_frozen": True, "data": {"data": [{"amount": 999}]},
        }})
        with patch("apps.dashboard.crud.dashboard_service.check_saved_access") as authorize:
            snapshot = _freeze_adapted_views(session, owner, submitted)
            record.analysis_state = dict(state, task_status="completed")
            edited = _freeze_adapted_views(session, owner, submitted, snapshot)
            session.execute.return_value.mappings.return_value.first.return_value = {
                "create_by": "1", "workspace_id": "1", "canvas_view_info": edited,
            }
            result = load_resource(session, SimpleNamespace(id="fixture"), owner)
        frozen = json.loads(result["canvas_view_info"])["c1"]
        self.assertEqual(frozen["analysis_state"]["task_status"], "partial")
        self.assertEqual(frozen["data"]["data"], [{"amount": 9}])
        self.assertEqual(authorize.call_count, 3)
