"""Deterministic investigation recovery probes at the request worker boundary."""

import json
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.chat.task import cbi_analysis as analysis
from apps.chat.task import cbi_investigation as investigation
from apps.chat.task.cbi_contract import (
    Action,
    ActionRejected,
    Topic,
    accept_investigation_action,
)

REAL_RECORD_STEP = analysis._record_investigation_step


def result_row(key, delta):
    return {
        "key": key,
        "label": key,
        "current": str(100 + delta),
        "baseline": "100",
        "delta": str(delta),
    }


class InvestigationWorker:
    def __init__(self, monkeypatch):
        self.user = SimpleNamespace(id=1)
        self.datasource = SimpleNamespace(id=5)
        self.topic = Topic.model_validate(
            {
                "version": "cfg-fixture",
                "default_definition": "default",
                "source_version": "frozen-fixture",
                "timezone": "Asia/Shanghai",
                "semantic": {
                    "date_field": "business_day",
                    "start": "2026-10-01",
                    "end": "2026-11-01",
                    "default_metric": "billed",
                    "metrics": {
                        "billed": {
                            "op": "sum",
                            "field": "billed_total",
                            "label": "账单",
                            "unit": "元",
                            "description": "账单总额",
                        }
                    },
                    "dimensions": {
                        "category": {"field": "category", "label": "品类"},
                        "product": {"field": "product", "label": "商品"},
                    },
                    "detail": {"order_id": "订单"},
                    "decomposition": {
                        "metrics": {"billed": True},
                        "dimensions": {"category": ["product"], "product": []},
                    },
                },
                "definitions": [
                    {
                        "id": "default",
                        "view": "demo_bi.order_analysis",
                        "label": "订单",
                        "description": "审批订单",
                    }
                ],
            }
        )
        self.plan = {
            "start": "2026-10-01",
            "end": "2026-11-01",
            "compare_start": "2026-09-01",
            "compare_end": "2026-10-01",
            "filters": {},
            "metrics": ["billed"],
            "group": "category",
            "detail": False,
            "top": 25,
            "overall_metrics": [],
        }
        self.proof = {
            "view": "demo_bi.order_analysis",
            "scope": investigation.source_scope(self.plan),
            "rows": 15,
            "digest": "a" * 64,
            "source_version": "frozen-fixture",
        }
        self.chat = SimpleNamespace(id=6)
        self.row = SimpleNamespace(
            id=10,
            chat_id=6,
            data=None,
            chart=None,
            sql=None,
            analysis_state={
                "schema_version": 1,
                "config_version": self.topic.version,
                "revision": 1,
                "request": {"text": "分析变化", "outputs": ["current", "baseline"]},
                "scope": {"definition": "default"},
                "decision": {"kind": "ready"},
                "task_status": "running",
                "semantic_plan": deepcopy(self.plan),
                "source_expectation": deepcopy(self.proof),
                "investigation": investigation.new_record(self.plan),
                "outputs": [],
                "steps": [],
                "events": [],
            },
        )
        self.read = FakeReadSession()
        self.read_scopes = []
        self.materializations = []
        self.attempts = []
        self.saves = []
        self.fail = None
        self.stop_after = None
        self.stop_during_proof = False
        self.stop = Event()
        worker = self

        class Metadata:
            def __init__(self, _):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def get(self, model, _):
                return worker.chat if model is analysis.Chat else worker.row

            def refresh(self, _):
                pass

            def expunge(self, _):
                pass

        def source(_session, user, chat, *, version):
            assert user is self.user and chat is self.chat
            assert version == self.topic.version
            return self.topic, self.datasource

        def read_session(dataset):
            assert dataset is self.datasource
            return self.read

        for name, value in {
            "Session": Metadata,
            "_accessible_topic": source,
            "get_session": read_session,
            "_semantic_proof": self.read_proof,
            "_semantic_materialize": self.materialize,
            "_record_investigation_step": self.record_step,
            "_save": self.save,
        }.items():
            monkeypatch.setattr(analysis, name, value)

    def read_proof(
        self, _session, user, _row, _state, topic, datasource, read, scope, **_policy
    ):
        assert user is self.user and datasource is self.datasource
        assert topic.version == self.topic.version and read is self.read
        self.read_scopes.append(deepcopy(scope))
        if self.stop_during_proof:
            self.stop.set()
        return dict(self.proof, scope=deepcopy(scope))

    def materialize(self, _session, user, row, state, _topic, datasource, read, plan):
        assert user is self.user and datasource is self.datasource and read is self.read
        self.materializations.append(deepcopy(plan))
        state["steps"].append(
            {"kind": "semantic", "status": "verified", "record_id": str(row.id)}
        )
        rows = [result_row("A", 80), result_row("B", -50)]
        view = {
            "title": "品类变化",
            "decomposition": {
                "rows": rows,
                "total": {"current": "230", "baseline": "200", "delta": "30"},
                "reconciled": True,
                "metric": "billed",
                "label": "账单",
                "dimension_label": "品类",
                "unit": "元",
            },
            "table": {"columns": [{"key": "delta", "label": "差额"}], "rows": rows},
        }
        outputs = [{"id": "billed_delta", "status": "verified", "value": "30"}]
        row.data = json.dumps({"data": []})
        return view, outputs, rows

    def record_step(
        self,
        _session,
        user,
        _row,
        _state,
        _topic,
        datasource,
        read,
        step,
        dispatch=None,
    ):
        assert user is self.user and datasource is self.datasource and read is self.read
        if dispatch and not dispatch():
            return None
        self.attempts.append(step["id"])
        if step["id"] == self.fail:
            raise TimeoutError("synthetic timeout")
        step.update(status="verified", record_id=str(20 + len(self.attempts)))
        if step["id"] == self.stop_after:
            self.stop.set()
        return True

    def save(self, _session, row, state, *, finished=True):
        self.saves.append((deepcopy(state), finished))
        row.analysis_state = deepcopy(state)
        row.finish = finished

    def switch_to_child(self, state):
        parent = self.row
        self.row = SimpleNamespace(
            id=parent.id + 1,
            chat_id=parent.chat_id,
            data=parent.data,
            chart=parent.chart,
            sql=parent.sql,
            analysis_state=deepcopy(state),
        )
        return parent

    def run(self):
        result = analysis._run(self.row.id, self.user, stop=self.stop).analysis_state
        return deepcopy(result)

    def action(self, state, name, *, decision_id=None):
        request = Action(
            action=name,
            expected_revision=state["revision"],
            request_id="request-" + name,
            decision_id=decision_id,
        )
        return analysis._investigation_action(
            self.row, state, self.topic, request, "button"
        )


