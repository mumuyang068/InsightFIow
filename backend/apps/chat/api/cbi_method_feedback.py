from hashlib import sha256
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from apps.chat.api.cbi_method_admin import require_maintainer
from apps.chat.api.cbi_multi import direct
from apps.chat.models.chat_model import Chat
from apps.chat.task.cbi_contract import timestamp
from apps.chat.task.cbi_methods.models import MethodFeedback
from apps.chat.task.cbi_methods.registry import resolve_binding
from apps.chat.task.cbi_multi_store import MultiTask, load_task, not_found, source_access
from common.core.deps import CurrentAssistant, CurrentUser, SessionDep

router = APIRouter(prefix="/chat/method-feedback",tags=["Method feedback"])


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    run_id: str = Field(min_length=1,max_length=80)
    text: str = Field(min_length=1,max_length=1500)
    expected: str = Field(min_length=1,max_length=1500)
    request_id: str = Field(min_length=1,max_length=80)


class Processing(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["processing","needs_info","resolved"]
    revision: int = Field(ge=0)
    request_id: str = Field(min_length=1,max_length=80)
    category: Literal["condition_difference","data_issue","method_issue","other"] | None = None
    note: str = Field(min_length=1,max_length=1500)
    comparison_chat: int | None = None
    comparison_run: str | None = Field(default=None,max_length=80)


def digest(command):
    return sha256(command.model_dump_json().encode()).hexdigest()


def run_summary(session, user, chat, task, source, run_id):
    run = task.state["runs"].get(run_id)
    plan = (run or {}).get("definition", {}).get("plan")
    if not run or not plan or not run.get("record_id"):
        raise not_found()
    actual = resolve_binding(session, user, source, plan["binding"], plan["binding_version"], history=True)
    result = run["result"]
    return {"chat_id":str(chat.id),"run_id":run_id,"record_id":run["record_id"],"question":run.get("question",task.state["question"]),
            "adopted":actual.model_context(),"plan":plan,"sources":run["definition"]["sources"],
            "result":{key:result[key] for key in ("numerator","denominator","rate_pct","status")}}


def public(row):
    return {"id":row.id,**{k:v for k,v in row.state.items() if k not in {"actions","digest","request_id"}}}


@router.get("/task/{chat_id}")
def list_own(chat_id: int,session: SessionDep,user: CurrentUser,assistant: CurrentAssistant):
    direct(assistant)
    _, _, _, _, role = load_task(session,user,chat_id)
    if role != "owner":
        raise not_found()
    return [public(item) for item in session.scalars(select(MethodFeedback).where(
        MethodFeedback.chat_id == chat_id, MethodFeedback.workspace == user.oid,
    )) if item.state["author"] == str(user.id)]


@router.post("/task/{chat_id}")
def submit(chat_id: int, cmd: Submission,session: SessionDep,user: CurrentUser,assistant: CurrentAssistant):
    direct(assistant)
    chat, task, _, source, role = load_task(session,user,chat_id,lock=True)
    if role != "owner":
        raise not_found()
    for row in session.scalars(select(MethodFeedback).where(MethodFeedback.chat_id == chat_id)):
        if row.state["request_id"] == cmd.request_id:
            if row.state["digest"] != digest(cmd):
                raise HTTPException(409,"request_id_reused")
            return public(row)
    run = run_summary(session,user,chat,task,source,cmd.run_id)
    row = MethodFeedback(id=uuid4().hex,workspace=user.oid,chat_id=chat_id,state={
        "author":str(user.id),"text":cmd.text,"expected":cmd.expected,"run":run,"request_id":cmd.request_id,
        "digest":digest(cmd),"created_at":timestamp(),"status":"pending","revision":0,"actions":{},
    })
    session.add(row)
    response=public(row)
    session.commit()
    return response


def context(session,user,chat_id):
    chat=session.get(Chat,chat_id)
    task=session.get(MultiTask,chat_id)
    if not chat or not task:
        raise not_found()
    _, source=source_access(session,user,chat)
    return chat, task, source


@router.get("/admin")
def admin_list(session: SessionDep,user: CurrentUser,assistant: CurrentAssistant):
    direct(assistant)
    require_maintainer(session,user)
    result=[]
    for row in session.scalars(select(MethodFeedback).where(MethodFeedback.workspace==user.oid)):
        try:
            chat,task,source=context(session,user,row.chat_id)
            run_summary(session,user,chat,task,source,row.state["run"]["run_id"])
            current=resolve_binding(session,user,source,row.state["run"]["plan"]["binding"]).model_context()
        except HTTPException:
            continue
        comparisons = []
        for other_chat in session.scalars(select(Chat).join(MultiTask, MultiTask.chat_id == Chat.id).where(
            Chat.oid == user.oid, Chat.datasource == chat.datasource, Chat.create_by == chat.create_by,
        ).order_by(Chat.id.desc()).limit(50)):
            other_task = session.get(MultiTask, other_chat.id)
            for run in other_task.state["runs"].values():
                plan = run["definition"].get("plan", {})
                if (plan.get("binding"), plan.get("store")) != (row.state["run"]["plan"]["binding"], row.state["run"]["plan"]["store"]):
                    continue
                if not run.get("record_id") or run["id"] == row.state["run"]["run_id"]:
                    continue
                try:
                    comparisons.append(run_summary(session, user, other_chat, other_task, source, run["id"]))
                except HTTPException:
                    continue
        result.append({**public(row),"current":current,"comparisons":comparisons})
    return result


def transition(old, new, category, comparison):
    allowed={"pending":{"processing","needs_info"},"processing":{"needs_info","resolved"},
             "needs_info":{"processing","resolved"},"resolved":set()}
    if new not in allowed[old]:
        raise HTTPException(409,"feedback_state_changed")
    if new=="resolved" and (not category or (category=="condition_difference" and not comparison)):
        raise HTTPException(422,"feedback_resolution_evidence_required")


@router.post("/admin/{id_}")
def process(id_: str, cmd: Processing,session: SessionDep,user: CurrentUser,assistant: CurrentAssistant):
    direct(assistant)
    require_maintainer(session,user)
    row=session.scalars(select(MethodFeedback).where(MethodFeedback.id==id_,MethodFeedback.workspace==user.oid)
                        .with_for_update().execution_options(populate_existing=True)).first()
    if not row:
        raise not_found()
    state=dict(row.state)
    chat,task,source=context(session,user,row.chat_id)
    old=run_summary(session,user,chat,task,source,state["run"]["run_id"])
    actions=dict(state["actions"])
    key=f"{user.id}:{cmd.request_id}"
    if key in actions:
        if actions[key]["digest"] != digest(cmd):
            raise HTTPException(409,"request_id_reused")
        return actions[key]["reply"]
    if state["revision"]!=cmd.revision:
        raise HTTPException(409,"feedback_revision_changed")
    other=None
    if cmd.comparison_chat and cmd.comparison_run:
        other_chat, other_task, other_source=context(session,user,cmd.comparison_chat)
        other=run_summary(session,user,other_chat,other_task,other_source,cmd.comparison_run)
        if (other_chat.create_by != chat.create_by or other_source.id != source.id
            or (old["plan"]["binding"],old["plan"]["store"])!=(other["plan"]["binding"],other["plan"]["store"])
            or old["run_id"]==other["run_id"]):
            raise HTTPException(422,"feedback_comparison_scope_mismatch")
    transition(state["status"],cmd.status,cmd.category,other)
    if cmd.status=="resolved" and cmd.category=="condition_difference":
        if not any(old["plan"].get(key) != other["plan"].get(key) for key in (
            "entry_start","entry_end","window_hours","observation_asof","eligibility","cohort_channel","repeat_channel",
        )):
            raise HTTPException(422, "feedback_condition_difference_unverified")
    state.update(status=cmd.status,revision=state["revision"]+1,handling={
        "by":str(user.id),"at":timestamp(),"note":cmd.note,"category":cmd.category,"comparison":other,"checked_running_version":old["adopted"]["version"],
    })
    row.state=state
    response=public(row)
    actions[key]={"digest":digest(cmd),"reply":response}
    row.state={**state,"actions":actions}
    session.commit()
    return response
