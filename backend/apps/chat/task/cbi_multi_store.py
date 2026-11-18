import os
from base64 import b64decode, b64encode
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Column, ForeignKey, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as SQLField, SQLModel

from apps.chat.models.chat_model import Chat, ChatRecord
from apps.chat.task.cbi_analysis import _require_assets
from apps.chat.task.cbi_contract import Identifier, Store, timestamp
from apps.chat.task.cbi_multi_files import FileIssue, approved_fields, parse_csv, profile
from apps.datasource.models.datasource import CoreDatasource, DatasourceConf
from apps.datasource.utils.utils import aes_decrypt
from apps.system.models.system_model import UserWsModel
from apps.system.models.user import UserModel


class MultiTopic(BaseModel):
    datasource: int
    label: str
    view: Identifier
    database: str
    reader: str
    port: int
    version: str
    sha256: str
    window: str
    currency: str
    kind: str = 'files'
    binding_manifests: dict = Field(default_factory=dict)


class MultiTask(SQLModel, table=True):
    __tablename__ = "cbi_multi_task"
    chat_id: int = SQLField(sa_column=Column(BigInteger, ForeignKey("chat.id", ondelete="CASCADE"), primary_key=True))
    state: dict = SQLField(sa_column=Column(JSONB, nullable=False))


def topic_for(source):
    for variable in ("SQLBOT_CBI_MULTI", "SQLBOT_CBI_METHODS"):
        location = os.environ.get(variable)
        if location and Path(location).is_file():
            topic = MultiTopic.model_validate_json(Path(location).read_bytes())
            if topic.datasource == source:
                return topic
    return None


def not_found():
    return HTTPException(404, "multi_analysis_not_found")


def source_access(session, user, chat):
    topic = topic_for(chat.datasource)
    if not topic or chat.oid != user.oid or chat.origin != 0:
        raise not_found()
    if not session.scalar(select(UserWsModel.uid).where(UserWsModel.uid == user.id, UserWsModel.oid == chat.oid)):
        raise not_found()
    datasource = session.get(CoreDatasource, chat.datasource)
    if not datasource or datasource.oid != user.oid or datasource.type != "pg":
        raise HTTPException(409, "analysis_source_changed")
    conf = DatasourceConf.model_validate_json(aes_decrypt(datasource.configuration))
    if (conf.host, conf.port, conf.database, conf.username, conf.dbSchema) != (
        "127.0.0.1", topic.port, topic.database, topic.reader, topic.view.split(".")[0],
    ):
        raise HTTPException(409, "analysis_source_changed")
    fields = {"binding", "payload", "manifest"} if topic.kind == "methods" else {"store", "sku", "net"}
    _require_assets(session, user, datasource, {topic.view: fields})
    return topic, datasource


def load_task(session, user, chat_id, *, lock=False, create=False):
    chat = session.get(Chat, chat_id)
    if not chat or chat.oid != user.oid:
        raise not_found()
    record = session.get(MultiTask, chat.id)
    handoff = record.state.get("handoff") if record else None
    owner = chat.create_by == user.id
    analyst = handoff and handoff["receiver"] == str(user.id)
    if not (owner or analyst):
        raise not_found()
    if lock:
        chat = session.scalars(
            select(Chat).where(Chat.id == chat_id).with_for_update().execution_options(populate_existing=True)
        ).first()
        if not chat or chat.oid != user.oid:
            raise not_found()
        record = session.get(MultiTask, chat.id, populate_existing=True)
        handoff = record.state.get("handoff") if record else None
        owner = chat.create_by == user.id
        if not owner and (not handoff or handoff["receiver"] != str(user.id)):
            raise not_found()
    topic, datasource = source_access(session, user, chat)
    if record and topic.kind == "methods":
        require_method_records(session, user, datasource, record.state, owner)
    if not record and create and owner:
        record = MultiTask(chat_id=chat.id, state={
            "revision": 0, "scope": None, "question": None, "parent_record_id": None,
            "files": {}, "definition": None, "runs": {}, "work": None, "adopted": None,
            "handoff": None, "actions": {},
        })
        session.add(record)
        session.flush()
    if not record:
        raise not_found()
    return chat, record, topic, datasource, "owner" if owner else "analyst"


def require_method_records(session, user, datasource, state, owner):
    from apps.chat.task.cbi_methods.registry import resolve_binding

    handoff = state.get("handoff") or {}
    visible = state["runs"].values() if owner else (
        state["runs"][key] for key in (handoff.get("base_run"), handoff.get("candidate")) if key in state["runs"]
    ) if handoff.get("status") != "requested" else ()
    references = {(plan["binding"],plan["binding_version"]) for run in visible
                  if (plan := run["definition"].get("plan"))}
    for key, version in references:
        resolve_binding(session, user, datasource, key, version, history=True)


