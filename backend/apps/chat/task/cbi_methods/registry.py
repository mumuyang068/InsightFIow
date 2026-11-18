import json
import os

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from apps.chat.task.cbi_analysis import _require_assets
from apps.chat.task.cbi_contract import timestamp
from apps.datasource.crud.datasource import get_table_obj_by_ds
from apps.db.db import get_session

from .binding import EventConfig, FIXTURE_VERSION, configured
from .compatibility import COMPATIBILITY
from .models import BindingHead, BindingVersion

EVENT_FIELDS = {"event_id", "attempt_id", "event_name", "occurred_at", "store_id"}


def unknown():
    return HTTPException(404, "method_binding_unavailable")


def ensure_view(session, user, source, view):
    for asset in get_table_obj_by_ds(session, user, source):
        if f"{asset.schema}.{asset.table.table_name}" == view:
            missing = sorted(EVENT_FIELDS - {item.field_name for item in asset.fields or []})
            if missing:
                raise HTTPException(422, {"code": "missing_view_fields", "fields": missing})
            _require_assets(session, user, source, {view: EVENT_FIELDS})
            return
    raise HTTPException(422, {"code": "view_not_registered", "view": view})


def resolve_binding(session, user, source, id_, version=None, *, history=False):
    if id_ in COMPATIBILITY:
        binding = COMPATIBILITY[id_]
        if version not in (None, FIXTURE_VERSION):
            raise unknown()
        if binding.test_only and not history and os.environ.get("SQLBOT_CBI_METHOD_TEST") != "1":
            raise unknown()
        return binding
    head = session.get(BindingHead, (user.oid, id_), populate_existing=True)
    if not head:
        raise unknown()
    number = head.active
    if version is not None:
        if not version.startswith("v") or not version[1:].isdecimal() or len(version) > 10:
            raise unknown()
        number = int(version[1:])
    row = session.get(BindingVersion, (user.oid, id_, number), populate_existing=True) if number else None
    if not row or not row.state.get("published_at"):
        raise unknown()
    config = EventConfig.model_validate_json(json.dumps(row.state["config"]))
    if config.datasource != source.id:
        raise unknown()
    ensure_view(session, user, source, config.view)
    return configured(config, user.oid, number)


def available_bindings(session, user, source):
    ids = list(COMPATIBILITY) + list(session.scalars(
        select(BindingHead.id).where(BindingHead.workspace == user.oid, BindingHead.active > 0)
    ))
    result = []
    for id_ in ids:
        try:
            result.append(resolve_binding(session, user, source, id_).model_context())
        except HTTPException as exc:
            if exc.status_code not in {403, 404, 422}:
                raise
    return result


def locked_head(session, workspace, id_, *, create=False):
    if create:
        session.execute(insert(BindingHead).values(workspace=workspace, id=id_, active=0, last_version=0).on_conflict_do_nothing())
    head = session.scalars(select(BindingHead)
        .where(BindingHead.workspace == workspace, BindingHead.id == id_).with_for_update()
        .execution_options(populate_existing=True)).first()
    if not head:
        raise unknown()
    return head


def save_candidate(session, workspace, config, base, actor):
    if config.id in COMPATIBILITY:
        raise HTTPException(409, "reserved_binding_id")
    head = locked_head(session, workspace, config.id, create=True)
    if head.active != base:
        raise HTTPException(409, "binding_base_changed")
    head.last_version += 1
    candidate = BindingVersion(workspace=workspace, id=config.id, version=head.last_version, state={
        "config": config.model_dump(mode="json"), "based_on": base, "created_by": str(actor),
        "created_at": timestamp(), "check": None, "published_at": None,
    })
    session.add(candidate)
    return candidate, head.active


def verify_view(session, user, source, config, topic):
    ensure_view(session, user, source, config.view)
    schema, view = config.view.split(".")
    with get_session(source) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '5s'"))
        who, mode = db.execute(text("SELECT current_user,current_setting('transaction_read_only')")).one()
        if who != topic.reader or mode != "on":
            raise HTTPException(409, "analysis_source_not_readonly")
        try:
            db.execute(text(f'SELECT event_id,attempt_id,event_name,occurred_at,store_id FROM "{schema}"."{view}" LIMIT 0'))
            available = db.execute(text("""SELECT data_type FROM information_schema.columns
                WHERE table_schema=:schema AND table_name=:view AND column_name='occurred_at'"""),
                {"schema":schema,"view":view}).scalar()
            if available != "timestamp with time zone":
                raise HTTPException(422, {"code":"invalid_event_timestamp_type","fields":["occurred_at"]})
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, {"code":"event_view_unavailable","view":config.view}) from exc
    return {"status":"passed","checked_at":timestamp(),"by":str(user.id),"view":config.view}


def list_versions(session, workspace):
    versions = session.scalars(select(BindingVersion)
        .where(BindingVersion.workspace == workspace)
        .order_by(BindingVersion.id, BindingVersion.version.desc())).all()
    heads = {row.id: row.active for row in session.scalars(select(BindingHead).where(BindingHead.workspace == workspace))}
    return [version_view(row, heads[row.id]) for row in versions]


def version_view(row, active):
    return {"id":row.id,"version":row.version,"active":active,**row.state}
