"""Manage registered topics without exposing connections or issuing ad hoc SQL."""

from collections import Counter
from datetime import UTC, date
from time import monotonic
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from sqlalchemy import DateTime, cast, column, inspect, select, table, text
from sqlalchemy.exc import SQLAlchemyError

from apps.chat.api.cbi_method_admin import require_maintainer
from apps.chat.api.cbi_multi import direct
from apps.chat.curd import cbi_theme as store
from apps.chat.models.chat_model import ChatRecord
from apps.chat.task import cbi_semantic
from apps.chat.task.cbi_analysis import _require_assets
from apps.chat.task.cbi_contract import (
    Identifier,
    Topic,
    connection_digest,
    is_managed_topic,
    load_effective_topic,
    load_topic,
    source_matches,
)
from apps.chat.task.cbi_multi_store import topic_for
from apps.chat.task.cbi_quality import quality_required_fields, run_quality
from apps.chat.task.cbi_theme_validation import (
    ConfigProblem,
    available_operators,
    signature,
    validate_content,
)
from apps.datasource.crud.datasource import get_table_obj_by_ds
from apps.datasource.models.datasource import CoreDatasource, DatasourceConf
from apps.datasource.utils.utils import aes_decrypt
from apps.db.db import exec_sql, get_session
from common.core.deps import CurrentAssistant, CurrentUser, SessionDep
from common.utils.utils import SQLBotLogUtil

router = APIRouter(prefix="/cbi/themes", tags=["Theme configuration"])

Id = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")]
AssetId = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$")]
SourceVersion = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:+-]+$")]
ViewName = TypeAdapter(Identifier)
ColumnName = TypeAdapter(cbi_semantic.Identifier)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetIn(Command):
    asset_id: AssetId


class RegisterIn(AssetIn):
    request_id: Id
    label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    source_data_version: SourceVersion | None = None
    content: dict


class AvailabilityIn(Command):
    request_id: Id
    expected_version: Id
    enabled: bool


class DraftIn(Command):
    draft_id: Id | None = None
    revision: int | None = Field(None, ge=1)
    base_version: Id
    content: dict


class DraftRef(Command):
    draft_id: Id
    revision: int = Field(ge=1)


class PreviewIn(DraftRef):
    intent: cbi_semantic.SemanticIntent


class ConfirmIn(Command):
    validation_id: Id
    note: str = Field(min_length=1, max_length=500)
    quality_report_id: Id | None = None


class PublishIn(DraftRef):
    expected_version: Id
    validation_id: Id
    request_id: Id
    quality_report_id: Id | None = None


class RollbackIn(Command):
    target_version: Id
    expected_version: Id
    request_id: Id


class QualityIn(Command):
    request_id: Id
    version: Id | None = None
    draft_id: Id | None = None
    revision: int | None = Field(default=None, ge=1)
    start: date
    end: date
    compare_start: date | None = None
    compare_end: date | None = None
    as_of: date | None = None

    @model_validator(mode="after")
    def check_target_and_scope(self):
        if self.version is None:
            if self.draft_id is None or self.revision is None:
                raise ValueError("quality_target_required")
        elif self.draft_id is not None or self.revision is not None:
            raise ValueError("quality_target_ambiguous")
        if (self.compare_start is None) != (self.compare_end is None):
            raise ValueError("quality_comparison_range_required")
        if any(
            not 0 < (end - start).days <= 731 for start, end in self.periods().values()
        ):
            raise ValueError("quality_invalid_period")
        if (
            self.compare_start is not None
            and self.compare_end is not None
            and self.start < self.compare_end
            and self.compare_start < self.end
        ):
            raise ValueError("quality_periods_overlap")
        return self

    def periods(self) -> dict[str, tuple[date, date]]:
        periods = {"current": (self.start, self.end)}
        if self.compare_start is not None and self.compare_end is not None:
            periods["baseline"] = (self.compare_start, self.compare_end)
        return periods


def _call(fn, *args):
    try:
        return fn(*args)
    except store.StoreError as exc:
        detail = {"reason": exc.reason}
        if exc.current_version is not None:
            detail["current_version"] = exc.current_version
        raise HTTPException(exc.status, detail) from exc


def _admin(session, user, assistant):
    direct(assistant)
    require_maintainer(session, user)


def _source(session, user, assistant, id_):
    _admin(session, user, assistant)
    ds = session.get(CoreDatasource, id_)
    if not ds or ds.oid != user.oid:
        raise HTTPException(404, "cbi_theme_not_found")
    return ds


