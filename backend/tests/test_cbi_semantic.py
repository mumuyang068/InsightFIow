from datetime import date

import pytest

from apps.chat.task.cbi_semantic import (
    PlanRejected,
    SemanticIntent,
    TopicSemantics,
    build_query,
    present,
    requested_prior_members,
    requests_all_groups,
    resolve,
)


@pytest.fixture
def topic():
    return TopicSemantics(
        date_field="business_day",
        start=date(2026, 1, 1),
        end=date(2026, 2, 1),
        default_metric="n",
        metrics={
            "n": {
                "op": "count_distinct",
                "field": "feedback_id",
                "label": "计数",
                "unit": "条",
                "description": "去重",
            },
            "avg_score": {
                "op": "average",
                "field": "score",
                "label": "均值",
                "unit": "分",
                "description": "总体平均",
            },
        },
        dimensions={
            "rank": {
                "field": "score",
                "label": "评分",
                "ordered": True,
                "numeric": True,
            },
            "kind": {
                "field": "kind",
                "label": "分类",
                "values": {"A": "甲", "B": "乙"},
            },
        },
        detail={"feedback_id": "记录 ID", "score": "评分"},
    )


def test_scope_inherits_only_selected_parent_and_disallows_unknown_fields(topic):
    parent = resolve(topic, SemanticIntent(operation="new", filters={"kind": "A"}))
    changed = resolve(topic, SemanticIntent(operation="revise", group="rank"), parent)
    assert changed["filters"] == {"kind": "A"}
    assert resolve(topic, SemanticIntent(operation="new"), parent)["filters"] == {}
    assert (
        resolve(
            topic, SemanticIntent(operation="revise", filters={"kind": None}), parent
        )["filters"]
        == {}
    )
    with pytest.raises(PlanRejected, match="semantic_unsupported"):
        resolve(topic, SemanticIntent(operation="new", group="unregistered"), parent)


def test_grouped_result_preserves_independent_overall_metrics(topic):
    plan = resolve(
        topic,
        SemanticIntent(
            operation="new", group="rank", metrics=["n"], overall_metrics=["avg_score"]
        ),
    )
    statement, sql, fields = build_query(topic, "demo_bi.reviews", plan)
    assert statement.is_select and fields == {"business_day", "feedback_id", "score"}
    assert '"demo_bi"' not in sql and "demo_bi.reviews" in sql
    presentation, outputs = present(
        topic, plan, [{"dimension": 1, "n": 2, "all_avg_score": 3.75}]
    )
    assert presentation["rows"] == [{"label": "1", "value": 2}]
    assert outputs[0]["value"] == 3.75
    assert outputs[0]["id"] == "avg_score"


@pytest.mark.parametrize("shape", [{"group": "kind"}, {"detail": True}])
def test_date_only_revision_keeps_result_shape_and_limit(topic, shape):
    old = resolve(topic, SemanticIntent(
        operation="new", top=5, filters={"kind": "A"}, **shape
    ))
    revision = SemanticIntent.model_validate_json(
        '{"operation":"revise","start":"2026-02-01","end":"2026-03-01"}'
    )
    after = resolve(topic, revision, old)
    assert after == {**old, "start": "2026-02-01", "end": "2026-03-01"}
    _, sql, fields = build_query(topic, "demo_bi.reviews", after)
    assert "LIMIT 6" in sql  # One extra row detects truncation.
    if shape.get("detail"):
        assert "COUNT(" not in sql
        assert {"feedback_id", "score"} <= fields
    else:
        assert "GROUP BY" in sql


def test_explicit_change_of_shape_and_limit(topic):
    parent = resolve(topic, SemanticIntent(
        operation="new", group="kind", top=5, filters={"kind": "A"},
        overall_metrics=["avg_score"]
    ))
    assert resolve(topic, SemanticIntent(operation="revise", top=10), parent) == {
        **parent, "top": 10
    }
    total = resolve(topic, SemanticIntent(operation="revise", group="none", detail=False), parent)
    assert total["filters"] == {"kind": "A"} and total["overall_metrics"] == []
    _, sql, _ = build_query(topic, "demo_bi.reviews", total)
    assert "GROUP BY" not in sql and "LIMIT" not in sql
    all_rows = resolve(topic, SemanticIntent(operation="revise", top=None), parent)
    assert all_rows["top"] == 100
    rows = [{"dimension": str(i), "n": i, "all_avg_score": 2} for i in range(101)]
    view, _ = present(topic, all_rows, rows)
    assert view["truncated"] is True and len(view["table"]["rows"]) == 100
    with pytest.raises(PlanRejected, match="semantic_scope"):
        resolve(topic, SemanticIntent(operation="revise"), {k: v for k, v in parent.items() if k != "top"})


def test_all_groups_does_not_consume_an_explicit_ranking(topic):
    assert requests_all_groups("8 月改为查看全部分类", topic, "kind")
    assert not requests_all_groups("查看全部分类中金额前五的类别", topic, "kind")
    assert not requests_all_groups("查看全部分类", topic, "rank")


def test_only_an_explicit_prior_member_request_reuses_prior_members(topic):
    prior = {
        "semantic_plan": {"group": "kind", "top": 2},
        "presentation": {"table": {"rows": [{"dimension": "A"}, {"dimension": "B"}]}},
    }
    assert requested_prior_members("换到 8 月", topic, prior) is None
    assert requested_prior_members("换到 8 月，只看原来这两个分类", topic, prior) == {"kind": ["A", "B"]}
    with pytest.raises(PlanRejected, match="semantic_scope"):
        requested_prior_members("只看原来的五个分类", topic, prior)


