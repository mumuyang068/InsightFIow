import pytest

from apps.chat.task.cbi_methods.binding import EventConfig, configured
from apps.chat.task.cbi_methods.contracts import Change, build_plan, exact_delta


def sample(**changes):
    return EventConfig.model_validate({
        "id": "demo", "datasource": 4, "label": "Demo", "store": "C", "view": "analysis.events",
        "entry_start": "2026-11-01", "entry_end": "2026-11-02", "observation_asof": "2026-11-04",
        **changes,
    })


def test_native_binding_is_independent_of_store_and_keeps_version():
    v1 = configured(sample(), 31, 1)
    v2 = configured(sample(window_hours=48), 31, 2)
    old = build_plan(v1, Change(), None, None)
    assert (old["store"], old["binding_version"], old["window_hours"]) == ("C", "v1", 24)
    assert build_plan(v2, Change(), None, None)["window_hours"] == 48
    assert build_plan(v1, Change(), old, "old") == old
    with pytest.raises(ValueError, match="parent_binding_mismatch"):
        build_plan(v2, Change(), old, "old")


def test_view_validation_rejects_unregistered_identifier_shape():
    with pytest.raises(ValueError):
        sample(view="analysis.somedata.more")
    with pytest.raises(ValueError):
        sample(window_hours=24, allowed_windows=[48])


def test_exact_percentage_delta_uses_member_counts():
    assert exact_delta({"numerator": 2, "denominator": 3}, {"numerator": 1, "denominator": 3}) == 33.333333