def _bind(session, user, assistant, id_):
    ds = _source(session, user, assistant, id_)
    if not is_managed_topic(session, id_):
        raise HTTPException(409, "cbi_theme_legacy_readonly")
    active = load_effective_topic(
        session, id_, workspace=user.oid, allow_unpublished=True
    )
    baseline = (
        active
        if active.asset_id
        else load_effective_topic(
            session,
            id_,
            workspace=user.oid,
            version=active.source_version,
        )
    )
    if not active.default_definition or not baseline.semantic or ds.type != "pg":
        raise HTTPException(409, "analysis_source_changed")
    view = baseline.by_id(baseline.default_definition).view
    conf = DatasourceConf.model_validate_json(aes_decrypt(ds.configuration))
    if not source_matches(baseline, conf) or conf.dbSchema != view.split(".")[0]:
        raise HTTPException(409, "analysis_source_changed")
    assets = [
        item
        for item in get_table_obj_by_ds(session, user, ds)
        if f"{item.schema}.{item.table.table_name}" == view
        and (not baseline.asset_id or str(item.table.id) == baseline.asset_id)
    ]
    if len(assets) != 1 or not assets[0].fields:
        raise HTTPException(403, "analysis_asset_not_available")
    asset = assets[0]
    allowed = {field.field_name: field.field_type or "" for field in asset.fields}
    _require_assets(session, user, ds, {view: baseline.semantic.fields})
    return ds, active, baseline, view, allowed, asset.table.id


def _columns(view, allowed, read_session):
    schema, name = view.split(".")
    columns = inspect(read_session.connection()).get_columns(name, schema=schema)
    return {
        col["name"]: str(col["type"]).lower()
        for col in columns
        if col["name"] in allowed
    }


def _registered_columns(view, allowed, read_session):
    schema, name = view.split(".")
    statement = text("""
        SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
        WHERE n.nspname = :schema AND c.relname = :name AND c.relkind = 'v'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
          AND pg_catalog.has_column_privilege(c.oid, a.attnum, 'SELECT')
    """)
    return {
        key: type_.lower()
        for key, type_ in read_session.execute(
            statement, {"schema": schema, "name": name}
        )
        if key in allowed
    }


def _bound_columns(baseline, view, allowed, read):
    inspect_columns = (
        _registered_columns if getattr(baseline, "asset_id", None) else _columns
    )
    return inspect_columns(view, allowed, read)


def _editable_baseline(baseline):
    return None if getattr(baseline, "asset_id", None) else baseline.semantic


def _validate(content, baseline, types):
    try:
        return validate_content(
            content, _editable_baseline(baseline) if baseline else None, types
        )
    except ConfigProblem as exc:
        raise HTTPException(422, {"reason": str(exc), "fields": exc.fields}) from exc


def _start_read(session):
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    session.execute(text("SET LOCAL statement_timeout = '20s'"))


def _version_dependency(ds, baseline, view, asset, types):
    return signature(
        {
            "source": str(ds.id),
            "view": view,
            "asset": str(asset),
            "columns": types,
            "snapshot": baseline.source_version,
            "database": baseline.expected_database,
            "reader": baseline.expected_reader,
            "port": baseline.expected_port,
            **({"connection": baseline.connection_digest} if baseline.asset_id else {}),
        }
    )


def _quality_source(user, ds, baseline, view, asset, allowed, fields, dependency):
    return {
        "source_id": str(ds.id),
        "source_version": baseline.source_version,
        "view": view,
        "asset_id": str(asset),
        "dependency": dependency,
        "authorization_digest": signature(
            {
                "actor": str(user.id),
                "workspace": str(user.oid),
                "source": str(ds.id),
                "view": view,
                "asset": str(asset),
                "columns": {field: allowed[field] for field in fields},
            }
        ),
    }


def _quality_fields(config):
    return config.fields | quality_required_fields(config, include_samples=True)


