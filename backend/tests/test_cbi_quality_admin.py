from contextlib import nullcontext
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from apps.chat.api import cbi_theme as api
from apps.chat.curd import cbi_theme as store

THEME = 4101
WORKSPACE = 81
AUTHOR = 91
BASE = "orders-original"
SOURCE = {"dependency": "schema-a", "authorization_digest": "access-a"}
SCOPE = {"start": "2026-10-01", "end": "2026-11-01"}
CONTENT = {
    "version": BASE,
    "semantic": {
        "date_field": "event_day",
        "quality": {
            "source_mode": "static",
            "rules": [
                {"id": "unique-order", "type": "unique", "severity": "failure"},
                {"id": "category", "type": "not_null", "severity": "warning"},
            ],
        },
    },
}


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'quality.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    tables = [
        store.Theme,
        store.Version,
        store.Draft,
        store.Preview,
        store.Publication,
        store.QualityReport,
    ]
    SQLModel.metadata.create_all(engine, tables=[item.__table__ for item in tables])
    with Session(engine) as session:
        store.seed(session, THEME, WORKSPACE, BASE, CONTENT, "file-digest")
        session.commit()
    yield engine
    engine.dispose()


def _draft(session, draft_id=None, revision=None):
    return store.save_draft(session, THEME, AUTHOR, draft_id, revision, BASE, CONTENT)


def _check(session, draft, execute, request_id="check-1", scope=SCOPE):
    return store.run_quality_check(
        session,
        THEME,
        AUTHOR,
        request_id,
        scope,
        None,
        draft.id,
        draft.revision,
        execute,
    )


def _receipt(status="passed", *, include_baseline=False):
    results = [
        {"rule_id": "unique-order", "scope": "current", "status": status},
        {"rule_id": "category", "scope": "current", "status": "warning"},
    ]
    if include_baseline:
        results.extend(
            [
                {"rule_id": "unique-order", "scope": "baseline", "status": "passed"},
                {"rule_id": "category", "scope": "baseline", "status": "passed"},
            ]
        )
    return {
        "status": "warning",
        "source": SOURCE,
        "read_receipt": {"snapshot_id": "snapshot-a"},
        "results": results,
    }


def test_exact_request_replays_fixed_draft_report_after_a_new_revision(engine):
    execute = Mock(return_value=_receipt())
    with Session(engine, expire_on_commit=False) as session:
        first = _draft(session)
        report = _check(session, first, execute)
        assert report.config_digest == first.content_digest
        session.commit()
        _draft(session, first.id, first.revision)
        session.commit()
        first_id = report.id

    with Session(engine) as session:
        retry = _check(session, first, execute)
        assert retry.id == first_id
        assert store.get_quality(session, THEME, first_id).receipt == {
            **_receipt(),
            "publishable": True,
        }
        assert [
            item.id for item in store.list_quality(session, THEME, None, first.id)
        ] == [first_id]
        with pytest.raises(store.StoreError, match="quality_request_conflict"):
            _check(session, first, execute, scope={**SCOPE, "end": "2026-12-01"})
    execute.assert_called_once()


def _preview(session, draft):
    return store.save_preview(
        session,
        THEME,
        AUTHOR,
        draft.id,
        draft.revision,
        "schema-a",
        {"kind": "preview", "execution_status": "succeeded"},
    )


def _publish(session, draft, preview, report=None, source=SOURCE):
    return store.publish(
        session,
        THEME,
        AUTHOR,
        draft.id,
        draft.revision,
        BASE,
        preview.id,
        "publish-1",
        "schema-a",
        report.id if report else None,
        source,
    )


