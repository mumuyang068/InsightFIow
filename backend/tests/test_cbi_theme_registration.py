from contextlib import nullcontext
from json import dumps
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import dialect
from sqlmodel import Session, SQLModel, create_engine

from apps.chat.api import cbi_theme as api
from apps.chat.curd import cbi_theme as store
from apps.chat.task.cbi_contract import is_managed_topic, load_effective_topic

SOURCE = 72
WORKSPACE = 91
MAINTAINER = 24
ASSET = "43"
VIEW = "example.order_lines"
TYPES = {
    "booked_day": "date",
    "document": "bigint",
    "extension": "numeric",
    "product": "text",
}
CONTENT = {
    "date_field": "booked_day",
    "start": "2011-02-01",
    "end": "2011-03-01",
    "default_metric": "amount",
    "metrics": {
        "amount": {
            "field": "extension",
            "op": "sum",
            "label": "Amount",
            "description": "Order-line amount",
            "unit": "example units",
        },
    },
    "dimensions": {},
    "detail": {"document": "Document"},
}
REQUEST = {
    "request_id": "register-1",
    "asset_id": ASSET,
    "label": "Orders",
    "content": CONTENT,
}


@pytest.fixture
def client_and_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'themes.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(
        engine,
        tables=[
            model.__table__
            for model in (
                store.Theme,
                store.Version,
                store.Draft,
                store.Preview,
                store.Publication,
                store.QualityReport,
            )
        ],
    )
    app = FastAPI()
    app.include_router(api.router)
    user = SimpleNamespace(id=MAINTAINER, oid=WORKSPACE, isAdmin=True)
    datasource = SimpleNamespace(
        id=SOURCE, oid=WORKSPACE, type="pg", name="Reader", description=""
    )
    native_asset = SimpleNamespace(
        schema="example",
        table=SimpleNamespace(id=int(ASSET), table_name="order_lines"),
        fields=[
            SimpleNamespace(field_name=key, field_type=value)
            for key, value in TYPES.items()
        ],
    )
    connection = {
        "host": "db.example",
        "port": 5432,
        "database": "business",
        "username": "reader",
        "dbSchema": "example",
    }

    with Session(engine) as session:
        original_get = session.get

        def get(model, id_, **kwargs):
            if model is api.CoreDatasource and id_ == SOURCE:
                return datasource
            return original_get(model, id_, **kwargs)

        session.get = get
        datasource.configuration = dumps(connection)
        monkeypatch.delenv("SQLBOT_CBI_MANAGED_TOPICS", raising=False)
        monkeypatch.delenv("SQLBOT_CBI_TOPICS", raising=False)
        monkeypatch.setattr(
            api,
            "require_maintainer",
            lambda _session, actor: (None if actor.isAdmin else _reject_maintainer()),
        )
        monkeypatch.setattr(api, "get_table_obj_by_ds", lambda *_: [native_asset])
        monkeypatch.setattr(api, "_require_assets", lambda *_: None)
        monkeypatch.setattr(api, "aes_decrypt", lambda value: value)
        read = SimpleNamespace(bind=SimpleNamespace(dialect=dialect()))
        monkeypatch.setattr(api, "get_session", lambda _: nullcontext(read))
        monkeypatch.setattr(api, "_start_read", lambda _: None)
        monkeypatch.setattr(
            api,
            "_registered_columns",
            lambda view, allowed, _: {
                key: TYPES[key] for key in allowed if view == VIEW and key in TYPES
            },
            raising=False,
        )
        app.dependency_overrides[api.SessionDep.__metadata__[0].dependency] = (
            lambda: session
        )
        app.dependency_overrides[api.CurrentUser.__metadata__[0].dependency] = (
            lambda: user
        )
        app.dependency_overrides[api.CurrentAssistant.__metadata__[0].dependency] = (
            lambda: None
        )
        yield TestClient(app), session, user
    engine.dispose()


def _reject_maintainer():
    raise HTTPException(403, "method_maintainer_required")


def test_register_route_creates_owned_draft_without_a_runtime_version(
    client_and_session,
):
    client, session, _ = client_and_session
    url = f"/cbi/themes/{SOURCE}/register"
    response = client.post(url, json=REQUEST)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["status"] == "draft"
    assert detail["source_version"] is None
    assert detail["active_version"] is None
    assert detail["revisions"] == []
    assert detail["draft"]["owner"] == str(MAINTAINER)
    assert detail["draft"]["content"]["metrics"]["amount"]["field"] == "extension"
    assert client.post(url, json=REQUEST).json()["draft"]["id"] == detail["draft"]["id"]
    assert client.post(url, json={**REQUEST, "label": "Other"}).status_code == 409
    with pytest.raises(HTTPException, match="cbi_theme_not_published"):
        load_effective_topic(session, SOURCE, workspace=WORKSPACE)
    with pytest.raises(HTTPException, match="cbi_theme_version_unavailable"):
        load_effective_topic(
            session, SOURCE, workspace=WORKSPACE, version=detail["base_version"]
        )


