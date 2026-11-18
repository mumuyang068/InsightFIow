from copy import deepcopy

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from apps.chat.curd import cbi_eval as store
from apps.chat.curd import cbi_theme

THEME = 51
WORKSPACE = 61
ACTOR = 71
CASE = {
    "id": "T01",
    "datasource_id": str(THEME),
    "category": "summary",
    "turns": [{"question": "Total?"}],
    "fixture": "clean",
    "expected": {
        "turns": [
            {"task_status": "completed", "query_scope": {"business": 1, "drill": 0}}
        ]
    },
    "protects": [],
}


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'evaluation.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(
        engine,
        tables=[
            model.__table__
            for model in (
                cbi_theme.Theme,
                cbi_theme.Version,
                store.EvaluationSuite,
                store.EvaluationBatch,
                store.EvaluationAttempt,
                store.EvaluationCommand,
            )
        ],
    )
    with Session(engine) as session:
        for theme in (THEME, THEME + 1):
            cbi_theme.seed(session, theme, WORKSPACE, "base", {}, "source")
        session.commit()
    yield engine
    engine.dispose()


def suite(session, request_id="suite", cases=None, previous_id=None, confirm=True):
    return store.save_suite(
        session,
        THEME,
        WORKSPACE,
        ACTOR,
        request_id,
        title="Fixed suite",
        description="",
        cases=cases or [deepcopy(CASE)],
        previous_id=previous_id,
        confirm=confirm,
    )


def context(version="base", permissions="standard", budget_scope=None):
    return {
        "workspace_id": str(WORKSPACE),
        "source": "snapshot",
        "model": {"id": "1", "prompt": "fixture"},
        "versions": {str(THEME): version},
        "configurations": {str(THEME): {"id": version}},
        "permissions": permissions,
        "data_version": "source-2026",
        "evaluator_version": "deterministic-1",
        "code_version": "dev05",
        "identity": {"id": "99"},
        "budget_scope": budget_scope,
    }


def batch(session, suite_id, request_id="batch", *, budget_scope=None, **change):
    command = {
        "suite_id": suite_id,
        "versions": {str(THEME): "base"},
        "case_ids": None,
        "parent_batch_id": None,
        "label": "Baseline",
        "budget": 4,
        "declared_variable": "theme_configuration",
        **change,
    }
    return store.start_batch(
        session,
        THEME,
        WORKSPACE,
        ACTOR,
        request_id,
        command,
        lambda cases: context(
            command["versions"][str(THEME)], budget_scope=budget_scope
        ),
    )


def test_revisions_remain_immutable_and_parent_scope_is_checked(engine):
    with Session(engine) as session:
        first = suite(session)
        first_id = first.id
        second_case = dict(CASE, turns=[{"question": "Amended question?"}])
        second = suite(session, "new", [second_case], first_id)
        assert second.revision == 2
        assert second.digest != first.digest
        assert store.get_suite(session, THEME, WORKSPACE, first_id).cases == [CASE]
        with pytest.raises(store.StoreError, match="evaluation_suite_unavailable"):
            store.get_suite(session, THEME + 1, WORKSPACE, first_id)
        with pytest.raises(store.StoreError, match="evaluation_suite_unavailable"):
            store.get_suite(session, THEME, WORKSPACE + 1, first_id)


def test_idempotent_start_preserves_first_attempt_after_explicit_retry(engine):
    with Session(engine) as session:
        fixed = suite(session)
        run, created = batch(session, fixed.id)
        replay, duplicate = batch(session, fixed.id)
        assert created and not duplicate and replay.id == run.id
        with pytest.raises(store.StoreError, match="evaluation_request_conflict"):
            batch(session, fixed.id, label="Tampered")
        first = store.claim_next(session, run.id)
        assert store.claim_next(session, run.id) is None
        assert store.record_model_call(session, run.id, first["attempt_id"], 48) == 1
        store.finish_attempt(
            session,
            run.id,
            first["attempt_id"],
            status="failed",
            observation={"record_id": "81"},
            judgement={"checks": []},
        )
        assert store.claim_next(session, run.id) is None
        store.change_batch(session, run.id, ACTOR, "retry", "resume", ["T01"])
        second = store.claim_next(session, run.id)
        assert second["attempt_id"] != first["attempt_id"]
        store.finish_attempt(
            session,
            run.id,
            second["attempt_id"],
            status="passed",
            observation={"record_id": "82"},
            judgement={"checks": ["updated"]},
        )
        report = store.public_batch(session, run, detail=True)
        assert report["rows"][0]["first"]["status"] == "failed"
        assert report["rows"][0]["latest"]["status"] == "passed"
        assert report["counts"]["first_passed"] == 0
        assert report["counts"]["model_calls"] == 1