def test_warning_confirmation_and_current_context_are_bound_to_publication(engine):
    with Session(engine) as session:
        draft = _draft(session)
        report = _check(session, draft, Mock(return_value=_receipt()))
        preview = _preview(session, draft)
        confirmed = store.confirm_preview(
            session, THEME, AUTHOR, preview.id, "Reviewed preview", report.id
        )
        assert confirmed.quality_report_id == report.id
        with pytest.raises(store.StoreError, match="quality_report_required"):
            _publish(session, draft, confirmed)
        with pytest.raises(store.StoreError, match="quality_report_dependency_changed"):
            _publish(session, draft, confirmed, report, {**SOURCE, "dependency": "new"})
        with pytest.raises(store.StoreError, match="quality_report_dependency_changed"):
            _publish(
                session,
                draft,
                confirmed,
                report,
                {**SOURCE, "authorization_digest": "new"},
            )
        published = _publish(session, draft, confirmed, report)
        assert published.quality_report_id == report.id
        assert store.quality_was_published(session, THEME, report.id)
        assert [
            item.id
            for item in store.list_quality(session, THEME, published.version, None)
        ] == [report.id]
        assert store.list_quality(session, THEME, BASE, None) == []
        assert _publish(session, draft, confirmed, report).version == published.version
        with pytest.raises(store.StoreError, match="publication_request_conflict"):
            _publish(session, draft, confirmed)
        assert (
            store.get_version(session, THEME, published.version).content["semantic"]
            == CONTENT["semantic"]
        )


@pytest.mark.parametrize("status", ["failed", "no_samples", "incomplete"])
def test_mandatory_rule_must_pass_each_selected_period(engine, status):
    with Session(engine) as session:
        draft = _draft(session)
        preview = _preview(session, draft)
        store.confirm_preview(session, THEME, AUTHOR, preview.id, "Reviewed preview")
        report = _check(session, draft, Mock(return_value=_receipt(status)))
        with pytest.raises(store.StoreError, match="quality_report_not_successful"):
            _publish(session, draft, preview, report)
        missing_baseline = _check(
            session,
            draft,
            Mock(return_value=_receipt()),
            "check-2",
            {**SCOPE, "compare_start": "2026-09-01", "compare_end": "2026-10-01"},
        )
        with pytest.raises(store.StoreError, match="quality_report_not_successful"):
            _publish(session, draft, preview, missing_baseline)


def test_old_check_cannot_approve_an_edited_draft(engine):
    with Session(engine) as session:
        old = _draft(session)
        report = _check(session, old, Mock(return_value=_receipt()))
        draft = _draft(session, old.id, old.revision)
        preview = _preview(session, draft)
        with pytest.raises(store.StoreError, match="quality_report_candidate_mismatch"):
            store.confirm_preview(
                session, THEME, AUTHOR, preview.id, "Reviewed", report.id
            )
        store.confirm_preview(session, THEME, AUTHOR, preview.id, "Reviewed")
        with pytest.raises(store.StoreError, match="quality_report_candidate_mismatch"):
            _publish(session, draft, preview, report)
        assert store.get_theme(session, THEME).active_version == BASE


def test_optional_rule_without_samples_does_not_block_passed_mandatory_check():
    receipt = _receipt()
    receipt["status"] = "no_samples"
    receipt["results"][1]["status"] = "no_samples"
    assert store.quality_passes(CONTENT, SCOPE, receipt)
    receipt["results"][0]["status"] = "no_samples"
    assert not store.quality_passes(CONTENT, SCOPE, receipt)


def test_unacknowledged_warning_cannot_publish(engine):
    with Session(engine) as session:
        draft = _draft(session)
        report = _check(session, draft, Mock(return_value=_receipt()))
        preview = _preview(session, draft)
        store.confirm_preview(session, THEME, AUTHOR, preview.id, "Reviewed preview")
        with pytest.raises(store.StoreError, match="quality_report_not_confirmed"):
            _publish(session, draft, preview, report)


