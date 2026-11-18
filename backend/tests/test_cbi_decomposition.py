from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from apps.chat.task.cbi_semantic import (
    PlanRejected,
    SemanticIntent,
    TopicSemantics,
    build_query,
    present,
    resolve,
)


def topic_config():
    return {
        "date_field": "business_day",
        "start": date(2026, 10, 1),
        "end": date(2026, 11, 1),
        "default_metric": "billed",
        "metrics": {
            "billed": {
                "field": "amount",
                "op": "sum",
                "label": "账单",
                "unit": "元",
                "description": "账单面值",
            },
            "mean": {
                "field": "amount",
                "op": "average",
                "label": "均值",
                "unit": "元",
                "description": "账单均值",
            },
        },
        "dimensions": {
            "category": {"field": "category", "label": "品类"},
            "product": {"field": "title", "identity": "product_id", "label": "商品"},
            "product_id": {"field": "product_id", "label": "商品编号", "numeric": True},
        },
        "detail": {"product_id": "商品编号"},
        "decomposition": {
            "metrics": {"billed": True},
            "dimensions": {"category": ["product"], "product": []},
        },
    }


@pytest.fixture
def topic():
    return TopicSemantics.model_validate(topic_config())


def comparison(topic, **options):
    previous = resolve(
        topic,
        SemanticIntent(
            operation="new",
            compare_start=date(2026, 9, 1),
            compare_end=date(2026, 10, 1),
        ),
    )
    return resolve(topic, SemanticIntent(operation="revise", **options), previous)


def execute(topic, plan, records):
    with create_engine("sqlite://").connect() as session:
        session.exec_driver_sql(
            "CREATE TABLE ledger (business_day date, category varchar, title varchar, product_id integer, amount integer)"
        )
        session.exec_driver_sql("INSERT INTO ledger VALUES (?, ?, ?, ?, ?)", records)
        statement, sql, fields = build_query(topic, "main.ledger", plan)
        rows = [dict(row) for row in session.execute(statement).mappings()]
    return present(topic, plan, rows), rows, sql, fields


def test_approved_group_breaks_down_a_selected_comparison(topic):
    plan = comparison(topic, group="category", filters={"category": "服饰"})
    ((view, outputs), _, sql, fields) = execute(
        topic,
        plan,
        [
            ("2026-09-30", "服饰", "女装", 1, 150),
            ("2026-10-01", "服饰", "女装", 1, 200),
            ("2026-10-31", "服饰", "短衫", 2, 300),
            ("2026-11-01", "服饰", "短衫", 2, 99),
            ("2026-10-31", "日用", "家居", 3, 87),
        ],
    )
    assert plan["start"] == "2026-10-01" and plan["compare_start"] == "2026-09-01"
    assert fields == {"business_day", "category", "amount"}
    assert sql.count("main.ledger.category = '服饰'") == 2
    result = view["decomposition"]
    assert result["total"]["current"] == "500"
    assert result["total"]["baseline"] == "150"
    assert result["total"]["delta"] == "350"
    assert result["rows"][0]["key"] == "服饰"
    assert result["rows"][0]["delta"] == "350"
    assert result["reconciled"]
    assert {output["id"] for output in outputs} == {
        "billed_current",
        "billed_baseline",
        "billed_delta",
        "billed_rate",
    }


def test_union_keys_separate_same_labels_and_preserve_unknown_dimension(topic):
    plan = comparison(topic, group="product")
    ((view, _), _, _, _) = execute(
        topic,
        plan,
        [
            ("2026-09-30", "A", "同名", 1, 100),
            ("2026-10-01", "A", "同名", 2, 100),
            ("2026-09-30", "A", "未分类", 3, 20),
            ("2026-10-01", "A", "未分类", 3, 20),
            ("2026-09-30", "A", None, None, 10),
            ("2026-10-01", "A", None, None, 10),
        ],
    )
    result = view["decomposition"]
    assert result["reconciled"] and result["total"]["delta"] == "0"
    assert result["total"]["rate"] == "0"
    rows = {row["key"]: row for row in result["rows"]}
    assert set(rows) == {None, 1, 2, 3}
    assert rows[1]["delta"] == "-100" and rows[1]["current_status"] == "absent"
    assert rows[2]["delta"] == "100" and rows[2]["baseline_status"] == "absent"
    assert rows[1]["label"] == rows[2]["label"] == "同名"
    assert rows[3]["label"] == "未分类" and rows[None]["label"] is None
    assert "share" not in rows[1]


def test_null_observation_is_not_counted_as_zero(topic):
    plan = comparison(topic, group="category")
    ((view, _), _, _, _) = execute(
        topic,
        plan,
        [
            ("2026-09-30", "A", "一", 1, 100),
            ("2026-10-01", "A", "一", 1, None),
            ("2026-10-01", "A", "一", 1, 200),
        ],
    )
    result = view["decomposition"]
    assert not result["reconciled"]
    assert result["total"]["current"] is None and result["total"]["baseline"] == "100"
    assert result["rows"][0]["current_status"] == "unknown"
    assert result["rows"][0]["delta"] is None


