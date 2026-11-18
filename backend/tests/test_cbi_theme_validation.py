import pytest

from apps.chat.task.cbi_semantic import TopicSemantics
from apps.chat.task.cbi_theme_validation import ConfigProblem, validate_content


@pytest.fixture
def config():
    return TopicSemantics.model_validate(
        {
            "date_field": "event_day",
            "start": "2026-10-01",
            "end": "2026-11-01",
            "default_metric": "billed",
            "metrics": {
                "billed": {
                    "op": "sum",
                    "field": "total",
                    "label": "Billed",
                    "unit": "USD",
                    "description": "Sum",
                }
            },
            "dimensions": {"category": {"field": "category", "label": "Category"}},
            "detail": {"id": "ID"},
            "guidance": ["Payment is unavailable."],
        }
    )


@pytest.fixture
def types():
    return {"event_day": "date", "total": "numeric", "category": "text", "id": "bigint"}


def test_registered_avg_uses_existing_numeric_field(config, types):
    candidate = config.model_dump(mode="json")
    candidate["metrics"]["average"] = {
        "op": "average",
        "field": "total",
        "label": "Average",
        "unit": "USD",
        "description": "Average",
    }
    assert validate_content(candidate, config, types).metrics["average"].op == "average"


@pytest.mark.parametrize(
    ("field", "path"),
    [("category", "metrics.average.op"), ("foreign_total", "metrics.average.field")],
)
def test_preview_does_not_compile_unknown_or_type_incompatible_fields(
    config, types, field, path
):
    candidate = config.model_dump(mode="json")
    candidate["metrics"]["average"] = {
        "op": "average",
        "field": field,
        "label": "Average",
        "unit": "USD",
        "description": "Average",
    }
    with pytest.raises(ConfigProblem) as exc:
        validate_content(candidate, config, types)
    assert path in exc.value.fields


def test_fact_guidance_and_asset_are_not_editable(config, types):
    candidate = config.model_dump(mode="json")
    candidate["guidance"] = ["Use total to report payments."]
    candidate["view"] = "other_db.private_orders"
    with pytest.raises(ConfigProblem) as exc:
        validate_content(candidate, config, types)
    assert "guidance" in exc.value.fields and "view" in exc.value.fields


def test_confirmation_rechecks_current_preview_columns_before_returning_rows(
    monkeypatch,
):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from apps.chat.api import cbi_theme

    user = SimpleNamespace(id=11)
    ds = object()
    view = "demo_bi.orders"
    saved = SimpleNamespace(actor_id=user.id, receipt={"fields": ["tax"]})
    monkeypatch.setattr(
        cbi_theme, "_bind", lambda *_: (ds, None, None, view, {"event_day": "date"}, 1)
    )
    monkeypatch.setattr(cbi_theme.store, "get_preview", lambda *_: saved)

    def verify(_session, _user, datasource, columns):
        assert datasource is ds and columns == {view: {"tax"}}
        raise HTTPException(409, "analysis_asset_not_available")

    monkeypatch.setattr(cbi_theme, "_require_assets", verify)

    def must_not_approve(*_):
        pytest.fail("confirmation attempted before current field access check")

    monkeypatch.setattr(cbi_theme.store, "confirm_preview", must_not_approve)
    with pytest.raises(HTTPException) as exc:
        cbi_theme.confirm(
            1, cbi_theme.ConfirmIn(validation_id="v", note="verified"), None, user, None
        )
    assert exc.value.detail == "analysis_asset_not_available"


def test_publication_receipts_use_same_offset_before_and_after_reading_db():
    from datetime import UTC, datetime, timedelta, timezone
    from types import SimpleNamespace

    from apps.chat.api.cbi_theme import _receipt

    at = datetime(2026, 11, 16, 17, tzinfo=UTC)
    values = {
        "request_id": "same",
        "action": "publish",
        "version": "cfg-v2",
        "previous_version": "cfg-v1",
        "actor_id": 1,
        "draft_id": "draft",
        "revision": 1,
        "validation_id": "check",
    }
    created = _receipt(SimpleNamespace(**values, created_at=at))
    replay = _receipt(
        SimpleNamespace(
            **values, created_at=at.astimezone(timezone(timedelta(hours=8)))
        )
    )
    assert created["created_at"].isoformat() == replay["created_at"].isoformat()