class FakeReadSession:
    def __init__(self):
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, statement, parameters=None):
        self.commands.append((str(statement), parameters))


@pytest.fixture
def worker(monkeypatch):
    return InvestigationWorker(monkeypatch)


def test_failed_step_keeps_checkpoint_and_resume_only_runs_missing_differential(worker):
    worker.fail = "drill-2"
    first = worker.run()

    assert first["task_status"] == "partial"
    assert first["investigation"]["stop_reason"] == "query_failed"
    assert [step["status"] for step in first["investigation"]["steps"]] == [
        "verified",
        "verified",
        "failed",
    ]
    assert len(worker.materializations) == 1
    assert worker.attempts == ["drill-1", "drill-2"]
    assert first["outputs"][0]["value"] == "30"
    assert not any(finished for _, finished in worker.saves[:-1])
    assert worker.saves[-1][1]
    assert (
        worker.read.commands[0][0]
        == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )

    updated, changed, child = worker.action(first, "resume_investigation")

    assert changed and child["task_status"] == "running"
    assert updated["task_status"] == "partial"
    assert child["outputs"] == updated["outputs"] == first["outputs"]
    assert child["request"]["parent_record_id"] == "10"
    parent = worker.switch_to_child(child)
    worker.fail = None
    resumed = worker.run()

    assert resumed["task_status"] == "completed"
    assert len(worker.materializations) == 1
    assert worker.attempts == ["drill-1", "drill-2", "drill-2"]
    assert len(worker.read_scopes) == 2
    assert resumed["investigation"]["findings"] == first["investigation"]["findings"]
    assert resumed["investigation"]["steps"][1] == first["investigation"]["steps"][1]
    assert resumed["outputs"] == first["outputs"]
    assert parent.analysis_state == first