def test_source_budget_error_returns_incomplete_without_inventing_counts(monkeypatch):
    ds = SimpleNamespace(id=THEME)
    baseline = SimpleNamespace(
        source_version=BASE, timezone="source_calendar", semantic=None
    )
    config = SimpleNamespace(quality=SimpleNamespace(source_mode="static"))
    target = SimpleNamespace(content={"source_version": BASE, "semantic": {}})
    source = Mock()
    source.execute.return_value.one.return_value = (
        "snapshot",
        datetime(2026, 11, 17, tzinfo=UTC),
    )
    monkeypatch.setattr(
        api.cbi_semantic.TopicSemantics, "model_validate", Mock(return_value=config)
    )
    monkeypatch.setattr(api, "_quality_fields", Mock(return_value={"order_id"}))
    monkeypatch.setattr(api, "_require_assets", Mock())
    monkeypatch.setattr(api, "get_session", Mock(return_value=nullcontext(source)))
    monkeypatch.setattr(api, "_columns", Mock(return_value={"order_id": "bigint"}))
    monkeypatch.setattr(api, "_version_dependency", Mock(return_value="schema-a"))
    monkeypatch.setattr(api, "validate_content", Mock(return_value=config))

    def timeout_after_beginning(*_args, before_query, **_kwargs):
        before_query()
        raise TimeoutError

    monkeypatch.setattr(api, "run_quality", timeout_after_beginning)
    user = SimpleNamespace(id=AUTHOR, oid=WORKSPACE)
    cmd = api.QualityIn(request_id="r", version=BASE, **SCOPE)
    bound = (ds, None, baseline, "demo.orders", {"order_id": "bigint"}, 1)
    receipt = api._run_quality(None, user, bound, cmd, target)
    assert receipt["status"] == "incomplete"
    assert receipt["reason"] == "quality_budget_exhausted"
    assert receipt["checked_on"] == "2026-11-17"
    assert receipt["coverage"] == receipt["results"] == []
    assert receipt["read_receipt"]["snapshot_id"] == "snapshot"
    assert receipt["read_receipt"]["quality_queries"] == 1


def test_report_get_enforces_binding_without_opening_source(monkeypatch):
    ds = SimpleNamespace(id=THEME)
    item = SimpleNamespace(
        author_id=AUTHOR,
        id="report-id",
        draft_id=None,
        version=BASE,
        config_digest="digest",
        revision=None,
        request_id="check-1",
        scope=SCOPE,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        receipt={
            **_receipt(),
            "source": {
                **SOURCE,
                "asset_id": "1",
                "view": "demo.orders",
                "source_version": BASE,
                "source_id": str(THEME),
            },
            "fields": ["order_id"],
            "queries": [{"sql": "SELECT order_id"}],
        },
    )
    read_source = Mock(side_effect=AssertionError("history must not connect to source"))
    monkeypatch.setattr(api, "get_session", read_source)
    monkeypatch.setattr(store, "get_quality", Mock(return_value=item), raising=False)
    monkeypatch.setattr(
        api,
        "_bind",
        Mock(
            return_value=(
                ds,
                None,
                SimpleNamespace(source_version=BASE),
                "demo.orders",
                {"order_id": "bigint"},
                1,
            )
        ),
    )
    require = Mock(side_effect=HTTPException(403, "analysis_row_policy_unsupported"))
    monkeypatch.setattr(api, "_require_assets", require)
    user = SimpleNamespace(id=AUTHOR, oid=WORKSPACE)
    with pytest.raises(HTTPException, match="analysis_row_policy_unsupported"):
        api.quality_detail(THEME, "report-id", None, user, None)
    require.assert_called_once_with(None, user, ds, {"demo.orders": {"order_id"}})
    require.side_effect = None
    assert api.quality_detail(THEME, "report-id", None, user, None)["queries"][0]["sql"]
    read_source.assert_not_called()


def test_public_quality_timestamp_is_stable_across_database_timezones():
    instant = datetime(2026, 11, 17, 12, tzinfo=UTC)
    local_timezone = timezone(timedelta(hours=8))
    item = SimpleNamespace(
        id="report-id",
        request_id="check-1",
        version=BASE,
        draft_id=None,
        revision=None,
        config_digest="digest",
        scope=SCOPE,
        receipt=_receipt(),
        started_at=instant,
        completed_at=instant,
    )
    local_read = SimpleNamespace(
        **(
            vars(item)
            | {
                "started_at": instant.astimezone(local_timezone),
                "completed_at": instant.astimezone(local_timezone),
            }
        )
    )
    original = api._public_quality(item)
    assert original["started_at"] == instant.isoformat()
    assert api._public_quality(local_read) == original


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"version": BASE, "draft_id": "draft", "revision": 1},
        {"draft_id": "draft"},
        {"version": BASE, "start": "2026-11-01"},
        {"version": BASE, "compare_start": "2026-09-01"},
        {"version": BASE, "compare_start": "2026-09-01", "compare_end": "2026-10-02"},
    ],
)
def test_quality_requires_exact_target_and_bounded_complete_periods(changes):
    with pytest.raises(ValidationError):
        api.QualityIn(request_id="r", **{**SCOPE, **changes})