def test_compare_cannot_silently_drop_grouping_or_use_empty_period(topic):
    with pytest.raises(PlanRejected, match="semantic_scope"):
        resolve(
            topic,
            SemanticIntent(
                operation="new",
                group="rank",
                compare_start=date(2025, 12, 1),
                compare_end=date(2026, 1, 1),
            ),
        )
    with pytest.raises(PlanRejected, match="semantic_scope"):
        resolve(
            topic,
            SemanticIntent(
                operation="new", start=date(2026, 2, 1), end=date(2026, 2, 1)
            ),
        )

    with pytest.raises(PlanRejected, match="semantic_scope"):
        resolve(
            topic,
            SemanticIntent(
                operation="new",
                start=date(2026, 1, 1),
                end=date(2026, 3, 1),
                compare_start=date(2026, 1, 1),
                compare_end=date(2026, 2, 1),
            ),
        )


def test_registered_values_normalize_aliases_and_reject_unknown_members(topic):
    intent = SemanticIntent(operation="new", filters={"kind": ["甲", "A", "乙"]})
    plan = resolve(topic, intent)
    assert plan["filters"] == {"kind": ["A", "B"]}
    statement, _, _ = build_query(topic, "demo_bi.reviews", plan)
    assert ["A", "B"] in statement.compile().params.values()
    with pytest.raises(PlanRejected, match="semantic_scope"):
        resolve(topic, SemanticIntent(operation="new", filters={"kind": ["A", "C"]}))


@pytest.fixture
def comparison_parent(topic):
    return resolve(
        topic,
        SemanticIntent(
            operation="new",
            compare_start=date(2025, 12, 1),
            compare_end=date(2026, 1, 1),
            filters={"kind": "A"},
        ),
    )


def test_filter_only_revision_keeps_comparison(topic, comparison_parent):
    intent = SemanticIntent.model_validate(
        {
            "operation": "revise",
            "filters": {"kind": "B"},
            "compare_start": None,
            "compare_end": None,
        }
    )
    result = resolve(topic, intent, comparison_parent)
    assert (result["compare_start"], result["compare_end"]) == (
        "2025-12-01",
        "2026-01-01",
    )
    assert result["filters"] == {"kind": "B"}
    assert comparison_parent["filters"] == {"kind": "A"}
    assert result["metrics"] == comparison_parent["metrics"]
    sql = build_query(topic, "demo_bi.reviews", result)[1]
    assert sql.count("demo_bi.reviews.kind = 'B'") == 2
    assert (
        resolve(
            topic,
            SemanticIntent(operation="revise", metrics=["avg_score"]),
            comparison_parent,
        )["compare_start"]
        == "2025-12-01"
    )


def test_replace_and_clear_comparison_are_explicit(topic, comparison_parent):
    dates = {"compare_start": date(2025, 11, 1), "compare_end": date(2025, 12, 1)}
    for action in ("replace", None):
        result = resolve(
            topic,
            SemanticIntent(operation="revise", comparison_action=action, **dates),
            comparison_parent,
        )
        assert (
            result["start"] == "2026-01-01" and result["compare_start"] == "2025-11-01"
        )
        assert result["filters"] == {"kind": "A"}
    clear = resolve(
        topic,
        SemanticIntent(operation="revise", comparison_action="clear"),
        comparison_parent,
    )
    assert clear["compare_start"] is clear["compare_end"] is None
    assert clear["filters"] == {"kind": "A"} and clear["metrics"] == ["n"]
    for bad in (
        {"comparison_action": "replace"},
        {"compare_start": date(2025, 12, 1)},
        {"comparison_action": "clear", **dates},
        {"comparison_action": "keep", **dates},
    ):
        with pytest.raises(PlanRejected, match="semantic_scope"):
            resolve(topic, SemanticIntent(operation="revise", **bad), comparison_parent)


def test_new_analysis_does_not_inherit_comparison(topic, comparison_parent):
    result = resolve(
        topic,
        SemanticIntent(operation="new", comparison_action="keep"),
        comparison_parent,
    )
    assert result["compare_start"] is result["compare_end"] is None
    assert not result["filters"]


def test_single_period_trend_clears_comparison_explicitly(topic, comparison_parent):
    result = resolve(
        topic,
        SemanticIntent(operation="revise", group="day", comparison_action="clear"),
        comparison_parent,
    )
    assert result["group"] == "day" and result["compare_start"] is None
    assert result["filters"] == {"kind": "A"}


def test_incompatible_or_overlapping_comparison_is_rejected(topic, comparison_parent):
    for change in (
        {"group": "day"},
        {"group": "rank"},
        {"detail": True},
        {"start": date(2025, 12, 2), "end": date(2026, 1, 1)},
    ):
        with pytest.raises(PlanRejected, match="semantic_scope"):
            resolve(
                topic, SemanticIntent(operation="revise", **change), comparison_parent
            )


def test_malformed_summary_is_a_result_error(topic, comparison_parent):
    single = resolve(topic, SemanticIntent(operation="new"))
    for plan, rows in [
        (single, []),
        (single, [{"n": 1}, {"n": 1}]),
        (
            comparison_parent,
            [{"period": "current", "n": 1}, {"period": "current", "n": 2}],
        ),
    ]:
        with pytest.raises(ValueError, match="Expected"):
            present(topic, plan, rows)