def _run_quality(session, user, bound, cmd, target):
    ds, _, baseline, view, allowed, asset = bound
    if target.content.get("source_version") != baseline.source_version:
        raise HTTPException(409, "analysis_source_changed")
    config = cbi_semantic.TopicSemantics.model_validate(target.content["semantic"])
    fields = _quality_fields(config)
    _require_assets(session, user, ds, {view: fields})
    source = _quality_source(user, ds, baseline, view, asset, allowed, fields, None)
    read_receipt = {
        "isolation": None,
        "read_at": None,
        "snapshot_id": None,
        "metadata_checks": 0,
        "receipt_queries": 0,
        "quality_queries": 0,
        "transaction_controls": 0,
        "model_calls": 0,
    }
    receipt = {
        "source": source,
        "read_receipt": read_receipt,
        "fields": sorted(fields),
        "trigger": "manual",
        "status": "incomplete",
        "source_mode": config.quality.source_mode if config.quality else None,
        "checked_on": None,
        "coverage": [],
        "results": [],
        "queries": [],
    }
    deadline = monotonic() + 20

    try:
        with get_session(ds) as read:
            _start_read(read)
            read_receipt["transaction_controls"] += 2
            read_receipt["receipt_queries"] += 1
            snapshot_id, read_at = read.execute(
                text("SELECT pg_current_snapshot()::text, clock_timestamp()")
            ).one()
            read_receipt.update(
                isolation="repeatable_read_read_only",
                snapshot_id=snapshot_id,
                read_at=read_at,
            )
            # The source-calendar marker describes date columns without a source timezone.
            timezone = (
                UTC
                if baseline.timezone == "source_calendar"
                else ZoneInfo(baseline.timezone)
            )
            checked_on = cmd.as_of or read_at.astimezone(timezone).date()
            receipt["checked_on"] = checked_on
            read_receipt["metadata_checks"] += 1
            types = _bound_columns(baseline, view, allowed, read)
            source["dependency"] = _version_dependency(ds, baseline, view, asset, types)
            config = validate_content(
                target.content["semantic"], _editable_baseline(baseline), types
            )

            def before_query():
                remaining_ms = int((deadline - monotonic()) * 1000)
                if remaining_ms < 1:
                    raise TimeoutError
                read_receipt["transaction_controls"] += 1
                read.execute(
                    text("SELECT set_config('statement_timeout', :timeout, true)"),
                    {"timeout": f"{remaining_ms}ms"},
                ).close()
                read_receipt["quality_queries"] += 1

            receipt.update(
                run_quality(
                    read,
                    config,
                    view,
                    allowed_fields=set(types),
                    periods=cmd.periods(),
                    include_samples=True,
                    checked_on=checked_on,
                    before_query=before_query,
                )
            )
    except ConfigProblem as error:
        receipt.update(
            reason="quality_source_schema_changed", field_issues=error.fields
        )
    except TimeoutError:
        receipt["reason"] = "quality_budget_exhausted"
    except SQLAlchemyError as error:
        SQLBotLogUtil.warning(f"CBI theme quality failed: {type(error).__name__}")
        driver = getattr(error, "orig", None)
        cancelled = (
            getattr(driver, "sqlstate", None) or getattr(driver, "pgcode", None)
        ) == "57014"
        receipt["reason"] = (
            "quality_budget_exhausted" if cancelled else "quality_source_unavailable"
        )
    except ValueError as error:
        SQLBotLogUtil.warning(
            f"CBI theme quality result incomplete: {type(error).__name__}"
        )
        receipt["reason"] = "quality_result_incomplete"
    return jsonable_encoder(receipt)


def _authorize_quality(session, user, bound, item):
    ds, _, baseline, view, _, asset = bound
    if item is None or (
        item.draft_id is not None
        and item.author_id != user.id
        and not store.quality_was_published(session, ds.id, item.id)
    ):
        raise HTTPException(404, {"reason": "quality_report_unavailable"})
    source = item.receipt["source"]
    if (
        source["source_id"],
        source["view"],
        source["asset_id"],
        source["source_version"],
    ) != (str(ds.id), view, str(asset), baseline.source_version):
        raise HTTPException(409, {"reason": "quality_report_dependency_changed"})
    _require_assets(session, user, ds, {view: set(item.receipt["fields"])})
    return item


def _public_quality(item, *, detail=False):
    status = item.receipt["status"]
    common = {
        "id": item.id,
        "check_id": item.id,
        "request_id": item.request_id,
        "version": item.version,
        "draft_id": item.draft_id,
        "revision": item.revision,
        "config_digest": item.config_digest,
        "scope": item.scope,
        "status": status,
        "counts": dict(
            Counter(outcome["status"] for outcome in item.receipt["results"])
        ),
        "publishable": item.draft_id is not None
        and item.receipt.get("publishable", False),
        "started_at": item.started_at.astimezone(UTC).isoformat(),
        "completed_at": item.completed_at.astimezone(UTC).isoformat(),
    }
    if not detail:
        return common
    return {**item.receipt, **common, "author": str(item.author_id)}


