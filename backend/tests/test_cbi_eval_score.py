from copy import deepcopy

import pytest

from apps.chat.task import cbi_eval_score

PLAN = {
    "start": "2026-10-01",
    "end": "2026-11-01",
    "compare_start": "2026-09-01",
    "compare_end": "2026-10-01",
    "metrics": ["billed"],
    "filters": {},
    "group": "none",
    "detail": False,
}
OUTPUTS = [
    {
        "id": "billed_current",
        "status": "verified",
        "unit": "示例单位",
        "value": "20555.387091195506605",
        "tolerance": "0.005",
    },
    {
        "id": "billed_baseline",
        "status": "verified",
        "unit": "示例单位",
        "value": "19139.126159638397345",
        "tolerance": "0.005",
    },
    {
        "id": "billed_delta",
        "status": "verified",
        "unit": "示例单位",
        "value": "1416.260931557109260",
        "tolerance": "0.005",
    },
]


def receipt(record_id, fields):
    return {
        "kind": "semantic",
        "status": "verified",
        "record_id": record_id,
        "fields": fields,
    }


@pytest.fixture
def comparison():
    expected = {
        "task_status": "completed",
        "plan": PLAN,
        "outputs": OUTPUTS,
        "query_scope": {"business": 1, "drill": 0},
    }
    steps = [
        dict(receipt("621", ["order_id"]), kind="quality", purpose="quality_check"),
        receipt("620", ["billed_total", "event_day"]),
        dict(receipt("622", ["product_id"]), purpose="source_proof"),
    ]
    actual = {
        "record_id": "620",
        "state": {
            "task_status": "completed",
            "semantic_plan": deepcopy(PLAN),
            "outputs": deepcopy(OUTPUTS),
            "steps": steps,
        },
        "queries": [
            {"record_id": step["record_id"], "sql": "SELECT 1"} for step in steps
        ],
    }
    return {"turns": [expected]}, {"turns": [actual]}


def verdict(pair):
    return cbi_eval_score.score(*pair)


def check(pair, status, *dimensions):
    result = verdict(pair)
    assert result["status"] == status, result
    assert set(dimensions) <= {issue["dimension"] for issue in result["issues"]}, result


def test_j01_wrong_signed_delta_and_explicit_direction_fail(comparison):
    check(comparison, "passed")
    turn = comparison[1]["turns"][0]["state"]
    turn["outputs"][-1]["value"] = "-1416.260931557109260"
    check(comparison, "failed", "quantity")
    turn["outputs"][-1]["value"] = "1416.26"
    turn["outputs"][-1]["direction"] = "decrease"
    check(comparison, "failed", "direction")


def test_j02_period_change_and_wrong_parent_fail(comparison):
    check(comparison, "passed")
    actual = comparison[1]["turns"][0]
    actual["state"]["semantic_plan"]["compare_start"] = "2026-08-01"
    check(comparison, "failed", "conditions")
    actual["state"]["semantic_plan"]["compare_start"] = "2026-09-01"
    expected = dict(comparison[0]["turns"][0], parent=0)
    actual_next = deepcopy(actual)
    actual_next["record_id"] = "623"
    actual_next["state"]["request"] = {"parent_record_id": "620"}
    for step, query in zip(
        actual_next["state"]["steps"], actual_next["queries"], strict=True
    ):
        step["record_id"] = "623" + step["record_id"]
        query["record_id"] = step["record_id"]
    comparison[0]["turns"].append(expected)
    comparison[1]["turns"].append(actual_next)
    check(comparison, "passed")
    actual_next["state"]["request"]["parent_record_id"] = "629"
    check(comparison, "failed", "conditions")


@pytest.fixture
def group(comparison):
    rows = [
        {
            "key": "Organic",
            "label": "Organic",
            "current": "185.2",
            "baseline": "135",
            "delta": "50.2",
        },
        {
            "key": "Direct",
            "label": "Direct",
            "current": "212.4",
            "baseline": "249",
            "delta": "-36.6",
        },
    ]
    expected = comparison[0]["turns"][0]
    actual = comparison[1]["turns"][0]
    expected["plan"] = dict(PLAN, group="account_source")
    actual["state"]["semantic_plan"]["group"] = "account_source"
    expected["decomposition"] = {
        "dimension": "account_source",
        "metric": "billed",
        "rows": rows,
    }
    actual["state"]["presentation"] = {
        "decomposition": {
            "dimension": "account_source",
            "metric": "billed",
            "rows": deepcopy(rows),
            "reconciled": True,
        }
    }
    return comparison


