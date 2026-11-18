from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.chat.task import cbi_contract


def test_managed_binding_selects_active_or_pinned_version_without_json(monkeypatch):
    monkeypatch.setenv("SQLBOT_CBI_MANAGED_TOPICS", "5,6,7")

    def reject(*_args):
        raise AssertionError("managed definitions must not fall back")

    monkeypatch.setattr(cbi_contract, "load_topic", reject)
    base = {
        "version": "fixture-data",
        "source_version": "fixture-data",
        "timezone": "UTC",
        "semantic": {
            "date_field": "event_day",
            "start": "2026-10-01",
            "end": "2026-11-01",
            "default_metric": "n",
            "metrics": {
                "n": {
                    "op": "count_distinct",
                    "field": "id",
                    "label": "n",
                    "unit": "rows",
                    "description": "count",
                }
            },
            "dimensions": {},
            "detail": {"id": "ID"},
        },
        "definitions": [
            {"id": "x", "view": "demo_bi.source", "label": "x", "description": "x"}
        ],
    }
    versions = {
        key: SimpleNamespace(
            version=key, content=dict(base, version=key), content_digest="abc"
        )
        for key in ("fixture-data", "cfg-v2")
    }
    active = SimpleNamespace(active_version="fixture-data", oid=1)

    def find(_session, id, version):
        assert id == 5
        return active, versions.get(version or active.active_version)

    monkeypatch.setattr(cbi_contract, "managed_record", find)
    version1 = cbi_contract.load_effective_topic(None, 5, workspace=1)
    active.active_version = "cfg-v2"
    assert version1.version == "fixture-data"
    assert cbi_contract.load_effective_topic(None, 5, workspace=1).version == "cfg-v2"
    assert (
        cbi_contract.load_effective_topic(
            None, 5, workspace=1, version=version1.version
        ).version
        == "fixture-data"
    )
    assert (
        cbi_contract.load_effective_topic(None, 5, workspace=1).source_version
        == "fixture-data"
    )
    with pytest.raises(HTTPException) as exc:
        cbi_contract.load_effective_topic(None, 5, workspace=1, version="missing")
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        cbi_contract.load_effective_topic(None, 5, workspace=2)
    assert exc.value.status_code == 404


def test_managed_missing_head_is_unavailable_without_falling_back(monkeypatch):
    monkeypatch.setenv("SQLBOT_CBI_MANAGED_TOPICS", "5")
    monkeypatch.setattr(cbi_contract, "managed_record", lambda *args: (None, None))
    with pytest.raises(HTTPException) as exc:
        cbi_contract.load_effective_topic(None, 5, workspace=1)
    assert exc.value.status_code == 503


@pytest.mark.parametrize("metric_id", ["order_payment_conversion_rate", "payment_conversion_rate"])
def test_explicit_unsupported_request_does_not_become_unknown_metric_clarification(metric_id):
    from apps.chat.task.cbi_semantic import SemanticIntent

    question = "2026 年 10 月订单支付转化率是多少？"
    reference = {"metric_id": metric_id, "quote": "订单支付转化率", "role": "requested"}
    intent = SemanticIntent(operation="unsupported", metric_references=[reference])
    current = {"billed": "账单总额"}
    decision = cbi_contract.validate_metric_request(question, intent, current, "billed", {})
    assert decision["disposition"] == "allow"
    executable = SemanticIntent(operation="new", metrics=["billed"], metric_references=[reference])
    assert cbi_contract.validate_metric_request(question, executable, current, "billed", {})["disposition"] == "clarify"


def test_unsupported_request_still_rejects_unverified_quote():
    from apps.chat.task.cbi_semantic import SemanticIntent

    intent = SemanticIntent(operation="unsupported", metric_references=[
        {"metric_id": "unknown", "quote": "支付转化率", "role": "requested"}
    ])
    decision = cbi_contract.validate_metric_request("账单总额？", intent, {"billed": "账单总额"}, "billed", {})
    assert decision["reason"] == "invalid_metric_quote"


@pytest.mark.parametrize(("view", "missing_fact"), [
    ("demo_bi.order_analysis", "payment"),
    ("example_bi.payments", None),
])
def test_unsupported_payment_explanation_is_specific_to_the_certified_billing_view(view, missing_fact):
    from apps.chat.task import cbi_analysis, cbi_semantic

    topic = SimpleNamespace(default_definition="sample", semantic=None,
                            by_id=lambda _key: SimpleNamespace(view=view))
    state = {"task_status": "blocked", "request": {"text": "订单支付转化率是多少？"}}
    intent = cbi_semantic.SemanticIntent(operation="unsupported", investigate=True)
    result = cbi_analysis._semantic_state(topic, state, intent, None, None)
    assert result["decision"]["reason"] == "semantic_unsupported"
    assert result["decision"].get("missing_fact") == missing_fact
    assert result["task_status"] == "blocked"