def test_data_change_suspends_old_result_and_reconfirm_checks_again(worker):
    worker.fail = "drill-2"
    original = worker.run()
    _, accepted, child = worker.action(original, "resume_investigation")
    assert accepted
    worker.switch_to_child(child)
    worker.proof["digest"] = "b" * 64
    changed = worker.run()

    pending = changed["investigation"]["source_confirmation"]
    assert (
        changed["task_status"] == "partial"
        and changed["investigation"]["stop_reason"] == "source_changed"
    )
    assert pending["proof"]["digest"] == "b" * 64
    assert changed["outputs"] == original["outputs"]
    assert worker.attempts == ["drill-1", "drill-2"]
    with pytest.raises(ActionRejected, match="investigation_resume_unavailable"):
        accept_investigation_action(
            changed,
            Action(
                action="resume_investigation",
                expected_revision=changed["revision"],
                request_id="another",
            ),
            "button",
        )

    with pytest.raises(ActionRejected, match="stale_decision"):
        worker.action(changed, "confirm_investigation_source", decision_id="stale")
    parent, confirmed, child = worker.action(
        changed, "confirm_investigation_source", decision_id=pending["id"]
    )
    assert confirmed and child is not None
    assert parent["outputs"] == changed["outputs"]
    assert not child["outputs"] and not child["investigation"]["steps"]
    assert child["source_expectation"] == pending["proof"]
    assert child["request"]["parent_record_id"] == "11"

    worker.switch_to_child(child)
    worker.proof["digest"] = "c" * 64
    next_change = worker.run()
    second = next_change["investigation"]["source_confirmation"]
    assert second["id"] != pending["id"] and second["proof"]["digest"] == "c" * 64
    assert worker.attempts == ["drill-1", "drill-2"]
    assert len(worker.materializations) == 1


def test_cancel_changed_source_keeps_verified_old_evidence(worker):
    worker.fail = "drill-2"
    original = worker.run()
    _, accepted, child = worker.action(original, "resume_investigation")
    assert accepted
    worker.switch_to_child(child)
    worker.proof["digest"] = "b" * 64
    changed = worker.run()
    pending = changed["investigation"]["source_confirmation"]
    result, accepted, child = worker.action(
        changed, "cancel_investigation_source", decision_id=pending["id"]
    )
    assert accepted and child is None
    assert result["task_status"] == "partial"
    assert not result["investigation"].get("source_confirmation")
    assert result["outputs"] == original["outputs"]
    assert worker.attempts == ["drill-1", "drill-2"]
    assert len(worker.materializations) == 1


def test_stop_before_run_bypasses_business_queries(worker):
    worker.stop.set()

    state = worker.run()

    assert (
        state["task_status"] == "partial"
        and state["investigation"]["stop_reason"] == "stopped"
    )
    assert (
        not worker.materializations and not worker.attempts and not worker.read_scopes
    )


def test_stop_after_verified_child_preserves_receipts_and_does_not_dispatch_next(
    worker,
):
    worker.stop_after = "drill-1"
    state = worker.run()

    assert (
        state["task_status"] == "partial"
        and state["investigation"]["stop_reason"] == "stopped"
    )
    assert worker.attempts == ["drill-1"]
    assert [item["status"] for item in state["investigation"]["steps"]] == [
        "verified",
        "verified",
        "pending",
    ]
    assert state["outputs"][0]["value"] == "30"


def test_budget_exhaustion_marks_partial_and_preserves_root_partition(
    worker, monkeypatch
):
    monkeypatch.setattr(
        investigation, "time_remaining", lambda _: 0.4 if worker.attempts else 54
    )

    state = worker.run()

    assert state["task_status"] == "partial"
    assert state["investigation"]["stop_reason"] == "budget_exhausted"
    assert worker.attempts == ["drill-1"]
    assert state["outputs"][0]["value"] == "30"
    assert all(
        command[1] is None or int(command[1]["timeout"]) <= 20_000
        for command in worker.read.commands
    )