def _draft(session, id_, ref, actor_id):
    draft = _call(store.get_draft, session, id_, ref.draft_id)
    if not draft:
        raise HTTPException(404, "cbi_theme_draft_not_found")
    if draft.actor_id != actor_id:
        raise HTTPException(403, "cbi_theme_draft_not_owned")
    if draft.revision != ref.revision:
        raise HTTPException(409, "cbi_theme_draft_changed")
    return draft


def _public_draft(draft):
    if draft is None:
        return None
    return {
        "id": draft.id,
        "revision": draft.revision,
        "base_version": draft.base_version,
        "content": draft.content["semantic"],
        "saved_at": draft.saved_at,
        "owner": str(draft.actor_id),
    }


def _public_validation(item):
    return {
        "id": item.id,
        "draft_id": item.draft_id,
        "revision": item.revision,
        "kind": item.receipt["kind"],
        "checked": item.checked,
        "receipt": item.receipt,
        "note": item.note,
        "created_at": item.created_at,
        "quality_report_id": getattr(item, "quality_report_id", None),
    }


def _receipt(item):
    return {
        "id": item.request_id,
        "kind": item.action,
        "version": item.version,
        "prior_version": None
        if item.previous_version.startswith("init-")
        else item.previous_version,
        "created_at": item.created_at.astimezone(UTC),
        "actor": str(item.actor_id),
        "draft_id": item.draft_id,
        "revision": item.revision,
        "validation_id": item.validation_id,
        "quality_report_id": getattr(item, "quality_report_id", None),
    }


def _row(session, user, ds):
    if is_managed_topic(session, ds.id):
        tip = load_effective_topic(
            session, ds.id, workspace=user.oid, allow_unpublished=True
        )
        head = store.get_theme(session, ds.id)
        draft = store.latest_draft(session, ds.id, user.id)
        verified = (
            next(
                (
                    item
                    for item in store.list_previews(session, ds.id, draft.id)
                    if item.checked and item.revision == draft.revision
                ),
                None,
            )
            if draft
            else None
        )
        return {
            "id": str(ds.id),
            "label": tip.label or ds.name,
            "description": (tip.semantic.description if head.active_version else "")
            or ds.description,
            "view": tip.by_id(tip.default_definition).view,
            "managed": True,
            "registered": head.initial_version is not None,
            "status": ("published" if head.enabled else "disabled")
            if head.active_version
            else "draft",
            "asset_id": tip.asset_id,
            "source_version": tip.source_version,
            "active_version": head.active_version,
            "base_version": head.initial_version if not head.active_version else None,
            "owner": str(draft.actor_id) if draft else None,
            "draft_revision": draft.revision if draft else None,
            "last_verified": verified.checked_at if verified else None,
        }
    legacy = load_topic(ds.id)
    native = topic_for(ds.id)
    if not (legacy or native):
        return None
    return {
        "id": str(ds.id),
        "label": ds.name,
        "description": ds.description,
        "view": legacy.definitions[0].view if legacy else native.view,
        "managed": False,
        "registered": False,
        "status": "legacy",
        "asset_id": None,
        "source_version": legacy.source_version if legacy else None,
        "base_version": None,
        "active_version": legacy.version if legacy else None,
        "owner": None,
        "draft_revision": None,
        "last_verified": None,
    }