def test_worker_uses_accepted_version_when_active_publication_changes(monkeypatch):
    """Hold a request after acceptance; change default before its executor starts."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from apps.chat.task import cbi_analysis

    active = {"version": "cfg-v1"}
    accepted = Event()
    continue_run = Event()
    row = SimpleNamespace(
        chat_id=12, analysis_state={"config_version": "cfg-v1", "revision": 1}
    )
    chat = SimpleNamespace(id=12)

    class Session:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, model, _):
            return chat if model is cbi_analysis.Chat else row

        def execute(self, _):
            pass

        def refresh(self, _):
            pass

        def expunge(self, _):
            pass

    def source(_session, _user, _chat, *, version=None):
        return SimpleNamespace(
            version=version or active["version"], semantic=True
        ), None

    def execute(_session, _user, _row, state, topic, _data, _read):
        state.update(task_status="completed", actual_config=topic.version)

    def save(_session, record, state):
        record.analysis_state = state

    for key, value in {
        "Session": Session,
        "get_session": Session,
        "_accessible_topic": source,
        "_run_semantic": execute,
        "_save": save,
    }.items():
        monkeypatch.setattr(cbi_analysis, key, value)

    def in_flight():
        intake = source(None, None, None)[0].version
        row.analysis_state["config_version"] = intake
        accepted.set()
        if not continue_run.wait(2):
            raise AssertionError("publication barrier timed out")
        return cbi_analysis._run(1, None).analysis_state

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(in_flight)
        assert accepted.wait(2)
        active["version"] = "cfg-v2"
        continue_run.set()
        saved = task.result(timeout=2)
    assert saved["task_status"] == "completed"
    assert saved["config_version"] == saved["actual_config"] == "cfg-v1"
    assert source(None, None, None)[0].version == "cfg-v2"


@pytest.mark.parametrize(
    ("question", "refs", "metrics", "verdict"),
    [
        (
            "不要订单均额，只要总额",
            [("avg", "excluded", "不要订单均额"), ("sales", "requested", "总额")],
            ["sales"],
            "allow",
        ),
        (
            "订单均额已下架，改查总额",
            [("avg", "context", "订单均额已下架")],
            ["sales"],
            "allow",
        ),
        (
            "不要总额，我要订单均额",
            [("sales", "excluded", "不要总额"), ("avg", "requested", "我要订单均额")],
            ["sales"],
            "unsupported",
        ),
        ("只查 AVG 和总额", [("avg", "requested", "AVG")], ["sales"], "unsupported"),
        (
            "每条账单平均多少",
            [("avg", "requested", "每条账单平均")],
            ["sales"],
            "unsupported",
        ),
        (
            "不要订单均额，再查订单均额",
            [("avg", "excluded", "不要订单均额")],
            ["sales"],
            "clarify",
        ),
        (
            "不要订单均额，再查订单均额",
            [("avg", "excluded", "不要订单均额"), ("avg", "requested", "再查订单均额")],
            ["sales"],
            "clarify",
        ),
        (
            "不要订单均额，只要总额",
            [("avg", "excluded", "编造文本")],
            ["sales"],
            "clarify",
        ),
        ("只查订单均额", [("unknown", "requested", "订单均额")], ["sales"], "clarify"),
        ("只查订单均额", [], ["sales"], "clarify"),
        ("只查订单均额", [("avg", "uncertain", "订单均额")], ["sales"], "clarify"),
        ("只查总额", [], None, "allow"),
    ],
)
def test_metric_request_roles(question, refs, metrics, verdict):
    from apps.chat.task.cbi_contract import validate_metric_request
    from apps.chat.task.cbi_semantic import SemanticIntent

    intent = SemanticIntent(
        operation="new",
        metrics=metrics,
        metric_references=[
            {"metric_id": key, "role": role, "quote": quote}
            for key, role, quote in refs
        ],
    )
    history = {"avg": [{"label": "订单均额", "meaning": "均额"}]}
    actual = validate_metric_request(
        question, intent, {"sales": "总额"}, "sales", history
    )
    assert actual["disposition"] == verdict
    if verdict == "unsupported":
        assert actual["requested_unavailable"] == ["avg"]


def test_history_is_filtered_by_identity_and_granted_field():
    from apps.chat.task.cbi_contract import historical_metric_catalog

    versions = [
        {
            "average": {"field": "total", "label": "旧均额", "description": "说明"},
            "hidden": {"field": "private", "label": "机密口径", "description": "说明"},
        }
    ]
    assert historical_metric_catalog({"average"}, versions, {"total"}) == {}
    assert historical_metric_catalog(set(), versions, {"total"}) == {
        "average": [{"label": "旧均额", "meaning": "说明"}]
    }


def test_metric_request_keeps_implicit_selection_on_date_only_follow_up():
    from apps.chat.task.cbi_contract import validate_metric_request
    from apps.chat.task.cbi_semantic import SemanticIntent

    plan = SemanticIntent(operation="revise", start="2026-11-01", end="2026-12-01")
    assert (
        validate_metric_request(
            "改查十一月",
            plan,
            {"orders": "条数", "sales": "总额"},
            "orders",
            {},
            {"metrics": ["sales"]},
        )["disposition"]
        == "allow"
    )


def test_background_metric_is_not_added_to_explicit_selection():
    from apps.chat.task.cbi_contract import validate_metric_request
    from apps.chat.task.cbi_semantic import SemanticIntent

    question = "上次问过总额，这次只看条数"
    intent = SemanticIntent(
        operation="new",
        metrics=["sales", "orders"],
        metric_references=[
            {"metric_id": "sales", "role": "context", "quote": "上次问过总额"},
            {"metric_id": "orders", "role": "requested", "quote": "这次只看条数"},
        ],
    )
    assert (
        validate_metric_request(
            question, intent, {"sales": "总额", "orders": "条数"}, "sales", {}
        )["disposition"]
        == "clarify"
    )