def test_restart_marks_unknown_attempt_and_resumes_only_unstarted_cases(
    engine, monkeypatch
):
    with Session(engine) as session:
        fixed = suite(session, cases=[CASE, dict(CASE, id="T02")])
        run, _ = batch(session, fixed.id)
        first = store.claim_next(session, run.id)

    monkeypatch.setattr(store, "PROCESS_EPOCH", "after-restart")
    with Session(engine) as session:
        run = store.get_batch(session, first["batch_id"])
        report = store.public_batch(session, run, detail=True)
        assert report["status"] == "stopped"
        assert [row["status"] for row in report["rows"]] == ["interrupted", "pending"]
        assert store.claim_next(session, run.id) is None
        store.change_batch(session, run.id, ACTOR, "resume-after-restart", "resume")
        second = store.claim_next(session, run.id)
        assert second["case_id"] == "T02"
        assert (
            store.public_batch(session, run, detail=True)["rows"][0]["first"]["status"]
            == "interrupted"
        )


def test_stop_keeps_inflight_receipt_and_drafts_have_no_expected_answer(engine):
    with Session(engine) as session:
        fixed = suite(session, cases=[CASE, dict(CASE, id="T02")])
        run, _ = batch(session, fixed.id)
        task = store.claim_next(session, run.id)
        store.change_batch(session, run.id, ACTOR, "stop", "stop")
        store.finish_attempt(
            session,
            run.id,
            task["attempt_id"],
            status="failed",
            observation={"record_id": "81", "answer": 20},
            judgement={"expected": 30},
        )
        assert store.claim_next(session, run.id) is None
        draft = store.failure_case(session, run, "T01")
        assert draft["expected"] is None
        assert draft["origin"]["batch_id"] == run.id
        assert "answer" not in draft["origin"]
        assert (
            store.public_batch(session, run, detail=True)["rows"][1]["status"]
            == "pending"
        )


def test_comparison_refuses_permission_drift_and_marks_unpaired_rows(engine):
    with Session(engine) as session:
        fixed = suite(session, cases=[CASE, dict(CASE, id="T02")])
        first, _ = batch(session, fixed.id)
        second, _ = batch(
            session,
            fixed.id,
            "candidate",
            versions={str(THEME): "different"},
            parent_batch_id=first.id,
            case_ids=["T01"],
        )
        report = store.compare(session, first, second)
        assert report["comparable"]
        assert [row["status"] for row in report["rows"]] == ["unpaired", "unpaired"]
        second.context = context("different", permissions="restricted")
        changed = store.compare(session, first, second)
        assert not changed["comparable"]
        assert [
            item["field"] for item in changed["differences"] if not item["allowed"]
        ] == ["permissions"]


def test_call_limit_persists_across_batches(engine):
    with Session(engine) as session:
        fixed = suite(session)
        run, _ = batch(session, fixed.id, budget_scope="acceptance")
        task = store.claim_next(session, run.id)
        assert store.record_model_call(session, run.id, task["attempt_id"], 1) == 1
        later, _ = batch(session, fixed.id, "later", budget_scope="acceptance")
        store.finish_attempt(
            session,
            run.id,
            task["attempt_id"],
            status="passed",
            observation={},
            judgement={},
        )
        next_task = store.claim_next(session, later.id)
        with pytest.raises(store.StoreError, match="evaluation_budget_exhausted"):
            store.record_model_call(session, later.id, next_task["attempt_id"], 1)
        session.rollback()
        unrelated, _ = batch(session, fixed.id, "new-budget")
        next_task = store.claim_next(session, unrelated.id)
        assert (
            store.record_model_call(session, unrelated.id, next_task["attempt_id"], 1)
            == 1
        )


