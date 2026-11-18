from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event

from apps.chat.task.cbi_semantic import TopicSemantics, instructions
from apps.chat.task.cbi_theme_validation import ConfigProblem, validate_content


def rule(rule_id, kind, field, metrics, **options):
    return {
        "id": rule_id,
        "type": kind,
        "fields": [field],
        "severity": "failure",
        "description": f"Validate {rule_id}",
        "suggestion": "Review the source rows",
        "metrics": metrics,
        **options,
    }


@pytest.fixture
def content():
    return {
        "date_field": "event_day",
        "start": "2026-10-01",
        "end": "2026-11-01",
        "default_metric": "billed",
        "metrics": {
            "billed": {
                "op": "sum",
                "field": "total",
                "label": "Billed",
                "unit": "CNY",
                "description": "Sum",
            },
            "volume": {
                "op": "count_distinct",
                "field": "id",
                "label": "Volume",
                "unit": "orders",
                "description": "Count",
            },
        },
        "dimensions": {
            "category": {"field": "category", "label": "Category"},
            "region": {"field": "region", "label": "Region"},
        },
        "detail": {"id": "ID"},
        "quality": {
            "source_mode": "static",
            "sample_keys": ["id"],
            "rules": [
                rule("grain", "unique", "id", ["billed", "volume"]),
                rule("missing_amount", "not_null", "total", ["billed"]),
                rule(
                    "invalid_total",
                    "range",
                    "total",
                    ["billed"],
                    minimum="-10",
                    maximum="100",
                ),
                rule("missing_day", "not_null", "event_day", ["billed"]),
                rule(
                    "missing_category",
                    "not_null",
                    "category",
                    ["billed"],
                    severity="warning",
                    dimensions=["category"],
                ),
            ],
        },
    }


TYPES = {
    "event_day": "date",
    "id": "bigint",
    "total": "numeric",
    "category": "text",
    "region": "text",
}


@pytest.fixture
def read_session():
    engine = create_engine("sqlite://")
    with engine.begin() as read:
        read.exec_driver_sql(
            "CREATE TABLE sample (event_day DATE, id INTEGER, total NUMERIC, category TEXT, region TEXT)"
        )
        read.exec_driver_sql(
            "INSERT INTO sample VALUES (?, ?, ?, ?, ?)",
            [
                ("2026-10-01", 1, 15, None, "a"),
                ("2026-10-02", 1, 20, "home", "a"),
                ("2026-10-30", 2, -10, "home", "a"),
                ("2026-10-31", 3, 100, "home", "a"),
                ("2026-10-30", 4, 101, "home", "a"),
                ("2026-10-30", 5, -11, "home", "a"),
                ("2026-09-30", 6, None, "home", "a"),
                ("2026-09-29", 7, 13, "home", "a"),
                ("2026-11-01", 8, None, "home", "a"),
                ("2026-10-03", 9, None, None, "b"),
                (None, 10, 3, "home", "a"),
                (None, 11, 3, "home", "b"),
            ],
        )
        yield read


def test_policy_is_optional_round_trips_and_stays_out_of_planning_prompt(content):
    legacy = deepcopy(content)
    del legacy["quality"]
    assert TopicSemantics.model_validate(legacy).quality is None
    topic = TopicSemantics.model_validate(content)
    assert topic.model_dump(mode="json")["quality"]["rules"][2]["minimum"] == "-10"
    assert "missing_amount" not in instructions(topic, None)


@pytest.mark.parametrize(
    "change", ["threshold", "impact", "unsafe_identifier", "freshness", "duplicate"]
)
def test_schema_rejects_invalid_policies(content, change):
    rules = content["quality"]["rules"]
    if change == "threshold":
        rules[2]["minimum"] = "101"
    elif change == "impact":
        rules[0]["metrics"] = ["unknown"]
    elif change == "unsafe_identifier":
        rules[0]["fields"] = ["unknown field"]
    elif change == "freshness":
        rules.append(
            rule("stale", "freshness", "event_day", ["volume"], max_age_days=7)
        )
    else:
        rules.append(rules[0])
    with pytest.raises(ValidationError):
        TopicSemantics.model_validate(content)


