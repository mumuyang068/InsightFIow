import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from apps.chat.api.cbi_multi import direct
from apps.chat.task.cbi_contract import timestamp
from apps.chat.task.cbi_methods.binding import BindingId, EventConfig
from apps.chat.task.cbi_methods.models import BindingVersion
from apps.chat.task.cbi_methods.registry import (
    list_versions, locked_head, save_candidate, unknown, verify_view, version_view,
)
from apps.chat.task.cbi_multi_store import topic_for
from apps.datasource.models.datasource import CoreDatasource, DatasourceConf
from apps.datasource.utils.utils import aes_decrypt
from apps.system.models.system_model import UserWsModel
from common.core.deps import CurrentAssistant, CurrentUser, SessionDep

router = APIRouter(prefix="/chat/method-admin", tags=["Method configuration"])


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base: int = Field(ge=0)
    config: EventConfig


class VersionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["check", "activate"]
    base: int = Field(ge=0)


def require_maintainer(session, user):
    row = session.execute(select(UserWsModel.weight).where(
        UserWsModel.uid == user.id, UserWsModel.oid == user.oid,
    )).first()
    if not user.isAdmin and (not row or row.weight < 1):
        raise HTTPException(403, "method_maintainer_required")


def native_source(session, user, datasource):
    source = session.get(CoreDatasource, datasource)
    topic = topic_for(datasource)
    if not source or not topic or topic.kind != "methods" or source.oid != user.oid or source.type != "pg":
        raise unknown()
    conf = DatasourceConf.model_validate_json(aes_decrypt(source.configuration))
    if (conf.host, conf.port, conf.username, conf.database, conf.dbSchema) != (
        "127.0.0.1", topic.port, topic.reader, topic.database, topic.view.split(".")[0],
    ):
        raise HTTPException(409, "analysis_source_changed")
    return source, topic


@router.get("/bindings")
def bindings(session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    require_maintainer(session, user)
    sources = session.scalars(select(CoreDatasource).where(CoreDatasource.oid == user.oid)).all()
    eligible = []
    for item in sources:
        topic = topic_for(item.id)
        if topic and topic.kind == "methods":
            eligible.append({"id":item.id,"name":item.name})
    return {"versions":list_versions(session, user.oid),"sources":eligible}


@router.post("/bindings")
def save(cmd: Candidate, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    require_maintainer(session, user)
    native_source(session, user, cmd.config.datasource)
    row, active = save_candidate(session, user.oid, cmd.config, cmd.base, user.id)
    response = version_view(row, active)
    session.commit()
    return response


@router.post("/bindings/{id_}/{number}")
def advance(id_: BindingId, number: int, cmd: VersionAction, session: SessionDep,
            user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    require_maintainer(session, user)
    head = locked_head(session, user.oid, id_)
    row = session.get(BindingVersion, (user.oid, id_, number), populate_existing=True)
    if not row:
        raise unknown()
    if row.state.get("published_at"):
        if cmd.action == "activate" and head.active == number and cmd.base == row.state["based_on"]:
            return version_view(row, head.active)
        raise HTTPException(409, "binding_version_immutable")
    if head.active != cmd.base or row.state["based_on"] != cmd.base:
        raise HTTPException(409, "binding_base_changed")
    state = dict(row.state)
    config = EventConfig.model_validate_json(json.dumps(state["config"]))
    source, topic = native_source(session, user, config.datasource)
    if cmd.action == "activate" and (state.get("check") or {}).get("status") != "passed":
        raise HTTPException(409, "binding_check_required")
    try:
        state["check"] = verify_view(session, user, source, config, topic)
    except HTTPException as exc:
        if cmd.action == "activate":
            raise
        state["check"] = {"status":"failed","detail":exc.detail,"checked_at":timestamp(),"by":str(user.id)}
    if cmd.action == "activate":
        head.active = number
        state.update(published_at=timestamp(), published_by=str(user.id))
    row.state = state
    result = version_view(row, head.active)
    session.commit()
    return result