def test_source_proof_denies_before_allocating_record_or_sending_read(monkeypatch):
    topic = SimpleNamespace(
        by_id=lambda key: SimpleNamespace(id=key, view="demo_bi.order_analysis"),
        semantic=SimpleNamespace(fields={"business_day", "billed_total"}),
    )
    state = {"scope": {"definition": "default"}, "steps": []}
    monkeypatch.setattr(
        investigation,
        "source_query",
        lambda semantic, view, scope: ("statement", "sql", semantic.fields),
    )
    checked = []

    def denied(session, user, datasource, assets):
        checked.append((session, user, datasource, assets))
        raise HTTPException(403, "analysis_row_policy_unsupported")

    def unexpected(*_):
        pytest.fail("Unauthorized source proof reached record creation or SQL")

    monkeypatch.setattr(analysis, "_require_assets", denied)
    monkeypatch.setattr(analysis, "_execute", unexpected)
    monkeypatch.setattr(analysis, "_semantic_internal", unexpected)

    with pytest.raises(HTTPException, match="analysis_row_policy_unsupported"):
        analysis._semantic_proof(
            "metadata", "user", "record", state, topic, "dataset", "read", {}
        )
    assert checked == [
        (
            "metadata",
            "user",
            "dataset",
            {"demo_bi.order_analysis": {"business_day", "billed_total"}},
        )
    ]
    assert not state["steps"]


def test_source_proof_compiles_exact_text_digest_in_two_periods(worker):
    scope = dict(worker.proof["scope"], filters={"category": ["A", "B"]})
    _, sql, fields = investigation.source_query(
        worker.topic.semantic, worker.proof["view"], scope
    )

    assert {"business_day", "billed_total", "order_id", "category", "product"} == fields
    assert "row_to_json(source_rows)" in sql
    assert "string_agg(" in sql and "ORDER BY" in sql and "sha256(" in sql
    assert sql.count("sha256(") == 2 and "DISTINCT" not in sql
    assert "LIMIT 50001" in sql
    assert "2026-09-01" in sql and "2026-10-01" in sql and "2026-11-01" in sql
    assert "demo_bi.order_analysis.category IN ('A', 'B')" in sql


def test_stop_during_original_scope_proof_prevents_narrowed_scope_query(worker):
    worker.row.analysis_state["semantic_plan"]["filters"] = {"category": "A"}
    worker.stop_during_proof = True

    state = worker.run()

    assert worker.read_scopes == [worker.proof["scope"]]
    assert not worker.materializations and not worker.attempts
    assert state["task_status"] == "partial"
    assert state["investigation"]["stop_reason"] == "stopped"


def test_members_resume_keeps_verified_current_period_after_baseline_failure(
    monkeypatch, worker
):
    materialized = []
    reports = []
    checkpointed = []
    failures = {"2026-09-01"}
    by_period = {"2026-10-01": "current", "2026-09-01": "baseline"}
    state = worker.row.analysis_state
    state["investigation"]["steps"] = [
        {
            "id": "members",
            "kind": "members",
            "status": "pending",
            "query_plan": deepcopy(worker.plan),
        }
    ]
    rows = iter([21, 22, 23])

    def internal(*_):
        return SimpleNamespace(id=next(rows))

    def query(_session, user, record, _state, _topic, datasource, read, plan):
        assert (
            user is worker.user
            and datasource is worker.datasource
            and read is worker.read
        )
        assert plan["detail"] and not plan["compare_start"] and not plan["compare_end"]
        assert plan["filters"] == worker.plan["filters"]
        name = by_period[plan["start"]]
        materialized.append(name)
        if plan["start"] in failures:
            raise TimeoutError("baseline failed")
        reports.append(record.id)
        return (
            {
                "table": {
                    "columns": [{"key": "order_id"}],
                    "rows": [{"order_id": record.id}],
                },
                "truncated": False,
            },
            [],
            [],
        )

    def checkpoint(_session, _row, state, *, finished):
        assert not finished
        checkpointed.append(deepcopy(state))

    monkeypatch.setattr(analysis, "_semantic_internal", internal)
    monkeypatch.setattr(analysis, "_semantic_materialize", query)
    monkeypatch.setattr(analysis, "_save", checkpoint)

    def read_members(state):
        step = state["investigation"]["steps"][0]
        return REAL_RECORD_STEP(
            None,
            worker.user,
            worker.row,
            state,
            worker.topic,
            worker.datasource,
            worker.read,
            step,
            lambda: True,
        )

    with pytest.raises(TimeoutError, match="baseline failed"):
        read_members(state)
    assert len(checkpointed) == 1
    restored = deepcopy(checkpointed[-1])
    assert restored["investigation"]["steps"][0]["records"] == ["21"]

    failures.clear()
    assert read_members(restored)
    result = restored["investigation"]["steps"][0]
    assert result["status"] == "verified"
    assert [item["period"] for item in result["tables"]] == ["current", "baseline"]
    assert result["records"] == ["21", "23"]
    assert materialized == ["current", "baseline", "baseline"] and reports == [21, 23]