def test_draft_requires_actual_rule_field_and_numeric_range_type(content):
    baseline = TopicSemantics.model_validate(content)
    content["quality"]["rules"][0]["fields"] = ["foreign_key"]
    content["quality"]["rules"][2]["fields"] = ["category"]
    with pytest.raises(ConfigProblem) as error:
        validate_content(content, baseline, TYPES)
    assert error.value.fields["quality.rules.0.fields.0"] == "field_not_available"
    assert error.value.fields["quality.rules.2.fields.0"] == "operator_incompatible"


def test_comparison_keeps_period_bounds_filters_and_whole_date_null_scope(
    content, read_session
):
    from apps.chat.task.cbi_quality import run_quality

    report = run_quality(
        read_session,
        TopicSemantics.model_validate(content),
        "main.sample",
        allowed_fields=set(TYPES),
        plan={
            "start": "2026-10-01",
            "end": "2026-11-01",
            "compare_start": "2026-09-01",
            "compare_end": "2026-10-01",
            "filters": {"region": "a"},
            "metrics": ["billed"],
            "group": "category",
        },
        checked_on=date(2026, 11, 17),
        include_samples=True,
    )
    findings = {(r["scope"], r["rule_id"]): r for r in report["results"]}
    duplicate = findings["current", "grain"]
    assert (
        duplicate["checked_count"],
        duplicate["issue_count"],
        duplicate["affected_rows"],
    ) == (5, 1, 2)
    assert duplicate["sample_keys"] == [{"id": "1"}]
    assert findings["current", "missing_amount"]["status"] == "passed"
    assert findings["baseline", "missing_amount"]["issue_count"] == 1
    assert findings["current", "invalid_total"]["issue_count"] == 2
    assert findings["current", "missing_category"]["status"] == "warning"
    assert findings["readable", "missing_day"]["issue_count"] == 1
    observed = {item["scope"]: item for item in report["coverage"]}
    assert observed["current"]["latest_observed_date"] == "2026-10-31"
    assert observed["readable"]["checked_count"] == 10
    assert observed["readable"]["start"] is None
    assert observed["readable"]["completeness_confirmed"] is False
    assert len(report["queries"]) == 3
    assert {q["query_ref"] for q in report["queries"]} >= {
        entry["query_ref"] for entry in findings.values()
    }
    assert report["status"] == "failed"
    assert report["results"][0]["metrics"] == ["billed"]


def test_driver_receives_only_authorized_applicable_fields(content, read_session):
    from apps.chat.task.cbi_quality import quality_required_fields, run_quality

    topic = TopicSemantics.model_validate(content)
    plan = {
        "start": "2026-10-01",
        "end": "2026-11-01",
        "metrics": ["volume"],
        "group": "none",
    }
    assert quality_required_fields(topic, plan) == {"event_day", "id"}
    attempted = []
    event.listen(
        read_session.engine,
        "before_cursor_execute",
        lambda *args: attempted.append(args[2]),
    )
    with pytest.raises(ValueError):
        run_quality(
            read_session, topic, "main.sample", plan=plan, allowed_fields={"event_day"}
        )
    with pytest.raises(ValueError):
        run_quality(
            read_session,
            topic,
            "main.sample suspicious",
            plan=plan,
            allowed_fields={"event_day", "id"},
        )
    assert attempted == []
    report = run_quality(
        read_session,
        topic,
        "main.sample",
        plan=plan,
        allowed_fields={"event_day", "id"},
    )
    assert len(attempted) == 1
    assert [check["rule_id"] for check in report["results"]] == ["grain"]
    assert report["queries"][0]["fields"] == ["event_day", "id"]
    assert "sample_keys" not in report["results"][0]