def test_top_reconciles_hidden_groups_and_exposes_other(topic):
    plan = comparison(topic, group="category", top=1)
    ((view, _), _, _, _) = execute(
        topic,
        plan,
        [
            ("2026-09-30", "A", "一", 1, 100),
            ("2026-10-01", "A", "一", 1, 200),
            ("2026-09-30", "B", "二", 2, 125),
            ("2026-10-01", "B", "二", 2, 75),
        ],
    )
    result = view["decomposition"]
    assert result["reconciled"] and result["truncated"] and result["omitted"] == 1
    assert result["coverage"]["source_complete"]
    assert result["rows"][0]["key"] == "A"
    assert result["other"] == {"current": "75", "baseline": "125", "delta": "-50"}


def test_complete_empty_period_depends_on_metric_definition(topic):
    inputs = [("2026-10-01", "A", "一", 1, 100)]
    ((view, _), _, _, _) = execute(topic, comparison(topic, group="category"), inputs)
    result = view["decomposition"]
    assert result["reconciled"] and result["total"]["baseline"] == "0"
    assert result["total"]["rate"] is None
    baseline = topic_config()
    baseline["decomposition"]["metrics"]["billed"] = False
    limited = TopicSemantics.model_validate(baseline)
    ((view, _), _, _, _) = execute(
        limited, comparison(limited, group="category"), inputs
    )
    assert not view["decomposition"]["reconciled"]
    assert view["decomposition"]["rows"][0]["baseline"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"group": "day"},
        {"group": "product_id"},
        {"metrics": ["mean"], "group": "category"},
        {"detail": True},
        {"totals": True, "group": "category"},
    ],
)
def test_unsupported_combinations_still_fail(topic, change):
    with pytest.raises(PlanRejected, match="semantic_scope"):
        comparison(topic, **change)


def test_malformed_public_configuration_rejects_nonadditive_and_unknown_keys():
    for decomposition in (
        {"metrics": {"mean": True}, "dimensions": {"category": []}},
        {"metrics": {"billed": True}, "dimensions": {"category": ["missing"]}},
        {"metrics": {"billed": True}, "dimensions": {"category": ["category"]}},
    ):
        config = topic_config()
        config["decomposition"] = decomposition
        with pytest.raises(ValidationError):
            TopicSemantics.model_validate(config)


def test_source_limit_retains_independent_totals_without_inventing_group_values(topic):
    plan = comparison(topic, group="product")
    records = [("2026-10-01", "A", str(key), key, 1) for key in range(1, 1002)]
    records.append(("2026-09-30", "A", "外部", 1002, 1))
    ((view, _), raw, _, _) = execute(topic, plan, records)
    limited_raw = [row for row in raw if row["dimension_key"] != 1001]
    result = view["decomposition"]
    assert not result["reconciled"] and result["reason"] == "source_truncated"
    assert result["total"] == {
        "current": "1001",
        "baseline": "1",
        "delta": "1000",
        "rate": "100000",
    }
    assert result["omitted"] is None and result["other"] is None
    result = present(topic, plan, limited_raw)[0]["decomposition"]
    assert not result["reconciled"] and result["reason"] == "reconciliation_failed"


def test_stable_identity_aligns_renamed_labels_and_filters_source_keys(topic):
    from apps.chat.task.cbi_semantic import decomposition_filter

    plan = comparison(topic, group="product")
    ((view, _), _, _, fields) = execute(
        topic,
        plan,
        [
            ("2026-09-30", "A", "旧名", 1, 100),
            ("2026-10-01", "A", "新名", 1, 110),
            ("2026-10-01", "A", "别名", 1, 10),
        ],
    )
    result = view["decomposition"]
    assert {"product_id", "title"}.issubset(fields)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["key"] == 1 and result["rows"][0]["delta"] == "20"
    assert decomposition_filter(topic, "product", 1) == {"product_id": 1}
    assert decomposition_filter(topic, "category", None) is None


def test_reconciliation_checks_both_period_totals_and_signed_delta(topic):
    payload = topic_config()
    payload["decomposition"]["tolerance"] = "0.001"
    topic = TopicSemantics.model_validate(payload)
    plan = comparison(topic, group="category")
    (_, raw, _, _) = execute(
        topic,
        plan,
        [
            ("2026-09-30", "A", "一", 1, 100),
            ("2026-10-01", "A", "一", 1, 200),
        ],
    )
    for row in raw:
        if row["cell"] == "total":
            row["amount"] = "200.0009" if row["period"] == "current" else "99.9991"
    result = present(topic, plan, raw)[0]["decomposition"]
    assert not result["reconciled"] and result["reason"] == "reconciliation_failed"