def test_api_rechecks_scope_for_reads_and_never_schedules_on_export(
    engine, monkeypatch
):
    from types import SimpleNamespace

    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from apps.chat.api import cbi_eval as api
    from apps.chat.task import cbi_eval_runtime as runtime
    from common.core.db import get_session
    from common.core.deps import get_current_assistant, get_current_user

    app = FastAPI()
    app.include_router(api.router)
    actor = SimpleNamespace(id=ACTOR, oid=WORKSPACE, maintainer=True, allowed=True)
    scheduled = []

    def dependency_session():
        with Session(engine) as session:
            yield session

    def bind(_session, user, assistant, theme_id):
        if not user.maintainer or assistant:
            raise HTTPException(403)
        if theme_id not in {THEME, THEME + 1}:
            raise HTTPException(404)

    def check_read(_session, user, _theme_id, _context):
        if not user.allowed:
            raise HTTPException(403)

    app.dependency_overrides[get_session] = dependency_session
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_current_assistant] = lambda: None
    monkeypatch.setattr(api, "_bind", bind)
    monkeypatch.setattr(runtime, "check_read", check_read)
    monkeypatch.setattr(runtime, "prepare", lambda *args: context())
    monkeypatch.setattr(runtime, "schedule", scheduled.append)
    client = TestClient(app)
    base = f"/cbi/themes/{THEME}/evaluation"
    cmd = {
        "title": "Imported",
        "confirm": True,
        "request_id": "create",
        "cases": [CASE],
    }
    invalid = deepcopy(cmd)
    invalid["cases"][0]["expected"]["turns"][0]["query_scope"]["business"] = True
    assert client.post(f"{base}/suites", json=invalid).status_code == 422

    created = client.post(f"{base}/suites", json=cmd)
    assert created.status_code == 200, created.text
    suite_id = created.json()["id"]
    start = {
        "suite_id": suite_id,
        "versions": {str(THEME): "base"},
        "label": "B",
        "request_id": "start",
    }
    response = client.post(f"{base}/batches", json=start)
    assert response.status_code == 200, response.text
    batch_id = response.json()["id"]
    assert client.post(f"{base}/batches", json=start).json()["id"] == batch_id
    assert scheduled == [batch_id]

    path = f"{base}/batches/{batch_id}"
    assert client.get(path).status_code == 200
    exported = client.get(f"{path}/export")
    assert exported.status_code == 200
    assert exported.json()["suite"]["cases"][0]["expected"] == CASE["expected"]
    assert "attachment" in exported.headers["Content-Disposition"]
    assert (
        client.get(
            f"/cbi/themes/{THEME + 1}/evaluation/batches/{batch_id}/export"
        ).status_code
        == 404
    )
    actor.allowed = False
    assert client.get(f"{path}/export").status_code == 403
    assert (
        client.get(
            f"{base}/compare", params={"baseline": batch_id, "candidate": batch_id}
        ).status_code
        == 403
    )
    actor.maintainer = False
    assert client.get(path).status_code == 403
    assert scheduled == [batch_id]


def test_resume_checks_build_before_any_dispatch_and_keeps_fixed_receipts(engine, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.chat.api import cbi_eval as api
    from apps.chat.task import cbi_eval_runtime as runtime
    from common.core.db import get_session
    from common.core.deps import get_current_assistant, get_current_user

    original = runtime._current_execution_versions()
    with Session(engine) as session:
        fixed = suite(session, cases=[CASE, dict(CASE, id="T02")])
        run, _ = batch(session, fixed.id)
        run.context = {**run.context, **original}
        session.commit()
        run_id = run.id
        first = store.claim_next(session, run_id)
        store.finish_attempt(session, run_id, first["attempt_id"], status="passed",
                             observation={"turns": []}, judgement={"status": "passed"})
        store.change_batch(session, run_id, ACTOR, "stop", "stop")

    def db():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=ACTOR, oid=WORKSPACE)
    app.dependency_overrides[get_current_assistant] = lambda: None
    monkeypatch.setattr(api, "_bind", lambda *_: None)
    monkeypatch.setattr(runtime, "check_read", lambda *_: None)
    scheduled = []
    monkeypatch.setattr(runtime, "schedule", scheduled.append)
    client = TestClient(app)
    path = f"/cbi/themes/{THEME}/evaluation/batches/{run_id}"
    before = client.get(path).json()
    command = {"request_id": "retry-same-command"}
    changed = {**original, "code_version": original["code_version"] + "-changed"}
    monkeypatch.setattr(runtime, "_current_execution_versions", lambda: changed)
    rejection = client.post(f"{path}/resume", json=command)
    assert rejection.status_code == 409
    assert rejection.json()["detail"] == "evaluation_code_changed"
    assert client.get(path).json() == before
    assert client.get(f"{path}/export").status_code == 200
    assert not scheduled

    monkeypatch.setattr(runtime, "_current_execution_versions", lambda: original)
    assert client.post(f"{path}/resume", json=command).status_code == 200
    assert scheduled == [run_id]
    with Session(engine) as session:
        assert store.claim_next(session, run_id)["case_id"] == "T02"


def test_same_build_allows_declared_configuration_change_and_new_build_is_incomparable():
    from types import SimpleNamespace

    left = SimpleNamespace(suite_id="fixed", declared_variable="theme_configuration", context=context())
    right = SimpleNamespace(suite_id="fixed", declared_variable="theme_configuration", context=context("other"))
    fixed = {"suite_digest": "fixed"}
    changes = store.comparison_differences(left, right, fixed, fixed)
    assert {item["field"] for item in changes} == {"versions", "configurations"}
    assert all(item["allowed"] for item in changes)
    right.context["code_version"] = "execution-manifest-v1:different"
    changes = store.comparison_differences(left, right, fixed, fixed)
    assert [item["field"] for item in changes if not item["allowed"]] == ["code_version"]