@pytest.mark.parametrize(
    "change",
    [
        {"start": "2026-10-02"},
        {"filters": {"category": None}},
        {"filters": {"category": ["A", "C"]}},
    ],
)
def test_investigation_cannot_expand_the_selected_parent(worker, change):
    original = dict(worker.plan, group="none", filters={"category": ["A", "B"]})
    parent = {
        "task_status": "completed",
        "source_proof": worker.proof,
        "semantic_plan": original,
        "outputs": [
            {"id": f"billed_{key}", "status": "verified", "value": value}
            for key, value in (("current", 230), ("baseline", 200))
        ],
        "presentation": {},
    }
    state = {"request": {}}
    intent = analysis.cbi_semantic.SemanticIntent(
        operation="revise", group="category", investigate=True, **change
    )
    child = analysis._semantic_state(
        worker.topic, state, intent, parent, 9, selected=True
    )
    assert child["decision"]["reason"] == "semantic_scope"
    assert "investigation" not in child


def test_selected_group_drill_uses_the_original_source_proof(worker):
    parent = {
        "task_status": "completed",
        "source_proof": worker.proof,
        "semantic_plan": worker.plan,
        "outputs": [
            {"id": f"billed_{key}", "status": "verified", "value": value}
            for key, value in (("current", 230), ("baseline", 200))
        ],
        "presentation": {},
    }
    intent = analysis.cbi_semantic.SemanticIntent(
        operation="revise", group="product", filters={"category": "A"}
    )
    child = analysis._semantic_state(
        worker.topic, {"request": {}}, intent, parent, 9, selected=True
    )
    assert child["investigation"]["plan"]["first_dimension"] == "product"
    assert child["source_expectation"] == worker.proof
    assert child["semantic_plan"]["compare_start"] == worker.plan["compare_start"]
    assert child["semantic_plan"]["filters"] == {"category": "A"}


def test_same_source_cannot_verify_a_different_total(worker):
    state = worker.row.analysis_state
    state["presentation"] = {}
    state["investigation"]["parent_total"] = {
        "current": "200",
        "baseline": "200",
        "delta": "0",
    }
    result = worker.run()
    assert result["task_status"] == "partial"
    assert result["investigation"]["stop_reason"] == "decomposition_unreconciled"
    assert not result["investigation"]["findings"]
    assert not result["presentation"].get("decomposition")
    assert not worker.attempts


def test_child_total_uses_only_configured_tolerance():
    expected = {"current": "230", "baseline": "200", "delta": "30"}
    result = {
        "reconciled": True,
        "coverage": {"tolerance": "0.001"},
        "total": {"current": "230.0004", "baseline": "200", "delta": "30.0004"},
    }
    assert analysis._decomposition_matches(result, expected)
    result["total"]["delta"] = "30.002"
    assert not analysis._decomposition_matches(result, expected)


def test_transaction_setup_error_persists_recoverable_failure(worker, monkeypatch):
    def unavailable(_):
        raise TimeoutError("cannot obtain a database connection")

    monkeypatch.setattr(analysis, "get_session", unavailable)
    result = worker.run()
    assert result["task_status"] == "blocked"
    assert result["investigation"]["stop_reason"] == "query_failed"
    assert not worker.read_scopes
    assert worker.row.finish


def selected_comparison(
    worker, *, investigate=False, filters=None, parent_filters=None
):
    original = dict(worker.plan, group="none", filters=parent_filters or {})
    proof = dict(worker.proof, scope=investigation.source_scope(original))
    parent = {
        "task_status": "completed",
        "source_proof": proof,
        "semantic_plan": original,
        "outputs": [
            {"id": f"billed_{key}", "status": "verified", "value": value}
            for key, value in (("current", 230), ("baseline", 200))
        ],
        "presentation": {},
    }
    intent = analysis.cbi_semantic.SemanticIntent(
        operation="revise", group="category", investigate=investigate, filters=filters
    )
    return analysis._semantic_state(
        worker.topic,
        {
            "request": {"text": "按品类比较"},
            "outputs": [],
            "steps": [],
            "revision": 1,
            "config_version": worker.topic.version,
            "schema_version": 1,
            "events": [],
        },
        intent,
        parent,
        9,
        selected=True,
    )