def test_j03_swapped_group_label_fails_even_when_amounts_match(group):
    check(group, "passed")
    rows = group[1]["turns"][0]["state"]["presentation"]["decomposition"]["rows"]
    rows[0]["label"], rows[1]["label"] = rows[1]["label"], rows[0]["label"]
    check(group, "failed", "presentation")
    for row in rows:
        row["label"] = row["key"]
    rows[0]["key"], rows[1]["key"] = rows[1]["key"], rows[0]["key"]
    check(group, "failed", "presentation")


def test_j04_extra_business_query_fails_internal_controls_do_not(group):
    check(group, "passed")
    actual = group[1]["turns"][0]
    actual["queries"].append(
        {"record_id": "623", "sql": "SELECT product_id FROM orders GROUP BY product_id"}
    )
    actual["state"]["steps"].append(receipt("623", ["product_id"]))
    check(group, "failed", "scope")


@pytest.fixture
def partial():
    expected = {
        "task_status": "partial",
        "query_scope": {"business": 1, "drill": 0, "quality": 1},
        "outputs": [
            {"id": "review_count", "status": "verified", "unit": "条", "value": 149},
            {
                "id": "average_rating",
                "status": "missing",
                "value": None,
                "reason": "quality_blocked",
            },
        ],
        "table": {"rows": [{"review_count": 149}]},
        "quality": {
            "status": "partial",
            "unavailable": ["average_rating"],
            "checks": [
                {
                    "rule_id": "score_valid",
                    "scope": "current",
                    "status": "failed",
                    "issue_count": 1,
                    "affected_rows": 1,
                }
            ],
        },
    }
    actual = {
        "record_id": "657",
        "state": {
            "task_status": "partial",
            "outputs": deepcopy(expected["outputs"]),
            "presentation": {"table": {"rows": [{"review_count": 149}]}},
            "steps": [
                receipt("657", ["review_id"]),
                dict(
                    receipt("658", ["rating"]), kind="quality", purpose="quality_check"
                ),
            ],
            "quality": {
                "status": "partial",
                "checks": [
                    {"rule_id": "score_valid", "scope": "current", "status": "failed"}
                ],
                "unavailable_outputs": [
                    {"key": "average_rating", "reason": "quality_blocked"}
                ],
            },
        },
        "raw_rows": [{"review_count": 149}],
        "queries": [
            {"record_id": "657", "sql": "SELECT count(*) AS review_count"},
            {
                "record_id": "658",
                "sql": "SELECT 1",
                "state": {
                    "quality_report": {
                        "results": deepcopy(expected["quality"]["checks"])
                    }
                },
            },
        ],
    }
    return {"turns": [expected]}, {"turns": [actual]}


def test_j05_unblocked_average_and_falsely_completed_fail(partial):
    check(partial, "passed")
    turn = partial[1]["turns"][0]
    turn["state"]["task_status"] = "completed"
    turn["state"]["outputs"][1].update(status="verified", value=4.006711409)
    turn["state"]["quality"]["unavailable_outputs"] = []
    check(partial, "failed", "quality", "state")
    turn["state"]["task_status"] = "partial"
    turn["state"]["outputs"] = deepcopy(partial[0]["turns"][0]["outputs"])
    turn["state"]["quality"]["unavailable_outputs"] = [
        {"key": "average_rating", "reason": "quality_blocked"}
    ]
    turn["raw_rows"][0]["average_rating"] = 4.006711409
    check(partial, "failed", "quality")


