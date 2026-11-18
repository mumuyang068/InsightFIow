import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from apps.chat.task import cbi_eval_runtime as runtime


def test_turns_pass_only_question_and_explicit_parent():
    case = {
        "turns": [{"question": "总额？", "expected": 12}, {"question": "只看 Gadget", "parent": 0}],
        "expected": {"answer": 12},
        "fixture": "rating_six",
    }
    turns = runtime.execution_turns(case)
    assert turns == [("总额？", None), ("只看 Gadget", 0)]
    assert "12" not in str(turns)


def test_followup_cannot_select_future_parent():
    with pytest.raises(ValueError, match="evaluation_parent_invalid"):
        runtime.execution_turns({"turns": [{"question": "问题", "parent": 1}]})


def test_disabled_or_privileged_runner_fails_closed(monkeypatch):
    session = Mock()
    monkeypatch.delenv("SQLBOT_CBI_EVAL_ACCOUNT", raising=False)
    with pytest.raises(HTTPException, match="evaluation_identity_unavailable"):
        runtime.bound_identity(session, 7)
    session.execute.assert_not_called()
    monkeypatch.setenv("SQLBOT_CBI_EVAL_ACCOUNT", "admin")
    with pytest.raises(HTTPException, match="evaluation_identity_unavailable"):
        runtime.bound_identity(session, 7)
    session.execute.assert_not_called()


def test_sdk_retry_limit_is_disabled_for_counted_calls():
    model = runtime.LLMConfig(
        model_type="openai", model_name="selected", model_id=10,
        additional_params={"temperature": 0.1, "max_retries": 3},
    )
    actual = runtime.counted_model(model)
    assert actual.additional_params == {"temperature": 0.1, "max_retries": 0, "timeout": 75}
    assert model.additional_params["max_retries"] == 3


def test_scope_fingerprint_ignores_configuration_variable():
    source = SimpleNamespace(oid=7, id=5, type="pg")
    fields_a = {"order": {"date", "amount"}}
    fields_b = {"order": {"date"}}
    assert runtime.permission_fingerprint(source, 10, fields_a) != runtime.permission_fingerprint(source, 10, fields_b)
    assert runtime.permission_fingerprint(source, 10, fields_a) != runtime.permission_fingerprint(source, 11, fields_a)


def test_ordinary_rollback_rejects_evaluation_only_version(monkeypatch):
    store = runtime.themes
    monkeypatch.setattr(store, "_lock_theme", lambda *_: SimpleNamespace(active_version="cfg-active"))
    monkeypatch.setattr(store, "_replay", lambda *_args, **_kwargs: None)
    lookup = Mock()
    monkeypatch.setattr(store, "get_version", lookup)
    with pytest.raises(store.StoreError, match="evaluation_configuration_readonly"):
        store.rollback(Mock(), 5, 1, "eval-standalone", "cfg-active", "request")
    lookup.assert_not_called()


def test_each_async_execution_owns_and_closes_its_provider_client(monkeypatch):
    config = runtime.LLMConfig(model_type="openai", model_name="selected")
    clients = []
    loops = []

    def create(config):
        client = config.additional_params["http_async_client"]
        assert not client.is_closed
        clients.append(client)
        loops.append(asyncio.get_running_loop())
        return SimpleNamespace(llm=object())

    monkeypatch.setattr(runtime.LLMFactory, "create_scoped_llm", create, raising=False)

    async def execute():
        async with runtime.managed_llm(config):
            assert not clients[-1].is_closed

    asyncio.run(execute())
    asyncio.run(execute())
    assert clients[0] is not clients[1] and loops[0] is not loops[1]
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("dependency", [
    "apps/chat/task/cbi_quality.py",
    "apps/chat/task/cbi_quality_runtime.py",
    "apps/chat/task/cbi_investigation.py",
    "apps/chat/task/cbi_theme_validation.py",
    "apps/chat/api/cbi_theme.py",
    "apps/ai_model/model_factory.py",
    "apps/ai_model/openai/llm.py",
])
def test_execution_version_changes_on_result_affecting_edit(tmp_path, dependency):
    for name in runtime.EXECUTION_FILES:
        item = tmp_path / name
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(b"before")
    assert dependency in runtime.EXECUTION_FILES
    before = runtime.execution_code_version(tmp_path)
    (tmp_path / dependency).write_bytes(b"changed")
    assert runtime.execution_code_version(tmp_path) != before
    assert before.startswith("execution-manifest-v1:")
