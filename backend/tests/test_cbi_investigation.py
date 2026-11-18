import pytest

from apps.chat.task import cbi_investigation as inv


def test_signed_candidates_are_stable_and_ignore_zero_or_missing():
    result = {
        "reconciled": True,
        "rows": [
            {"key": "c", "label": "C", "delta": "0"},
            {"key": "b", "label": "B", "delta": "-100"},
            {"key": "a", "label": "A", "delta": "100"},
            {"key": "d", "label": "D", "delta": None},
        ],
    }
    assert [row["key"] for row in inv.candidates(result)] == ["a", "b"]


def test_child_comparison_keeps_both_periods_and_applies_registered_identity():
    root = {
        "start": "2026-10-01",
        "end": "2026-11-01",
        "compare_start": "2026-09-01",
        "compare_end": "2026-10-01",
        "filters": {"shop": "A"},
        "group": "category",
        "detail": False,
    }
    child = inv.child_plan(root, "product", "category_id", 27, top=25)
    assert child["filters"] == {"shop": "A", "category_id": 27}
    assert root["filters"] == {"shop": "A"}
    assert (
        child["compare_start"] == root["compare_start"] and child["group"] == "product"
    )


def test_fact_checks_period_direction_unit_scope_and_source():
    actual = inv.fact(
        {
            "key": "gizmo",
            "label": "Gizmo",
            "delta": "-1274.15",
            "current": "3894.53",
            "baseline": "5168.68",
        },
        {
            "start": "2026-10-01",
            "end": "2026-11-01",
            "compare_start": "2026-09-01",
            "compare_end": "2026-10-01",
            "group": "category",
            "filters": {},
        },
        "billed",
        "示例单位",
        "42",
    )
    assert inv.verify_claim(actual, actual)
    for key, value in {
        "direction": "increase",
        "label": "Gadget",
        "current_period": ["2026-01-01", "2026-02-01"],
        "unit": "CNY",
        "baseline": "1",
        "metric": "order_count",
        "reference": "43",
        "delta": "1274.15",
        "key": "gadget",
        "filters": [["category", "Gadget"]],
    }.items():
        assert not inv.verify_claim(actual, {**actual, key: value}), key
    assert not inv.verify_claim(actual, {"fact_id": actual["fact_id"]})


@pytest.mark.parametrize(
    "old,new",
    [
        (None, {"digest": "hash"}),
        ({"digest": "a"}, {"digest": "b"}),
        ({"digest": "a", "rows": 12}, {"digest": "a", "rows": 13}),
    ],
)
def test_unproven_or_changed_source_cannot_reuse(old, new):
    assert not inv.same_source(old, new)


def test_request_scope_hash_changes_for_a_filter():
    plan = {
        "start": "2026-10-01",
        "end": "2026-11-01",
        "compare_start": "2026-09-01",
        "compare_end": "2026-10-01",
        "filters": {"sku": [101, 102]},
        "group": "category",
    }
    assert inv.source_scope(plan) == inv.source_scope({**plan, "group": "sku"})
    assert inv.source_scope(plan) != inv.source_scope(
        {**plan, "filters": {"sku": [103]}}
    )