def bind_scope(session, user, chat, state, start):
    if state["question"]:
        raise HTTPException(409, "scope_already_bound")
    store, question = start.store, start.question
    if start.parent_record_id is not None:
        parent = session.get(ChatRecord, start.parent_record_id)
        marker = (parent.analysis_state or {}).get("multi") if parent else None
        if not parent or parent.create_by != user.id or parent.datasource != chat.datasource or not marker:
            raise not_found()
        _, parent_task, _, _, _ = load_task(session, user, parent.chat_id)
        parent_run = parent_task.state["runs"].get(marker["run_id"])
        if not parent_run:
            raise not_found()
        store = parent_run["definition"]["store"]
    if not store:
        topic = topic_for(chat.datasource)
        if not topic or topic.kind != "methods":
            raise HTTPException(422, "store_required")
    state.update(scope=store, question=question, parent_record_id=(
        str(start.parent_record_id) if start.parent_record_id is not None else None
    ))


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store: Store | None = None
    question: str = Field(min_length=1, max_length=800, pattern=r"\S")
    parent_record_id: int | None = None


def add_file(state, user, *, purpose, filename, raw, replaces):
    if len(state["files"]) >= 20:
        raise HTTPException(409, "file_version_limit")
    if replaces:
        previous = state["files"].get(replaces)
        if not previous or previous["purpose"] != purpose:
            raise HTTPException(409, "invalid_replacement")
    filename = filename.replace("\\", "/").split("/")[-1]
    if Path(filename).suffix.lower() != ".csv" or len(filename) > 180 or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, "csv_required")
    id_ = uuid4().hex
    summary, fields, issue = {}, {}, None
    try:
        table = parse_csv(raw)
        summary = profile(table)
        fields = approved_fields(purpose, table)
    except FileIssue as exc:
        issue = exc.detail
    state["files"][id_] = {
        "id": id_, "owner": str(user.id), "name": filename, "purpose": purpose,
        "sha256": sha256(raw).hexdigest(), "size": len(raw), "replaces": replaces,
        "created": timestamp(), "summary": summary, "fields": fields, "issue": issue,
        "content": b64encode(raw).decode("ascii"),
    }
    return id_


def selected_files(state, ids, allowed=None):
    prepared = {}
    for role, id_ in ids.items():
        file = state["files"].get(id_)
        if not file or file["purpose"] != role or (allowed is not None and id_ not in allowed):
            raise not_found()
        if file["issue"]:
            raise HTTPException(422, file["issue"])
        prepared[role] = file
    if not {"roster", "costs"}.issubset(prepared):
        raise HTTPException(422, "required_inputs_missing")
    return prepared


def decode_file(file):
    raw = b64decode(file["content"], validate=True)
    if sha256(raw).hexdigest() != file["sha256"]:
        raise HTTPException(409, "file_integrity_failed")
    return parse_csv(raw)


def public_file(file):
    return {k: v for k, v in file.items() if k != "content"}


def visible_state(chat, state, topic, role):
    handoff = state["handoff"]
    analyst = role == "analyst"
    shared = set(handoff["files"]) if analyst else state["files"].keys()
    accepted = analyst and handoff["status"] != "requested"
    visible_runs = (
        {key: run for key, run in state["runs"].items() if key in (handoff["base_run"], handoff.get("candidate"))}
        if accepted else {} if analyst else state["runs"]
    )
    runs = {
        key: {k: v for k, v in run.items() if k != "snapshot"}
        for key, run in visible_runs.items()
    }
    return {
        "id": str(chat.id), "role": role, "revision": state["revision"], "scope": state["scope"],
        "question": state["question"], "parent_record_id": state["parent_record_id"],
        "topic": topic.model_dump(exclude={"database", "reader", "port"}),
        "files": [public_file(state["files"][key]) for key in sorted(shared, key=lambda x: state["files"][x]["created"])]
                 if not analyst or accepted else [],
        "definition": deepcopy(state["definition"]) if not analyst else None,
        "runs": runs, "work": state["work"] if not analyst else None,
        "adopted": state["adopted"] if not analyst else None,
        "handoff": handoff,
        **({"saved": state.get("saved", {}), "quality": state.get("quality", {})}
           if not analyst and topic.kind == "methods" else {}),
    }


def collaborator(session, sender, chat, receiver):
    target = session.get(UserModel, receiver)
    if not target or target.status != 1 or target.id == sender.id or not session.scalar(
        select(UserWsModel.uid).where(UserWsModel.uid == receiver, UserWsModel.oid == chat.oid)
    ):
        raise not_found()
    return target