def test_empty_and_rolling_source_states(content, read_session):
    from apps.chat.task.cbi_quality import run_quality

    content["quality"] = {
        "source_mode": "rolling",
        "rules": [rule("stale", "freshness", "event_day", ["volume"], max_age_days=7)],
    }
    topic = TopicSemantics.model_validate(content)

    def check(at, filters=None):
        return run_quality(
            read_session,
            topic,
            "main.sample",
            allowed_fields=set(TYPES),
            checked_on=at,
            plan={
                "start": "2026-10-01",
                "end": "2026-11-01",
                "metrics": ["volume"],
                "filters": filters or {},
            },
        )

    assert check(date(2026, 11, 8))["status"] == "passed"
    assert check(date(2026, 11, 9))["status"] == "failed"
    empty = check(date(2026, 11, 9), {"region": "absent"})
    assert empty["status"] == "no_samples"
    assert empty["results"][0]["checked_count"] == 0
    assert empty["results"][0]["issue_count"] is None


def test_maintenance_sample_request_is_bounded_in_source_sql(content, read_session):
    from apps.chat.task.cbi_quality import run_quality

    read_session.exec_driver_sql(
        "INSERT INTO sample VALUES ('2026-10-29', ?, NULL, 'home', 'a')",
        [(index,) for index in range(50, 80)],
    )
    report = run_quality(
        read_session,
        TopicSemantics.model_validate(content),
        "main.sample",
        include_samples=True,
        allowed_fields=set(TYPES),
    )
    missing = next(
        item for item in report["results"] if item["rule_id"] == "missing_amount"
    )
    assert missing["issue_count"] == 31
    assert len(missing["sample_keys"]) <= 5
    assert missing["samples_truncated"] is True


def test_quality_empty_policy_and_total_skip_dimension_warning(content, read_session):
    from apps.chat.task.cbi_quality import quality_required_fields, run_quality

    content["quality"]["rules"] = [content["quality"]["rules"][-1]]
    topic = TopicSemantics.model_validate(content)
    total = {
        "start": "2026-10-01",
        "end": "2026-11-01",
        "group": "none",
        "metrics": ["billed"],
    }
    assert quality_required_fields(topic, total) == set()
    total_report = run_quality(
        read_session, topic, "main.sample", allowed_fields=set(TYPES), plan=total
    )
    assert total_report["status"] == "not_applicable"
    assert not total_report["queries"]
    grouped = {**total, "group": "category"}
    grouped_report = run_quality(
        read_session, topic, "main.sample", allowed_fields=set(TYPES), plan=grouped
    )
    assert grouped_report["status"] == "warning"
    assert grouped_report["results"][0]["issue_count"] == 2
    content["quality"]["rules"] = []
    no_rules = run_quality(
        read_session,
        TopicSemantics.model_validate(content),
        "main.sample",
        allowed_fields=set(),
    )
    assert no_rules["status"] == "not_applicable"
    assert not no_rules["queries"]


def test_detail_quality_depends_on_fields_even_when_plan_default_metric_differs(
    content, read_session
):
    from apps.chat.task.cbi_quality import run_quality

    content["detail"]["total"] = "Amount"
    topic = TopicSemantics.model_validate(content)
    report = run_quality(
        read_session,
        topic,
        "main.sample",
        allowed_fields=set(TYPES),
        plan={
            "start": "2026-10-01",
            "end": "2026-11-01",
            "group": "none",
            "metrics": ["volume"],
            "detail": True,
        },
    )
    amount = next(
        item for item in report["results"] if item["rule_id"] == "missing_amount"
    )
    assert amount["status"] == "failed"
    assert amount["metrics"] == ["volume"]


def test_compound_keys_do_not_group_incomplete_keys_as_duplicates(
    content, read_session
):
    from apps.chat.task.cbi_quality import run_quality

    content["quality"]["rules"] = [
        rule("grain", "unique", "id", ["volume"], fields=["id", "category"])
    ]
    content["quality"]["sample_keys"] = ["id", "category"]
    read_session.exec_driver_sql(
        "INSERT INTO sample VALUES ('2026-10-29', ?, NULL, ?, 'a')",
        [(1, None), (1, "home")],
    )
    report = run_quality(
        read_session,
        TopicSemantics.model_validate(content),
        "main.sample",
        include_samples=True,
        allowed_fields=set(TYPES),
    )
    duplicate = report["results"][0]
    assert (
        duplicate["checked_count"],
        duplicate["issue_count"],
        duplicate["affected_rows"],
    ) == (5, 1, 2)
    assert duplicate["sample_keys"] == [{"id": "1", "category": "home"}]
