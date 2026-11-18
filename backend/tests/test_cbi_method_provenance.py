from types import SimpleNamespace

from apps.chat.task.cbi_methods.contracts import Change, build_plan
from apps.chat.task.cbi_methods.provenance import compose_definition


def test_bound_defaults_and_explicit_same_value_have_different_origins():
    plan = build_plan("r11_funnel", Change(), None, None)
    binding = SimpleNamespace(id="r11_funnel", version=plan["binding_version"])
    first = compose_definition(plan, binding, Change(window_hours=24), None, "model", "q1")
    assert first["sources"]["store"] == {"kind": "binding", "id": binding.id, "version": binding.version}
    assert first["sources"]["entry_start"]["kind"] == "binding"
    assert first["sources"]["window_hours"] == {"kind": "model", "id": "q1"}
    later = compose_definition(plan, binding, Change(window_hours=24), "r1", "controls", "q2")
    assert later["sources"]["window_hours"] == {"kind": "controls", "id": "q2"}
    assert later["sources"]["timezone"] == {"kind": "run", "id": "r1"}


def test_explicit_null_is_input_and_omitted_null_is_inherited():
    plan = build_plan("r11_repeat", Change(), None, None)
    binding = SimpleNamespace(id="r11_repeat", version=plan["binding_version"])
    got = compose_definition(plan, binding, Change(cohort_channel=None), "prev", "model", "q3")
    assert got["sources"]["cohort_channel"] == {"kind": "model", "id": "q3"}
    assert got["sources"]["repeat_channel"] == {"kind": "run", "id": "prev"}