def test_register_rejects_incompatible_or_unauthorized_configuration(
    client_and_session,
):
    client, session, user = client_and_session
    url = f"/cbi/themes/{SOURCE}/register"
    assert client.post(url, json={**REQUEST, "asset_id": "876"}).status_code == 403
    invalid = {**CONTENT, "date_field": "document"}
    response = client.post(url, json={**REQUEST, "content": invalid})
    assert response.status_code == 422
    assert (
        response.json()["detail"]["fields"]["date_field"]
        == "approved_date_column_required"
    )
    invalid = {
        **CONTENT,
        "metrics": {"amount": {**CONTENT["metrics"]["amount"], "field": "product"}},
    }
    response = client.post(url, json={**REQUEST, "content": invalid})
    assert response.status_code == 422
    assert (
        response.json()["detail"]["fields"]["metrics.amount.op"]
        == "operator_incompatible"
    )
    user.isAdmin = False
    assert client.post(url, json=REQUEST).status_code == 403
    user.isAdmin = True
    user.oid = WORKSPACE + 1
    assert client.post(url, json=REQUEST).status_code == 404
    assert store.get_theme(session, SOURCE) is None


def test_registered_source_guard_keeps_existing_bindings_and_fails_closed(
    client_and_session, monkeypatch
):
    client, session, _ = client_and_session
    monkeypatch.setenv("SQLBOT_CBI_MANAGED_TOPICS", str(SOURCE))
    response = client.post(f"/cbi/themes/{SOURCE}/register", json=REQUEST)
    assert response.status_code == 409
    assert is_managed_topic(session, SOURCE)
    assert store.get_theme(session, SOURCE) is None
    with pytest.raises(HTTPException, match="cbi_theme_unavailable"):
        load_effective_topic(session, SOURCE, workspace=WORKSPACE)

    def unavailable(*_args):
        raise RuntimeError

    monkeypatch.delenv("SQLBOT_CBI_MANAGED_TOPICS")
    monkeypatch.setattr(store, "get_theme", unavailable)
    with pytest.raises(HTTPException, match="cbi_theme_unavailable"):
        is_managed_topic(session, SOURCE)
    with pytest.raises(HTTPException, match="cbi_theme_unavailable"):
        load_effective_topic(session, SOURCE, workspace=WORKSPACE)


def test_publication_and_disable_preserve_the_allowed_pinned_version(
    client_and_session,
):
    client, session, _ = client_and_session
    response = client.post(f"/cbi/themes/{SOURCE}/register", json=REQUEST)
    assert response.status_code == 200, response.text
    detail = response.json()
    draft = detail["draft"]
    marker = detail["base_version"]
    preview = store.save_preview(
        session,
        SOURCE,
        MAINTAINER,
        draft["id"],
        draft["revision"],
        "dependency",
        {"kind": "preview", "execution_status": "succeeded"},
    )
    store.confirm_preview(session, SOURCE, MAINTAINER, preview.id, "Reviewed")
    event_ = store.publish(
        session,
        SOURCE,
        MAINTAINER,
        draft["id"],
        draft["revision"],
        marker,
        preview.id,
        "publish-1",
        "dependency",
    )
    version = event_.version
    session.commit()
    assert load_effective_topic(session, SOURCE, workspace=WORKSPACE).version == version
    url = f"/cbi/themes/{SOURCE}/availability"
    disable = {"request_id": "disable-1", "expected_version": version, "enabled": False}
    response = client.post(url, json=disable)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "disabled"
    with pytest.raises(HTTPException, match="cbi_theme_disabled"):
        load_effective_topic(session, SOURCE, workspace=WORKSPACE)
    assert (
        load_effective_topic(
            session, SOURCE, workspace=WORKSPACE, version=version
        ).version
        == version
    )
    with pytest.raises(store.StoreError, match="registration_base_readonly"):
        store.rollback(session, SOURCE, MAINTAINER, marker, version, "rollback-1")
    enable = {"request_id": "enable-1", "expected_version": version, "enabled": True}
    assert client.post(url, json=enable).json()["status"] == "published"
    assert client.post(url, json=disable).json()["status"] == "published"
    assert load_effective_topic(session, SOURCE, workspace=WORKSPACE).version == version


def test_asset_sample_runs_only_the_selected_authorized_view(
    client_and_session, monkeypatch
):
    client, _, user = client_and_session
    captured = []

    def execute(ds, sql, *, statement, read_session):
        captured.append((ds.id, sql))
        assert read_session.bind.dialect.name == "postgresql"
        assert statement.selected_columns.keys() == [
            "booked_day",
            "document",
            "extension",
            "product",
        ]
        assert "FROM example.order_lines" in sql and "LIMIT 20" in sql
        return {"data": [{"extension": 125.0}]}

    monkeypatch.setattr(api, "exec_sql", execute)
    url = f"/cbi/themes/{SOURCE}/asset-preview"
    assert client.post(url, json={"asset_id": "876"}).status_code == 403
    user.isAdmin = False
    assert client.post(url, json={"asset_id": ASSET}).status_code == 403
    assert captured == []
    user.isAdmin = True
    response = client.post(url, json={"asset_id": ASSET})
    assert response.status_code == 200, response.text
    assert response.json()["query_count"] == 1
    assert response.json()["rows"] == [{"extension": 125.0}]
    assert captured == [(SOURCE, response.json()["sql"])]
