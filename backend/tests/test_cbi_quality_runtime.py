from types import SimpleNamespace

from apps.chat.task import cbi_quality_runtime as runtime


def topic():
    return SimpleNamespace(
        metrics={
            "review_count": SimpleNamespace(label="评价数"),
            "average_rating": SimpleNamespace(label="平均评分"),
        }
    )


def plan():
    return {
        "metrics": ["review_count", "average_rating"],
        "overall_metrics": [],
        "group": "none",
        "detail": False,
        "compare_start": None,
    }


def result(status, metrics, severity="failure"):
    return {
        "rule_id": "rating_range",
        "type": "range",
        "scope": "current",
        "metrics": metrics,
        "status": status,
        "severity": severity,
        "sample_keys": [{"review_id": 36}],
        "sql": "private technical detail",
    }


def test_rating_failure_removes_only_affected_metric_and_preserves_request():
    requested = plan()
    report = {"status": "failed", "results": [result("failed", ["average_rating"])]}
    disposition = runtime.decide(topic(), requested, report)
    assert requested["metrics"] == ["review_count", "average_rating"]
    assert disposition.plan["metrics"] == ["review_count"]
    assert disposition.status == "partial"
    assert disposition.unavailable == [
        {"key": "average_rating", "label": "平均评分", "reason": "quality_blocked"},
    ]


def test_single_required_metric_failure_produces_no_result_plan():
    requested = dict(plan(), metrics=["average_rating"])
    disposition = runtime.decide(
        topic(),
        requested,
        {"status": "failed", "results": [result("failed", ["average_rating"])]},
    )
    assert disposition.plan is None
    assert disposition.status == "blocked"


def test_technical_fields_are_not_in_normal_quality_receipt():
    report = {
        "status": "warning",
        "results": [result("warning", ["average_rating"], severity="warning")],
    }
    disposition = runtime.decide(topic(), plan(), report)
    assert disposition.status == "warning"
    assert disposition.plan == plan()
    safe = runtime.public_evidence(report, disposition, ["5"], "2026-11-17T00:00:00Z")
    assert "sample_keys" not in str(safe)
    assert "private technical detail" not in str(safe)
    assert safe["checks"][0]["affected_metrics"] == ["average_rating"]


def test_unknown_completion_cannot_publish_a_result():
    disposition = runtime.decide(
        topic(), plan(), {"status": "incomplete", "results": []}
    )
    assert disposition.plan is None
    assert disposition.status == "incomplete"
    assert {item["reason"] for item in disposition.unavailable} == {
        "quality_incomplete"
    }


def test_quality_failure_does_not_unbind_grouped_detail():
    requested = dict(plan(), detail=True)
    disposition = runtime.decide(
        topic(),
        requested,
        {"status": "failed", "results": [result("failed", ["average_rating"])]},
    )
    assert disposition.plan is None
    assert {item["key"] for item in disposition.unavailable} == {
        "review_count",
        "average_rating",
    }


def test_empty_scope_is_an_observation_not_a_successful_full_scan():
    requested = plan()
    disposition = runtime.decide(
        topic(), requested, {"status": "no_samples", "results": []}
    )
    assert disposition.plan == requested
    assert disposition.status == "no_samples"


def test_nonempty_scope_with_no_usable_values_does_not_pass_required_check():
    report = {
        "status": "no_samples",
        "results": [result("no_samples", ["average_rating"])],
        "coverage": [{"scope": "current", "checked_count": 2}],
    }
    disposition = runtime.decide(topic(), plan(), report)
    assert disposition.status == "partial"
    assert disposition.unavailable[0]["reason"] == "quality_incomplete"


def test_new_evidence_supersedes_only_the_resumed_scope():
    state = {}
    independent = dict(plan(), group="rating")
    passed = {
        "status": "passed",
        "checked_at": "t1",
        "checks": [],
        "report_ids": ["a"],
        "unavailable_outputs": [],
    }
    blocked = dict(
        passed,
        status="blocked",
        report_ids=["b"],
        unavailable_outputs=[{"key": "average_rating", "reason": "quality_blocked"}],
    )
    runtime.record_evidence(state, plan(), passed)
    runtime.record_evidence(state, independent, blocked)
    assert state["quality"]["status"] == "blocked"
    assert state["quality"]["report_ids"] == ["a", "b"]
    runtime.record_evidence(state, independent, dict(passed, report_ids=["c"]))
    assert state["quality"]["status"] == "passed"
    assert state["quality"]["report_ids"] == ["a", "c"]


def test_explicit_quality_retry_is_versioned_and_idempotent():
    from apps.chat.task.cbi_contract import Action, accept_quality_retry

    state = {
        "task_status": "blocked",
        "quality": {"unavailable_outputs": [{"key": "average_rating"}]},
        "semantic_plan": plan(),
        "events": [],
        "revision": 3,
    }
    request = Action(action="retry_quality", request_id="ready", expected_revision=3)
    updated, fresh = accept_quality_retry(state, request)
    assert fresh and updated["task_status"] == "blocked"
    assert updated["events"][0]["action"] == "retry_quality"
    assert accept_quality_retry(updated, request) == (updated, False)