def test_j06_business_zero_and_execution_failure_are_distinct():
    expected = {
        "task_status": "blocked",
        "decision_reason": "semantic_query_failed",
        "query_scope": {"business": 0, "drill": 0},
        "outputs": [],
    }
    state = {
        "task_status": "blocked",
        "decision": {"reason": "semantic_query_failed"},
        "steps": [],
        "outputs": [],
    }
    actual = {"record_id": "60", "state": state, "queries": [], "raw_rows": None}
    pair = ({"turns": [expected]}, {"turns": [actual]})
    check(pair, "passed")
    actual["raw_rows"] = [{"repeat_accounts": 0}]
    check(pair, "failed", "state")
    expected.update(
        task_status="completed",
        decision_reason="ready",
        raw={"rows": [{"repeat_accounts": 0}]},
        query_scope={"business": 1, "drill": 0},
    )
    state.update(
        task_status="completed",
        decision={"reason": "ready"},
        steps=[receipt("60", ["user_id"])],
    )
    actual["queries"] = [{"record_id": "60", "sql": "SELECT 0"}]
    check(pair, "passed")
    actual["raw_rows"][0]["repeat_accounts"] = None
    check(pair, "failed", "quantity")


def test_j07_legal_rounding_alias_and_order_pass_but_ranking_changes_fail(group):
    expected = group[0]["turns"][0]
    turn = group[1]["turns"][0]
    expected["table"] = {
        "key": "dimension",
        "rows": [
            {"dimension": "A", "billed": "120.313"},
            {"dimension": "B", "billed": "20.314"},
        ],
        "tolerance": "0.005",
    }
    turn["state"]["presentation"]["table"] = {
        "rows": [
            {"dimension": "B", "billed": 20.31},
            {"dimension": "A", "billed": 120.31},
        ]
    }
    turn["queries"][0]["sql"] = "SELECT 1 AS alias_quality"
    check(group, "passed")
    expected["table"]["ordered"] = True
    check(group, "failed", "presentation")
    turn["state"]["presentation"]["table"]["rows"].reverse()
    check(group, "passed")


def test_j08_missing_expectations_and_missing_receipts_do_not_pass(
    comparison, monkeypatch
):
    check(comparison, "passed")
    check(({}, comparison[1]), "unjudgeable", "evidence")
    missing = deepcopy(comparison[1])
    missing["turns"][0]["queries"].pop(1)
    check((comparison[0], missing), "unjudgeable", "evidence")

    def crash(*_args, **_kwargs):
        raise RuntimeError("secret exception context")

    monkeypatch.setattr(cbi_eval_score, "_score_turn", crash)
    check(comparison, "evaluator_error", "evaluator")
    assert "secret exception context" not in str(verdict(comparison))


def test_free_text_claim_requires_review_and_known_direction_mismatch_still_fails(
    comparison,
):
    turn = comparison[1]["turns"][0]
    turn["answer_text"] = "账单变化来自营销投放，整体趋势走低"
    check(comparison, "review", "claim")
    turn["state"]["outputs"][-1]["value"] = -1416.26
    check(comparison, "failed", "quantity", "claim")


def test_provider_errors_cannot_masquerade_as_legitimate_refusal():
    expected = {
        "task_status": "blocked",
        "decision_reason": "semantic_unsupported",
        "query_scope": {"business": 0, "drill": 0},
        "outputs": [],
    }
    turn = {
        "record_id": "60",
        "queries": [],
        "state": {
            "task_status": "blocked",
            "decision": {"reason": "semantic_unsupported"},
            "steps": [],
            "outputs": [],
        },
    }
    pair = ({"turns": [expected]}, {"turns": [turn]})
    check(pair, "passed")
    turn["model"] = [{"error": True, "message": {"error": "interpretation_failed"}}]
    check(pair, "unjudgeable", "evidence")


def test_truncated_decomposition_checks_unlisted_totals(group):
    partial_total = {"current": "18", "baseline": "1", "delta": "17"}
    group[0]["turns"][0]["decomposition"].update(
        omitted=3, other=partial_total, tolerance="0"
    )
    actual = group[1]["turns"][0]["state"]["presentation"]["decomposition"]
    actual.update(omitted=3, other=dict(partial_total))
    check(group, "passed")
    actual["other"]["delta"] = "170"
    check(group, "failed", "quantity")
