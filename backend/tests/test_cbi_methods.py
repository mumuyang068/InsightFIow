from pydantic import ValidationError
import pytest

from apps.chat.task.cbi_methods.contracts import Change, build_plan
from apps.chat.task.cbi_methods.compatibility import COMPATIBILITY
from apps.chat.task.cbi_methods.provenance import compose_definition


def test_strict_structure_names_bad_fields():
    with pytest.raises(ValidationError) as bad:
        Change.model_validate({"window_hours": "48", "invented_scope": "B"})
    assert {i["type"] for i in bad.value.errors()} == {"literal_error", "extra_forbidden"}


def test_window_only_and_field_provenance():
    first = build_plan("r11_funnel", Change(), None, None)
    second = build_plan("r11_funnel", Change(window_hours=48), first, "original")
    assert {k for k in first if first[k] != second[k]} == {"window_hours"}
    definition = compose_definition(second, COMPATIBILITY["r11_funnel"], Change(window_hours=48), "original", "controls", "click")
    assert definition["sources"]["window_hours"] == {"kind": "controls", "id": "click"}
    assert definition["sources"]["entry_start"] == {"kind": "run", "id": "original"}


def test_repeat_filters_independent_and_d30_unsupported():
    paid = build_plan("r11_repeat", Change(), None, None)
    ad = build_plan("r11_repeat", Change(cohort_channel="ad"), paid, "paid")
    assert ad["cohort_channel"] == "ad" and ad["repeat_channel"] is None
    net = build_plan("r11_repeat", Change(eligibility="net", cohort_channel=None), ad, "ad")
    assert net["cohort_channel"] is None and net["repeat_channel"] is None
    with pytest.raises(ValueError, match="snapshot"):
        build_plan("r11_repeat", Change(eligibility="net", observation_asof="2026-11-09"), paid, "paid")
