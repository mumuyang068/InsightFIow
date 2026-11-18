import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from apps.chat.curd import cbi_theme as store

THEME = 4101
WORKSPACE = 81
AUTHOR = 91
BASE = "orders-original"
EXECUTED = {
    "kind": "preview",
    "execution_status": "succeeded",
    "rows": [{"total": 4.2}],
}


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'theme.db').as_posix()}")

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
                store.QualityReport,
                store.Preview,
                store.Publication,
            )
        ],
    )
    with Session(engine) as session:
        store.seed(
            session,
            THEME,
            WORKSPACE,
            BASE,
            {"version": BASE, "metric": "count"},
            "file-digest",
        )
        session.commit()
    yield engine
    engine.dispose()


def new_draft(session, actor=AUTHOR, metric="average"):
    return store.save_draft(
        session, THEME, actor, None, None, BASE, {"version": BASE, "metric": metric}
    )


def approve(session, draft):
    record = store.save_preview(
        session, THEME, draft.actor_id, draft.id, draft.revision, "schema-a", EXECUTED
    )
    return store.confirm_preview(
        session, THEME, draft.actor_id, record.id, "Independently matched the fixture"
    )


def publish(session, draft, validation, request="publish-1", dependency="schema-a"):
    return store.publish(
        session,
        THEME,
        draft.actor_id,
        draft.id,
        draft.revision,
        BASE,
        validation.id,
        request,
        dependency,
    )


def test_seed_does_not_overwrite_an_active_publication(engine):
    with Session(engine) as session:
        draft = new_draft(session)
        publication = publish(session, draft, approve(session, draft))
        version = publication.version
        session.commit()

    with Session(engine) as session:
        store.seed(
            session,
            THEME,
            WORKSPACE,
            "changed-file-version",
            {"metric": "replaced"},
            "other-file-digest",
        )
        session.commit()
        assert store.get_theme(session, THEME).source_digest == "file-digest"
        assert store.get_theme(session, THEME).active_version == version
        assert store.get_version(session, THEME, BASE).content == {
            "version": BASE,
            "metric": "count",
        }
        assert store.get_version(session, THEME, version).content == {
            "version": version,
            "metric": "average",
        }
        assert len(store.list_versions(session, THEME)) == 2
        assert [row.id for row in store.list_themes(session, WORKSPACE)] == [THEME]
        with pytest.raises(store.StoreError):
            store.get_version(session, THEME, "changed-file-version")


def test_drafts_are_independent_and_stale_editor_cannot_overwrite(engine):
    with Session(engine) as session:
        first = new_draft(session)
        second = new_draft(session, AUTHOR + 1, "maximum")
        updated = store.save_draft(
            session,
            THEME,
            AUTHOR,
            first.id,
            1,
            BASE,
            {"version": BASE, "metric": "sum"},
        )
        assert updated.revision == 2
        assert store.latest_draft(session, THEME, AUTHOR + 1).id == second.id
        with pytest.raises(store.StoreError, match="draft_revision_conflict"):
            store.save_draft(session, THEME, AUTHOR, first.id, 1, BASE, first.content)
        assert store.get_draft(session, THEME, first.id).content["metric"] == "sum"
        assert store.get_draft(session, THEME, second.id).content["metric"] == "maximum"
        assert store.get_theme(session, THEME).active_version == BASE


@pytest.mark.parametrize(
    "receipt",
    [
        {"kind": "static", "execution_status": "not_run"},
        {"kind": "preview", "execution_status": "failed"},
    ],
)
def test_confirmation_requires_a_successful_explicit_preview(engine, receipt):
    with Session(engine) as session:
        draft = new_draft(session)
        record = store.save_preview(
            session, THEME, AUTHOR, draft.id, 1, "schema-a", receipt
        )
        with pytest.raises(store.StoreError, match="preview_not_successful"):
            store.confirm_preview(session, THEME, AUTHOR, record.id, "unverified")
        assert not store.get_preview(session, THEME, record.id).checked


def test_preview_is_bound_to_dependency_and_candidate_revision(engine):
    with Session(engine) as session:
        draft = new_draft(session)
        validation = approve(session, draft)
        with pytest.raises(store.StoreError, match="preview_dependency_changed"):
            publish(session, draft, validation, dependency="schema-b")
        updated = store.save_draft(
            session, THEME, AUTHOR, draft.id, 1, BASE, {"metric": "sum"}
        )
        with pytest.raises(store.StoreError, match="draft_revision_conflict"):
            publish(session, draft, validation)
        with pytest.raises(store.StoreError, match="preview_candidate_mismatch"):
            publish(session, updated, validation)
        assert store.list_previews(session, THEME, draft.id)[0].id == validation.id
        assert store.get_theme(session, THEME).active_version == BASE


def test_compare_and_swap_refreshes_current_after_another_session_publishes(engine):
    with Session(engine) as stale_session, Session(engine) as current_session:
        stale_candidate = new_draft(stale_session, AUTHOR + 1, "maximum")
        stale_preview = approve(stale_session, stale_candidate)
        stale_session.commit()
        stale_theme = store.get_theme(stale_session, THEME)
        assert stale_theme.active_version == BASE

        current_candidate = new_draft(current_session)
        winning = publish(
            current_session,
            current_candidate,
            approve(current_session, current_candidate),
        )
        winning_version = winning.version
        current_session.commit()

        with pytest.raises(store.StoreError, match="active_version_conflict") as exc:
            publish(
                stale_session, stale_candidate, stale_preview, request="losing-publish"
            )
        assert exc.value.current_version == winning_version
        assert store.get_draft(stale_session, THEME, stale_candidate.id).revision == 1
        assert len(store.list_publications(stale_session, THEME)) == 1


def test_exact_retry_returns_original_receipt_after_rollback(engine):
    with Session(engine, expire_on_commit=False) as session:
        candidate = new_draft(session)
        remaining_draft = new_draft(session, AUTHOR + 1, "maximum")
        validation = approve(session, candidate)
        publication = publish(session, candidate, validation)
        version = publication.version
        session.commit()
        session.refresh(publication)
        receipt = publication.model_dump(mode="json")

    with Session(engine) as session:
        with pytest.raises(store.StoreError):
            store.rollback(
                session, THEME, AUTHOR, "wrong-version", version, "rollback-invalid"
            )
        assert store.get_theme(session, THEME).active_version == version
        rollback = store.rollback(session, THEME, AUTHOR, BASE, version, "rollback-1")
        assert (
            store.rollback(session, THEME, AUTHOR, BASE, version, "rollback-1").version
            == rollback.version
        )
        retry = publish(session, candidate, validation, dependency="current-schema")
        assert retry.model_dump(mode="json") == receipt
        with pytest.raises(store.StoreError, match="publication_request_conflict"):
            store.rollback(session, THEME, AUTHOR, BASE, version, "publish-1")
        with pytest.raises(store.StoreError, match="publication_request_conflict"):
            store.publish(
                session,
                THEME,
                AUTHOR,
                candidate.id,
                2,
                BASE,
                validation.id,
                "publish-1",
                "schema-a",
            )
        with pytest.raises(store.StoreError, match="draft_already_published"):
            publish(session, candidate, validation, request="publish-2")
        assert store.get_theme(session, THEME).active_version == BASE
        assert store.list_publications(session, THEME)[0].action == "rollback"
        assert len(store.list_versions(session, THEME)) == 2
        assert store.latest_draft(session, THEME, AUTHOR + 1).id == remaining_draft.id
        assert store.latest_draft(session, THEME, AUTHOR) is None