@router.get("")
def list_themes(session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    _admin(session, user, assistant)
    sources = session.scalars(
        select(CoreDatasource)
        .where(CoreDatasource.oid == user.oid)
        .order_by(CoreDatasource.id)
    )
    return [row for ds in sources if (row := _row(session, user, ds))]


def _source_assets(session, user, ds, conf):
    for asset in get_table_obj_by_ds(session, user, ds):
        try:
            view = ViewName.validate_python(f"{asset.schema}.{asset.table.table_name}")
        except ValidationError:
            continue
        if asset.schema != conf.dbSchema:
            continue
        fields = {}
        for item in asset.fields or ():
            try:
                field = ColumnName.validate_python(item.field_name)
            except ValidationError:
                continue
            fields[field] = item.field_type or ""
        if fields:
            yield str(asset.table.id), view, fields


def _field_options(types, baseline=None):
    return [
        {
            "name": key,
            "type": value,
            "operators": available_operators(key, value, baseline),
        }
        for key, value in sorted(types.items())
    ]


def _registrable(ds):
    return ds.type == "pg" and not (load_topic(ds.id) or topic_for(ds.id))


@router.get("/assets")
def available_assets(
    session: SessionDep, user: CurrentUser, assistant: CurrentAssistant
):
    _admin(session, user, assistant)
    sources = session.scalars(
        select(CoreDatasource)
        .where(CoreDatasource.oid == user.oid, CoreDatasource.type == "pg")
        .order_by(CoreDatasource.id)
    ).all()
    result = []
    for ds in sources:
        if is_managed_topic(session, ds.id) or not _registrable(ds):
            continue
        entries = []
        try:
            conf = DatasourceConf.model_validate_json(aes_decrypt(ds.configuration))
            candidates = list(_source_assets(session, user, ds, conf))
            if not candidates:
                continue
            with get_session(ds) as read:
                _start_read(read)
                for asset_id, view, allowed in candidates:
                    try:
                        _require_assets(session, user, ds, {view: set(allowed)})
                    except HTTPException as exc:
                        if exc.status_code in {403, 409}:
                            continue
                        raise
                    types = _registered_columns(view, allowed, read)
                    if types:
                        entries.append(
                            {
                                "id": asset_id,
                                "view": view,
                                "fields": _field_options(types),
                            }
                        )
        except (SQLAlchemyError, ValidationError) as error:
            SQLBotLogUtil.warning(
                f"CBI theme asset catalog unavailable: {type(error).__name__}"
            )
        if entries:
            result.append({"id": str(ds.id), "label": ds.name, "assets": entries})
    return result


def _authorized_asset(session, user, ds, asset_id):
    conf = DatasourceConf.model_validate_json(aes_decrypt(ds.configuration))
    for candidate, view, allowed in _source_assets(session, user, ds, conf):
        if asset_id == candidate:
            _require_assets(session, user, ds, {view: set(allowed)})
            return conf, view, allowed
    raise HTTPException(403, "analysis_asset_not_available")


@router.post("/{id_}/asset-preview")
def asset_preview(
    id_: int,
    cmd: AssetIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    ds = _source(session, user, assistant, id_)
    if is_managed_topic(session, id_):
        _, _, baseline, view, allowed, asset_id = _bind(session, user, assistant, id_)
        if not baseline.asset_id or cmd.asset_id != str(asset_id):
            raise HTTPException(403, "analysis_asset_not_available")
    elif _registrable(ds):
        _, view, allowed = _authorized_asset(session, user, ds, cmd.asset_id)
    else:
        raise HTTPException(409, "cbi_theme_legacy_readonly")
    try:
        with get_session(ds) as read:
            _start_read(read)
            types = _registered_columns(view, allowed, read)
            if not types:
                raise HTTPException(403, "analysis_asset_not_available")
            schema, name = view.split(".")
            source = table(name, *(column(key) for key in sorted(types)), schema=schema)
            statement = select(source).limit(20)
            sql = str(
                statement.compile(
                    dialect=read.bind.dialect, compile_kwargs={"literal_binds": True}
                )
            )
            rows = exec_sql(ds, sql, read_session=read, statement=statement)["data"]
    except (ValueError, SQLAlchemyError) as error:
        SQLBotLogUtil.warning(f"CBI theme asset sample failed: {type(error).__name__}")
        raise HTTPException(409, "analysis_source_changed") from error
    SQLBotLogUtil.info(
        f"CBI asset sample source={id_} asset={cmd.asset_id} actor={user.id} view={view} queries=1"
    )
    return {
        "asset_id": cmd.asset_id,
        "view": view,
        "fields": _field_options(types),
        "rows": rows,
        "query_count": 1,
        "sql": sql,
    }


@router.post("/{id_}/register")
def register(
    id_: int,
    cmd: RegisterIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    ds = _source(session, user, assistant, id_)
    # Register through a locked host data source so its connection cannot be
    # rebound concurrently with the initial authorized field contract.
    ds = session.get(
        CoreDatasource, ds.id, populate_existing=True, with_for_update=True
    )
    if not ds or ds.oid != user.oid:
        raise HTTPException(404, "cbi_theme_not_found")
    if not _registrable(ds) or (
        is_managed_topic(session, id_) and not store.get_theme(session, id_)
    ):
        raise HTTPException(409, "cbi_theme_legacy_readonly")
    conf, view, allowed = _authorized_asset(session, user, ds, cmd.asset_id)
    try:
        with get_session(ds) as read:
            _start_read(read)
            types = _registered_columns(view, allowed, read)
    except SQLAlchemyError as error:
        SQLBotLogUtil.warning(
            f"CBI theme registration metadata failed: {type(error).__name__}"
        )
        raise HTTPException(409, "analysis_source_changed") from error
    if not types:
        raise HTTPException(403, "analysis_asset_not_available")
    config = _validate(cmd.content, None, types)
    binding = connection_digest(conf)
    topic = Topic(
        version="pending",
        source_version=cmd.source_data_version,
        timezone="source_calendar",
        default_definition="semantic",
        label=cmd.label,
        definitions=[
            {
                "id": "semantic",
                "label": cmd.label,
                "description": "",
                "view": view,
            }
        ],
        expected_database=conf.database,
        expected_reader=conf.username,
        expected_port=conf.port,
        expected_host=conf.host,
        asset_id=cmd.asset_id,
        connection_digest=binding,
        semantic=config,
    )
    _call(
        store.register,
        session,
        id_,
        user.oid,
        user.id,
        cmd.request_id,
        binding,
        topic.model_dump(mode="json", exclude={"version", "config_digest"}),
    )
    response = detail(id_, session, user, assistant)
    session.commit()
    return response


@router.post("/{id_}/availability")
def availability(
    id_: int,
    cmd: AvailabilityIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    ds = _source(session, user, assistant, id_)
    if not is_managed_topic(session, id_):
        raise HTTPException(409, "cbi_theme_legacy_readonly")
    _call(
        store.set_availability,
        session,
        id_,
        user.id,
        cmd.expected_version,
        cmd.request_id,
        cmd.enabled,
    )
    response = _row(session, user, ds)
    session.commit()
    return response


@router.get("/{id_}")
def detail(
    id_: int, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant
):
    ds = _source(session, user, assistant, id_)
    row = _row(session, user, ds)
    if not row:
        raise HTTPException(404, "cbi_theme_not_found")
    if not row["managed"]:
        return dict(
            row,
            content=None,
            draft=None,
            fields=[],
            validations=[],
            revisions=[],
            publications=[],
            adoption=None,
        )
    _, active, baseline, _, types, _ = _bind(session, user, assistant, id_)
    draft = store.latest_draft(session, id_, user.id)
    revisions = store.list_versions(session, id_)
    publications = store.list_publications(session, id_)
    previews = (
        {v.id: v for v in store.list_previews(session, id_, draft.id)} if draft else {}
    )
    for event in publications:
        if event.validation_id and event.validation_id not in previews:
            prior = store.get_preview(session, id_, event.validation_id)
            if prior and set(prior.receipt.get("fields", ())) <= types.keys():
                previews[prior.id] = prior
    for item in previews.values():
        _require_assets(
            session, user, ds, {row["view"]: set(item.receipt.get("fields", ()))}
        )
    adopted = None
    if row["active_version"]:
        version = next(v for v in revisions if v.version == active.version)
        last = max(
            (p.created_at for p in publications if p.action in {"publish", "rollback"}),
            default=version.created_at,
        )
        adopted = session.scalar(
            select(ChatRecord.id)
            .where(
                ChatRecord.datasource == id_,
                ChatRecord.analysis_state["task_status"].as_string() == "completed",
                ChatRecord.analysis_state["config_version"].as_string()
                == active.version,
                ChatRecord.analysis_state["config_digest"].as_string()
                == active.config_digest,
                cast(
                    ChatRecord.analysis_state["request"]["received_at"].as_string(),
                    DateTime(timezone=True),
                )
                >= last,
            )
            .order_by(ChatRecord.id.desc())
            .limit(1)
        )
    content = active.semantic.model_dump(mode="json") if row["active_version"] else None
    return dict(
        row,
        content=content,
        draft=_public_draft(draft),
        fields=_field_options(types, _editable_baseline(baseline)),
        validations=[_public_validation(item) for item in previews.values()],
        revisions=[
            {
                "version": v.version,
                "created_at": v.created_at,
                "digest": v.content_digest,
            }
            for v in revisions
        ],
        publications=[_receipt(item) for item in publications],
        adoption={"record_id": str(adopted), "version": active.version}
        if adopted
        else None,
    )


@router.post("/{id_}/draft")
def save(
    id_: int,
    cmd: DraftIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _, active, baseline, _, types, _ = _bind(session, user, assistant, id_)
    config = _validate(cmd.content, baseline, types)
    payload = active.model_dump(mode="json", exclude={"config_digest"})
    payload["semantic"] = config.model_dump(mode="json")
    draft = _call(
        store.save_draft,
        session,
        id_,
        user.id,
        cmd.draft_id,
        cmd.revision,
        cmd.base_version,
        payload,
    )
    response = _public_draft(draft)
    session.commit()
    return response


@router.post("/{id_}/validate")
def validate(
    id_: int,
    cmd: DraftRef,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    ds, _, baseline, view, allowed, asset = _bind(session, user, assistant, id_)
    draft = _draft(session, id_, cmd, user.id)
    with get_session(ds) as read:
        _start_read(read)
        types = _bound_columns(baseline, view, allowed, read)
    _validate(draft.content["semantic"], baseline, types)
    dependency = _version_dependency(ds, baseline, view, asset, types)
    receipt = {
        "kind": "static",
        "execution_status": "not_run",
        "activity": {"metadata_check": 1, "business_queries": 0, "model_calls": 0},
        "source_version": baseline.source_version,
        "columns": sorted(types),
        "dependency": dependency,
    }
    item = _call(
        store.save_preview,
        session,
        id_,
        user.id,
        draft.id,
        draft.revision,
        dependency,
        receipt,
    )
    result = _public_validation(item)
    session.commit()
    return result


@router.post("/{id_}/preview")
def preview(
    id_: int,
    cmd: PreviewIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    ds, _, baseline, view, allowed, asset = _bind(session, user, assistant, id_)
    draft = _draft(session, id_, cmd, user.id)
    try:
        with get_session(ds) as read:
            _start_read(read)
            types = _bound_columns(baseline, view, allowed, read)
            config = _validate(draft.content["semantic"], baseline, types)
            dependency = _version_dependency(ds, baseline, view, asset, types)
            if cmd.intent.operation != "new":
                raise HTTPException(422, "cbi_theme_preview_requires_explicit_scope")
            plan = cbi_semantic.resolve(config, cmd.intent)
            stmt, compiled, fields = cbi_semantic.build_query(config, view, plan)
            _require_assets(session, user, ds, {view: fields})
            receipt = {
                "kind": "preview",
                "source_version": baseline.source_version,
                "plan": plan,
                "sql": compiled,
                "fields": sorted(fields),
                "activity": {
                    "metadata_check": 1,
                    "business_queries": 1,
                    "model_calls": 0,
                },
                "dependency": dependency,
            }
            try:
                rows = exec_sql(ds, compiled, read_session=read, statement=stmt)["data"]
                presentation, outputs = cbi_semantic.present(config, plan, rows)
                if not presentation.get("decomposition", {}).get("reconciled", True):
                    raise ValueError("Unreconciled comparison preview")
                receipt.update(
                    execution_status="succeeded",
                    rows=rows,
                    presentation=presentation,
                    outputs=outputs,
                )
            except Exception as error:
                SQLBotLogUtil.warning(
                    f"CBI theme preview failed: {type(error).__name__}"
                )
                receipt.update(
                    execution_status="failed", reason="semantic_query_failed"
                )
    except cbi_semantic.PlanRejected as exc:
        raise HTTPException(
            422, {"reason": exc.reason, "fields": {"intent": "unsupported_scope"}}
        ) from exc
    item = _call(
        store.save_preview,
        session,
        id_,
        user.id,
        draft.id,
        draft.revision,
        dependency,
        jsonable_encoder(receipt),
    )
    response = _public_validation(item)
    session.commit()
    return response


@router.post("/{id_}/quality")
def quality_check(
    id_: int,
    cmd: QualityIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _source(session, user, assistant, id_)
    if not is_managed_topic(session, id_):
        raise HTTPException(409, "cbi_theme_legacy_readonly")

    def execute(target):
        bound = _bind(session, user, assistant, id_)
        return _run_quality(session, user, bound, cmd, target)

    item = _call(
        store.run_quality_check,
        session,
        id_,
        user.id,
        cmd.request_id,
        cmd.model_dump(
            mode="json",
            include={"start", "end", "compare_start", "compare_end", "as_of"},
        ),
        cmd.version,
        cmd.draft_id,
        cmd.revision,
        execute,
    )
    bound = _bind(session, user, assistant, id_)
    response = _public_quality(
        _authorize_quality(session, user, bound, item), detail=True
    )
    session.commit()
    return response


@router.get("/{id_}/quality")
def quality_history(
    id_: int,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
    version: Id | None = None,
    draft_id: Id | None = None,
):
    bound = _bind(session, user, assistant, id_)
    if draft_id is not None:
        if version is not None:
            raise HTTPException(422, {"reason": "quality_target_ambiguous"})
        draft = store.get_draft(session, id_, draft_id)
        if not draft or draft.actor_id != user.id:
            raise HTTPException(404, "cbi_theme_draft_not_found")
    else:
        version = version or bound[1].version
        if version == store.get_theme(session, id_).initial_version:
            return []
        _call(store.get_version, session, id_, version)
    reports = []
    for item in store.list_quality(session, id_, version, draft_id):
        try:
            _authorize_quality(session, user, bound, item)
        except HTTPException as error:
            if error.status_code in {403, 409}:
                continue
            raise
        reports.append(_public_quality(item))
    return reports


@router.get("/{id_}/quality/{check_id}")
def quality_detail(
    id_: int,
    check_id: Id,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    bound = _bind(session, user, assistant, id_)
    item = _authorize_quality(
        session, user, bound, store.get_quality(session, id_, check_id)
    )
    return _public_quality(item, detail=True)


@router.post("/{id_}/confirm")
def confirm(
    id_: int,
    cmd: ConfirmIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    bound = _bind(session, user, assistant, id_)
    ds, _, _, view, _, _ = bound
    preview = store.get_preview(session, id_, cmd.validation_id)
    if not preview or preview.actor_id != user.id:
        raise HTTPException(404, "cbi_theme_preview_not_found")
    _require_assets(session, user, ds, {view: set(preview.receipt.get("fields", ()))})
    if not cmd.note.strip():
        raise HTTPException(422, "cbi_theme_confirmation_note_required")
    if cmd.quality_report_id is not None:
        _authorize_quality(
            session, user, bound, store.get_quality(session, id_, cmd.quality_report_id)
        )
    item = _call(
        store.confirm_preview,
        session,
        id_,
        user.id,
        cmd.validation_id,
        cmd.note.strip(),
        cmd.quality_report_id,
    )
    response = _public_validation(item)
    session.commit()
    return response


@router.post("/{id_}/publish")
def publish(
    id_: int,
    cmd: PublishIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    bound = _bind(session, user, assistant, id_)
    ds, _, baseline, view, allowed, asset = bound
    previous = store.get_publication(session, id_, cmd.request_id)
    quality_source = None
    if previous:
        validation = store.get_preview(session, id_, cmd.validation_id)
        dependency = validation.dependency if validation else ""
    else:
        draft = _draft(session, id_, cmd, user.id)
        if cmd.quality_report_id is not None:
            _authorize_quality(
                session,
                user,
                bound,
                store.get_quality(session, id_, cmd.quality_report_id),
            )
        with get_session(ds) as read:
            _start_read(read)
            types = _bound_columns(baseline, view, allowed, read)
        config = _validate(draft.content["semantic"], baseline, types)
        dependency = _version_dependency(ds, baseline, view, asset, types)
        if config.quality and config.quality.rules:
            fields = _quality_fields(config)
            _require_assets(session, user, ds, {view: fields})
            quality_source = _quality_source(
                user, ds, baseline, view, asset, allowed, fields, dependency
            )
    item = _call(
        store.publish,
        session,
        id_,
        user.id,
        cmd.draft_id,
        cmd.revision,
        cmd.expected_version,
        cmd.validation_id,
        cmd.request_id,
        dependency,
        cmd.quality_report_id,
        quality_source,
    )
    response = _receipt(item)
    session.commit()
    return response


@router.post("/{id_}/rollback")
def rollback(
    id_: int,
    cmd: RollbackIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    ds, _, baseline, view, allowed, _ = _bind(session, user, assistant, id_)
    if not store.get_publication(session, id_, cmd.request_id):
        target = load_effective_topic(
            session, id_, workspace=user.oid, version=cmd.target_version
        )
        with get_session(ds) as read:
            _start_read(read)
            types = _bound_columns(baseline, view, allowed, read)
        _validate(target.semantic.model_dump(mode="json"), baseline, types)
    item = _call(
        store.rollback,
        session,
        id_,
        user.id,
        cmd.target_version,
        cmd.expected_version,
        cmd.request_id,
    )
    response = _receipt(item)
    session.commit()
    return response
