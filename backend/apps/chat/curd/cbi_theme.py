import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from apps.chat.models.cbi_theme import (
    Draft,
    Preview,
    Publication,
    QualityReport,
    Theme,
    Version,
)


class StoreError(Exception):
    def __init__(
        self, reason: str, status: int = 409, *, current_version: str | None = None
    ):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.current_version = current_version


def _digest(content: dict) -> str:
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def get_theme(session: Session, id: int) -> Theme | None:
    return session.get(Theme, id, populate_existing=True)


def list_themes(session: Session, oid: int) -> list[Theme]:
    return list(
        session.scalars(select(Theme).where(Theme.oid == oid).order_by(Theme.id))
    )


def _lock_theme(session: Session, id: int) -> Theme:
    # Serialize revisions, approval and publication for this theme across transactions.
    theme = session.get(Theme, id, populate_existing=True, with_for_update=True)
    if theme is None:
        raise StoreError("theme_unavailable", 404)
    return theme


def get_version(session: Session, id: int, version: str) -> Version:
    row = session.get(Version, (id, version))
    if row is None:
        raise StoreError("configuration_unavailable")
    return row


def list_versions(session: Session, id: int, *, include_evaluation: bool = False) -> list[Version]:
    query = (
        select(Version)
        .where(Version.theme_id == id)
        .order_by(Version.created_at.desc(), Version.version.desc())
    )
    if not include_evaluation:
        query = query.where(~Version.version.startswith("eval-"))
    initial = exists().where(Theme.id == id, Theme.initial_version == Version.version)
    return list(session.scalars(query.where(~initial)))


def seed(
    session: Session, id: int, oid: int, version: str, content: dict, source_digest: str
) -> Theme:
    query = (
        insert(Theme)
        .values(id=id, oid=oid, active_version=version, source_digest=source_digest)
        .on_conflict_do_nothing(index_elements=[Theme.id])
        .returning(Theme.id)
    )
    if session.scalar(query) is not None:
        session.add(
            Version(
                theme_id=id,
                version=version,
                content=deepcopy(content),
                content_digest=_digest(content),
                created_at=datetime.now(UTC),
            )
        )
        session.flush()
    theme = get_theme(session, id)
    if theme is None or theme.oid != oid:
        raise StoreError("theme_unavailable", 404)
    return theme


def register(
    session: Session,
    id: int,
    oid: int,
    actor_id: int,
    request_id: str,
    source_digest: str,
    content: dict,
) -> Theme:
    digest = _digest({"actor": actor_id, "content": content})
    initial = f"init-{uuid4().hex}"
    query = (
        insert(Theme)
        .values(
            id=id,
            oid=oid,
            initial_version=initial,
            source_digest=source_digest,
            registration_request_id=request_id,
            registration_digest=digest,
        )
        .on_conflict_do_nothing(index_elements=[Theme.id])
        .returning(Theme.id)
    )
    if session.scalar(query) is not None:
        version_content = {**deepcopy(content), "version": initial}
        session.add(
            Version(
                theme_id=id,
                version=initial,
                content=version_content,
                content_digest=_digest(version_content),
                created_by=actor_id,
                created_at=datetime.now(UTC),
            )
        )
        session.flush()
        save_draft(session, id, actor_id, None, None, initial, version_content)
    theme = _lock_theme(session, id)
    if theme.oid != oid:
        raise StoreError("theme_unavailable", 404)
    if (theme.registration_request_id, theme.registration_digest) != (
        request_id,
        digest,
    ):
        raise StoreError("theme_already_registered")
    return theme


def get_draft(session: Session, id: int, draft_id: str) -> Draft | None:
    query = (
        select(Draft)
        .where(Draft.theme_id == id, Draft.id == draft_id)
        .order_by(Draft.revision.desc())
        .limit(1)
    )
    return session.scalar(query)


def _published_draft(id: int, draft_id):
    return exists().where(Publication.theme_id == id, Publication.draft_id == draft_id)


def latest_draft(session: Session, id: int, actor_id: int) -> Draft | None:
    query = (
        select(Draft)
        .where(
            Draft.theme_id == id,
            Draft.actor_id == actor_id,
            ~_published_draft(id, Draft.id),
        )
        .order_by(Draft.saved_at.desc(), Draft.revision.desc(), Draft.id.desc())
        .limit(1)
    )
    return session.scalar(query)


def get_quality(session: Session, id: int, check_id: str) -> QualityReport | None:
    return session.get(QualityReport, (id, check_id))


def quality_was_published(session: Session, id: int, check_id: str) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    Publication.theme_id == id,
                    Publication.quality_report_id == check_id,
                )
            )
        )
    )