def test_group_only_completes_even_if_unrequested_drill_is_unavailable(worker):
    comparison = selected_comparison(worker)
    worker.switch_to_child(comparison)
    worker.fail = "drill-1"
    result = worker.run()
    assert result["task_status"] == "completed"
    assert result["source_proof"] == worker.proof
    assert result["request"]["parent_record_id"] == "9"
    assert [step["id"] for step in result["investigation"]["steps"]] == [
        "decomposition"
    ]
    assert worker.read_scopes == [worker.proof["scope"]]
    assert worker.attempts == []


def test_explicit_investigation_keeps_required_drill_and_resumes_only_missing(worker):
    comparison = selected_comparison(worker, investigate=True)
    worker.switch_to_child(comparison)
    worker.fail = "drill-2"
    result = worker.run()
    assert result["task_status"] == "partial"
    assert result["request"]["parent_record_id"] == "9"
    assert [step["status"] for step in result["investigation"]["steps"]] == [
        "verified",
        "verified",
        "failed",
    ]
    worker.fail = None
    _, _, resumed = worker.action(result, "resume_investigation")
    worker.switch_to_child(resumed)
    final = worker.run()
    assert final["task_status"] == "completed"
    assert final["presentation"] == result["presentation"]
    assert final["investigation"]["comparison_record_id"] == "9"
    assert len(worker.materializations) == 1
    assert worker.attempts == ["drill-1", "drill-2", "drill-2"]


def test_group_only_confirmed_source_change_keeps_single_step(worker):
    comparison = selected_comparison(worker)
    worker.switch_to_child(comparison)
    worker.proof["digest"] = "b" * 64
    pending = worker.run()
    assert pending["investigation"]["stop_reason"] == "source_changed"
    assert worker.materializations == []
    _, _, renewed = worker.action(
        pending,
        "confirm_investigation_source",
        decision_id=pending["investigation"]["source_confirmation"]["id"],
    )
    worker.switch_to_child(renewed)
    final = worker.run()
    assert final["task_status"] == "completed"
    assert final["source_proof"]["digest"] == "b" * 64
    assert len(worker.materializations) == 1
    assert worker.attempts == []


def test_group_only_cannot_silently_expand_selected_parent_filters(worker):
    comparison = selected_comparison(
        worker, filters={"category": None}, parent_filters={"category": "A"}
    )
    assert comparison["decision"]["reason"] == "semantic_scope"
    assert "investigation" not in comparison


def test_narrowed_single_step_resume_rechecks_original_parent_source(
    worker, monkeypatch
):
    child = selected_comparison(worker, filters={"category": "A"})
    worker.switch_to_child(child)
    original_scope = worker.proof["scope"]
    materialize = worker.materialize

    def fail_root(*_args):
        raise TimeoutError("first grouped query stalled after source proof")

    monkeypatch.setattr(analysis, "_semantic_materialize", fail_root)
    failed = worker.run()
    assert failed["investigation"]["stop_reason"] == "query_failed"
    assert failed["source_proof"]["scope"]["filters"] == {"category": "A"}
    _, _, resumed = worker.action(failed, "resume_investigation")
    worker.switch_to_child(resumed)

    def source_only_parent_changed(
        session, user, row, state, topic, datasource, read, scope
    ):
        proof = worker.read_proof(
            session, user, row, state, topic, datasource, read, scope
        )
        return dict(proof, digest="c" * 64) if scope == original_scope else proof

    monkeypatch.setattr(analysis, "_semantic_proof", source_only_parent_changed)
    monkeypatch.setattr(analysis, "_semantic_materialize", materialize)
    result = worker.run()
    assert result["task_status"] == "partial"
    assert result["investigation"]["stop_reason"] == "source_changed"
    assert result["investigation"]["comparison_record_id"] == "9"
    assert worker.materializations == []


def test_revoked_parent_comparison_stops_before_source_or_data_sql(worker, monkeypatch):
    child = selected_comparison(worker)
    worker.switch_to_child(child)

    def revoked(*_args, **_kwargs):
        raise HTTPException(403, "permission_denied")

    monkeypatch.setattr(analysis, "_accessible_topic", revoked)
    result = worker.run()
    assert result["task_status"] == "blocked"
    assert worker.read_scopes == []
    assert worker.materializations == []
