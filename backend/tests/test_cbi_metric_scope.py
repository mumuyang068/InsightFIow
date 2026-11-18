import json

import pytest

from apps.chat.task.cbi_contract import validate_metric_request
from apps.chat.task.cbi_semantic import SemanticIntent, TopicSemantics, instructions


METRICS = {"net_amount": "订单行折后金额", "orders": "订单数", "units": "商品件数"}


def check(question, intent, prior=None, default="net_amount", current=METRICS):
    return validate_metric_request(question, intent, current, default, {}, prior)


@pytest.mark.parametrize(
    ("operation", "text", "prior", "expected", "basis"),
    [
        ("new", "比较 1997 年 8 月与 7 月，以 8 月为基准，计算 7 月相对它的变化。",
         None, ["net_amount"], "published_default"),
        ("revise", "本期改为 9 月，仍和 7 月比较。",
         {"metrics": ["net_amount"]}, ["net_amount"], "parent_metrics"),
        ("revise", "本期改为 9 月，仍和 7 月比较。",
         {"metrics": ["orders", "units"]}, ["orders", "units"], "parent_metrics"),
    ],
)
def test_adopts_version_or_parent_scope_after_model_expansion(operation, text, prior, expected, basis):
    model = SemanticIntent(operation=operation, metrics=["units", "orders", "net_amount"])
    decision = check(text, model, prior)
    assert decision["disposition"] == "allow"
    assert decision["basis"] == basis
    assert decision["adopted_metrics"] == expected
    assert decision["model_metrics"] == ["units", "orders", "net_amount"]


def test_explicit_selection_overrides_default_and_parent_without_dropping_requested_metrics():
    model = SemanticIntent(operation="revise", metrics=["orders", "units", "net_amount"], metric_references=[
        {"metric_id": "orders", "role": "requested", "quote": "订单数"},
        {"metric_id": "net_amount", "role": "requested", "quote": "订单行折后金额"},
    ])
    result = check("改查订单数和订单行折后金额", model, {"metrics": ["units"]})
    assert result["adopted_metrics"] == ["orders", "net_amount"]
    assert result["basis"] == "explicit_metrics"


def test_literal_metric_remains_explicit_with_no_model_reference():
    result = check("1997 年 8 月的订单数是多少？",
                   SemanticIntent(operation="new", metrics=["units", "orders", "net_amount"]))
    assert result["adopted_metrics"] == ["orders"]


@pytest.mark.parametrize("quote", ["计算7月相对它的变化", "计算 7 月相对它的变化"])
def test_relative_period_phrase_does_not_cite_a_specific_metric(quote):
    model = SemanticIntent(operation="new", metrics=["net_amount"], metric_references=[
        {"metric_id": "net_amount", "role": "requested", "quote": quote},
    ])
    result = check("比较 1997 年 8 月与 7 月，以 8 月为基准，计算 7 月相对它的变化。", model)
    assert result["basis"] == "published_default"
    assert result["adopted_metrics"] == ["net_amount"]


def test_a_postfix_exclusion_does_not_bind_the_default_metric():
    result = check("订单行折后金额不用查，只看订单数。",
                   SemanticIntent(operation="new", metrics=["units", "orders", "net_amount"]))
    assert result["adopted_metrics"] == ["orders"]


def test_inherited_overall_projection_is_not_an_opportunity_to_add_metrics():
    plan = SemanticIntent(operation="revise", metrics=["orders", "units"], overall_metrics=["units"])
    result = check("本期改为九月", plan, {"metrics": ["orders"], "overall_metrics": ["net_amount"]})
    assert result["adopted_metrics"] == ["orders"]
    assert result["adopted_overall_metrics"] == ["net_amount"]


def test_invalid_default_and_unavailable_parent_do_not_fall_back_to_model_choices():
    assert check("比较两期变化", SemanticIntent(operation="new", metrics=["orders"]),
                 default="retired")["disposition"] == "clarify"
    assert check("本期改为九月", SemanticIntent(operation="revise", metrics=["orders"]),
                 {"metrics": ["retired"]})["disposition"] == "clarify"


def test_uses_default_from_bound_configuration_in_prompt_metadata():
    topic = TopicSemantics.model_validate({
        "date_field": "day", "start": "1997-07-01", "end": "1997-09-01",
        "default_metric": "orders", "metrics": {
            "orders": {"op": "sum", "field": "orders", "label": "订单数", "unit": "", "description": ""},
        }, "dimensions": {}, "detail": {"day": "日期"},
    })
    prompt = instructions(topic, None)
    metadata = json.JSONDecoder().raw_decode(prompt[prompt.index('{"metrics":'):])[0]
    assert metadata["default_metric"] == "orders"