def list_quality(
    session: Session, id: int, version: str | None, draft_id: str | None
) -> list[QualityReport]:
    if draft_id:
        target = QualityReport.draft_id == draft_id
    else:
        target = or_(
            QualityReport.version == version,
            exists().where(
                Publication.theme_id == id,
                Publication.version == version,
                Publication.quality_report_id == QualityReport.id,
            ),
        )
    query = (
        select(QualityReport)
        .where(QualityReport.theme_id == id, target)
        .order_by(QualityReport.completed_at.desc(), QualityReport.id.desc())
    )
    return list(session.scalars(query))


def run_quality_check(
    session: Session,
    id: int,
    actor_id: int,
    request_id: str,
    scope: dict,
    version: str | None,
    draft_id: str | None,
    revision: int | None,
    execute: Callable[[Draft | Version], dict],
) -> QualityReport:
    # Hold the existing PostgreSQL theme lock through the bounded source scan,
    # so concurrent exact retries wait for and return the same stored receipt.
    theme = _lock_theme(session, id)
    previous = session.scalar(
        select(QualityReport).where(
            QualityReport.theme_id == id,
            QualityReport.request_id == request_id,
        )
    )
    if previous is not None:
        if (
            previous.author_id,
            previous.version,
            previous.draft_id,
            previous.revision,
            previous.scope,
        ) != (actor_id, version, draft_id, revision, scope):
            raise StoreError("quality_request_conflict")
        return previous
    if version is not None:
        if version == theme.initial_version:
            raise StoreError("configuration_unavailable")
        target = get_version(session, id, version)
    elif draft_id is not None:
        target = _require_draft(get_draft(session, id, draft_id), actor_id, revision)
    else:
        raise StoreError("quality_target_required", 422)
    started = datetime.now(UTC)
    receipt = execute(target)
    row = QualityReport(
        theme_id=id,
        id=str(uuid4()),
        request_id=request_id,
        author_id=actor_id,
        version=version,
        draft_id=draft_id,
        revision=revision,
        config_digest=target.content_digest,
        scope=deepcopy(scope),
        receipt={
            **deepcopy(receipt),
            "publishable": quality_passes(target.content, scope, receipt),
        },
        started_at=started,
        completed_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def _quality_policy(content: dict) -> dict:
    return (content.get("semantic") or {}).get("quality") or {}


def quality_passes(content: dict, scope: dict, receipt: dict) -> bool:
    policy = _quality_policy(content)
    rules = policy.get("rules", [])
    if not rules or receipt.get("status") not in {"passed", "warning", "no_samples"}:
        return False
    if not receipt.get("read_receipt", {}).get("snapshot_id"):
        return False
    statuses = {
        (result["rule_id"], result["scope"]): result["status"]
        for result in receipt.get("results", [])
    }
    if not {"passed", "warning"}.intersection(statuses.values()):
        return False
    date_field = content["semantic"]["date_field"]
    periods = ("current", "baseline") if scope.get("compare_start") else ("current",)
    for rule in rules:
        if rule["severity"] != "failure":
            continue
        if rule["type"] == "freshness" and policy["source_mode"] == "static":
            continue
        if rule["type"] == "freshness" and scope.get("as_of") is not None:
            return False
        scopes = (
            ("readable",)
            if rule["type"] == "freshness"
            or (rule["type"] == "not_null" and rule["fields"] == [date_field])
            else periods
        )
        if any(statuses.get((rule["id"], period)) != "passed" for period in scopes):
            return False
    return True


def _require_quality(
    session: Session, id: int, draft: Draft, check_id: str
) -> QualityReport:
    report = get_quality(session, id, check_id)
    if report is None or report.author_id != draft.actor_id:
        raise StoreError("quality_report_unavailable", 404)
    if (report.draft_id, report.revision, report.config_digest) != (
        draft.id,
        draft.revision,
        draft.content_digest,
    ):
        raise StoreError("quality_report_candidate_mismatch")
    if not report.receipt.get("publishable"):
        raise StoreError("quality_report_not_successful")
    return report


def _require_draft(
    draft: Draft | None, actor_id: int, revision: int | None = None
) -> Draft:
    if draft is None:
        raise StoreError("draft_unavailable", 404)
    if draft.actor_id != actor_id:
        raise StoreError("draft_owner_mismatch", 403)
    if revision is not None and draft.revision != revision:
        raise StoreError("draft_revision_conflict")
    return draft


def _require_current(theme: Theme, version: str) -> None:
    current = theme.active_version or theme.initial_version
    if current != version:
        raise StoreError(
            "active_version_conflict", current_version=current
        )


def save_draft(
    session: Session,
    id: int,
    actor_id: int,
    draft_id: str | None,
    expected_revision: int | None,
    base_version: str,
    content: dict,
) -> Draft:
    theme = _lock_theme(session, id)
    if draft_id is None:
        if expected_revision is not None:
            raise StoreError("draft_revision_conflict")
        _require_current(theme, base_version)
        draft_id = str(uuid4())
        revision = 1
    else:
        previous = _require_draft(get_draft(session, id, draft_id), actor_id)
        if previous.revision != expected_revision:
            raise StoreError("draft_revision_conflict")
        if previous.base_version != base_version:
            raise StoreError(
                "draft_base_conflict", current_version=theme.active_version
            )
        if session.scalar(select(_published_draft(id, draft_id))):
            raise StoreError("draft_already_published")
        revision = previous.revision + 1
    row = Draft(
        theme_id=id,
        id=draft_id,
        revision=revision,
        base_version=base_version,
        actor_id=actor_id,
        content=deepcopy(content),
        content_digest=_digest(content),
        saved_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def save_preview(
    session: Session,
    id: int,
    actor_id: int,
    draft_id: str,
    revision: int,
    dependency: str,
    receipt: dict,
) -> Preview:
    _require_draft(session.get(Draft, (id, draft_id, revision)), actor_id)
    row = Preview(
        theme_id=id,
        id=str(uuid4()),
        draft_id=draft_id,
        revision=revision,
        actor_id=actor_id,
        dependency=dependency,
        receipt=deepcopy(receipt),
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row


def get_preview(session: Session, id: int, validation_id: str) -> Preview | None:
    return session.get(Preview, (id, validation_id), populate_existing=True)


def list_previews(session: Session, id: int, draft_id: str) -> list[Preview]:
    query = (
        select(Preview)
        .where(Preview.theme_id == id, Preview.draft_id == draft_id)
        .order_by(Preview.created_at.desc(), Preview.id.desc())
        .execution_options(populate_existing=True)
    )
    return list(session.scalars(query))


def _require_preview(
    session: Session, id: int, actor_id: int, validation_id: str
) -> Preview:
    row = get_preview(session, id, validation_id)
    if row is None:
        raise StoreError("preview_unavailable", 404)
    if row.actor_id != actor_id:
        raise StoreError("draft_owner_mismatch", 403)
    if (
        row.receipt.get("kind") != "preview"
        or row.receipt.get("execution_status") != "succeeded"
    ):
        raise StoreError("preview_not_successful")
    return row


def confirm_preview(
    session: Session,
    id: int,
    actor_id: int,
    validation_id: str,
    note: str,
    quality_report_id: str | None = None,
) -> Preview:
    _lock_theme(session, id)
    row = _require_preview(session, id, actor_id, validation_id)
    draft = _require_draft(get_draft(session, id, row.draft_id), actor_id, row.revision)
    if quality_report_id is not None:
        report = _require_quality(session, id, draft, quality_report_id)
        if report.receipt["source"]["dependency"] != row.dependency:
            raise StoreError("quality_report_dependency_changed")
    if row.checked:
        if (row.note, row.quality_report_id) != (note, quality_report_id):
            raise StoreError("preview_already_confirmed")
        return row
    row.checked = True
    row.checked_by = actor_id
    row.checked_at = datetime.now(UTC)
    row.note = note
    row.quality_report_id = quality_report_id
    session.flush()
    return row


def get_publication(session: Session, id: int, request_id: str) -> Publication | None:
    return session.get(Publication, (id, request_id))


def _replay(
    session: Session,
    id: int,
    request_id: str,
    actor_id: int,
    action: str,
    expected_version: str,
    *,
    draft_id: str | None = None,
    revision: int | None = None,
    validation_id: str | None = None,
    quality_report_id: str | None = None,
    target_version: str | None = None,
) -> Publication | None:
    record = get_publication(session, id, request_id)
    if record is None:
        return None
    expected = (
        actor_id,
        action,
        expected_version,
        draft_id,
        revision,
        validation_id,
        quality_report_id,
        target_version,
    )
    actual = (
        record.actor_id,
        record.action,
        record.previous_version,
        record.draft_id,
        record.revision,
        record.validation_id,
        record.quality_report_id,
        record.version if record.action == "rollback" else None,
    )
    if actual != expected:
        raise StoreError("publication_request_conflict")
    return record


def publish(
    session: Session,
    id: int,
    actor_id: int,
    draft_id: str,
    revision: int,
    expected_version: str,
    validation_id: str,
    request_id: str,
    dependency: str,
    quality_report_id: str | None = None,
    quality_source: dict | None = None,
) -> Publication:
    theme = _lock_theme(session, id)
    replay = _replay(
        session,
        id,
        request_id,
        actor_id,
        "publish",
        expected_version,
        draft_id=draft_id,
        revision=revision,
        validation_id=validation_id,
        quality_report_id=quality_report_id,
    )
    if replay is not None:
        return replay
    _require_current(theme, expected_version)
    draft = _require_draft(get_draft(session, id, draft_id), actor_id, revision)
    if draft.base_version != expected_version:
        raise StoreError("draft_base_conflict", current_version=theme.active_version)
    if session.scalar(select(_published_draft(id, draft_id))):
        raise StoreError("draft_already_published")
    preview = _require_preview(session, id, actor_id, validation_id)
    if (preview.draft_id, preview.revision) != (draft_id, revision):
        raise StoreError("preview_candidate_mismatch")
    if not preview.checked:
        raise StoreError("preview_not_confirmed")
    if preview.dependency != dependency:
        raise StoreError("preview_dependency_changed")
    if _quality_policy(draft.content).get("rules"):
        if quality_report_id is None:
            raise StoreError("quality_report_required")
        report = _require_quality(session, id, draft, quality_report_id)
        if report.receipt.get("source") != quality_source:
            raise StoreError("quality_report_dependency_changed")
        if (
            report.receipt["status"] == "warning"
            and preview.quality_report_id != report.id
        ):
            raise StoreError("quality_report_not_confirmed")
    elif quality_report_id is not None:
        raise StoreError("quality_report_candidate_mismatch")

    version = f"cfg-{uuid4().hex}"
    content = deepcopy(draft.content)
    content["version"] = version
    now = datetime.now(UTC)
    session.add(
        Version(
            theme_id=id,
            version=version,
            content=content,
            content_digest=_digest(content),
            dependency=dependency,
            created_by=actor_id,
            created_at=now,
        )
    )
    session.flush()
    record = Publication(
        theme_id=id,
        request_id=request_id,
        action="publish",
        previous_version=expected_version,
        version=version,
        actor_id=actor_id,
        draft_id=draft_id,
        revision=revision,
        validation_id=validation_id,
        quality_report_id=quality_report_id,
        created_at=now,
    )
    theme.active_version = version
    session.add(record)
    session.flush()
    return record


def rollback(
    session: Session,
    id: int,
    actor_id: int,
    target_version: str,
    expected_version: str,
    request_id: str,
) -> Publication:
    theme = _lock_theme(session, id)
    replay = _replay(
        session,
        id,
        request_id,
        actor_id,
        "rollback",
        expected_version,
        target_version=target_version,
    )
    if replay is not None:
        return replay
    if not theme.active_version:
        raise StoreError("cbi_theme_not_published")
    _require_current(theme, expected_version)
    if target_version == expected_version:
        raise StoreError("rollback_target_current")
    if target_version.startswith("eval-"):
        raise StoreError("evaluation_configuration_readonly")
    if target_version == theme.initial_version:
        raise StoreError("registration_base_readonly")
    get_version(session, id, target_version)
    record = Publication(
        theme_id=id,
        request_id=request_id,
        action="rollback",
        previous_version=expected_version,
        version=target_version,
        actor_id=actor_id,
        created_at=datetime.now(UTC),
    )
    theme.active_version = target_version
    session.add(record)
    session.flush()
    return record


def set_availability(
    session: Session,
    id: int,
    actor_id: int,
    expected_version: str,
    request_id: str,
    enabled: bool,
) -> Theme:
    theme = _lock_theme(session, id)
    if theme.initial_version is None:
        raise StoreError("cbi_theme_legacy_readonly")
    action = "enable" if enabled else "disable"
    if _replay(session, id, request_id, actor_id, action, expected_version) is not None:
        return theme
    if theme.active_version is None:
        raise StoreError("cbi_theme_not_published")
    _require_current(theme, expected_version)
    theme.enabled = enabled
    session.add(
        Publication(
            theme_id=id,
            request_id=request_id,
            action=action,
            previous_version=expected_version,
            version=expected_version,
            actor_id=actor_id,
            created_at=datetime.now(UTC),
        )
    )
    session.flush()
    return theme


def list_publications(session: Session, id: int) -> list[Publication]:
    query = (
        select(Publication)
        .where(Publication.theme_id == id)
        .order_by(Publication.created_at.desc(), Publication.request_id.desc())
    )
    return list(session.scalars(query))
