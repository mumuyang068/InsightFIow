from copy import deepcopy
import os
from typing import Literal

from anyio import from_thread
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from apps.chat.api.cbi_multi import direct, persist, render
from apps.chat.models.chat_model import Chat, ChatRecord
from apps.chat.task.cbi_contract import timestamp
from apps.chat.task.cbi_methods import workflow
from apps.chat.task.cbi_methods.binding import BindingId
from apps.chat.task.cbi_methods.contracts import Change, build_plan
from apps.chat.task.cbi_methods.provenance import compose_definition
from apps.chat.task.cbi_methods.registry import available_bindings, resolve_binding
from apps.chat.task.cbi_methods.planning import interpret
from apps.chat.task.cbi_multi_store import load_task, not_found, source_access
from apps.chat.task.cbi_multi_workflow import check_command, receipt
from common.core.deps import CurrentAssistant, CurrentUser, SessionDep

router = APIRouter(prefix="/chat/multi", tags=["Task analytical methods"])


class MethodCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["question", "configure", "execute", "save", "diagnostic", "reread"]
    revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    binding: BindingId | None = None
    question: str | None = Field(default=None, max_length=800, pattern=r"\S")
    saved_record: int | None = None
    run_id: str | None = Field(default=None, max_length=80)
    changes: Change | None = None


def require_method(topic):
    if topic.kind != "methods":
        raise not_found()


def problem(exc):
    if isinstance(exc, ValidationError):
        return {"code": "method_fields_invalid", "fields": [
            {"location": list(issue["loc"]), "type": issue["type"]}
            for issue in exc.errors(include_input=False, include_context=False, include_url=False)
        ]}
    return {"code": str(exc) if str(exc).isidentifier() else "method_parameter_invalid"}


def load_saved(session, user, chat, record_id):
    row = session.get(ChatRecord, record_id)
    if not row or row.create_by != user.id or row.datasource != chat.datasource:
        raise not_found()
    _, source, _, _, _ = load_task(session, user, row.chat_id)
    saving = next((s for s in source.state.get("saved", {}).values() if s["record_id"] == str(record_id)), None)
    run = source.state["runs"].get(saving["run_id"]) if saving else None
    if not run:
        raise HTTPException(409, "saved_method_unavailable")
    if run["id"] in source.state.get("quality", {}):
        raise HTTPException(409, "saved_method_corrected")
    return run


@router.get("/task/{chat_id}/comparison")
def comparison(chat_id: int, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat, task, topic, _, role = load_task(session, user, chat_id)
    require_method(topic)
    handoff = render(chat, task, topic, role)["handoff"]
    if not handoff:
        return {"status": "not_requested", "reference": str(chat_id)}
    candidate_key = handoff.get("candidate")
    if not candidate_key:
        return {"status": "not_ready", "reference": handoff["id"], "phase": handoff["status"]}
    candidate = task.state["runs"].get(candidate_key)
    if not candidate:
        return {"status": "failed", "reference": handoff["id"]}
    return {"status": "ready", "reference": handoff["id"], "candidate": candidate_key,
            "base": handoff["base_run"], "change": candidate["correction"]["change"]}


@router.get("/catalog/{chat_id}")
def catalog(chat_id: int, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat = session.get(Chat, chat_id)
    if not chat or chat.create_by != user.id:
        raise not_found()
    topic, source = source_access(session, user, chat)
    require_method(topic)
    return available_bindings(session, user, source)


@router.post("/task/{chat_id}/method")
def command(chat_id: int, cmd: MethodCommand, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat, task, topic, datasource, role = load_task(session, user, chat_id, lock=True)
    require_method(topic)
    if role != "owner":
        raise not_found()
    state = deepcopy(task.state)
    key, digest, prior = check_command(state, user, cmd)
    if prior:
        return {"receipt": prior, "task": render(chat, task, topic, role)}
    if len(state["runs"]) >= 35 and cmd.action in {"question", "execute", "diagnostic", "reread"}:
        raise HTTPException(409, "run_limit")
    current = state["runs"].get(cmd.run_id or state["work"])
    if cmd.run_id and current is None:
        raise not_found()
    previous = current["definition"]["plan"] if current else None
    previous_id = current["id"] if current else None
    saved = load_saved(session, user, chat, cmd.saved_record) if cmd.saved_record else None
    if saved:
        if cmd.action not in {"question", "reread"} or cmd.changes is not None:
            raise HTTPException(422, "saved_reread_scope_mismatch")
        previous, previous_id = saved["definition"]["plan"], saved["id"]
    binding = (previous and previous["binding"]) if cmd.action == "reread" or saved else (
        cmd.binding or (previous and previous["binding"])
    )
    if cmd.action == "save" and current:
        binding = current["definition"]["plan"]["binding"]
    if not binding:
        raise HTTPException(422, "method_binding_required")
    if previous and previous["binding"] != binding:
        previous, previous_id = None, None
    pinned = previous["binding_version"] if previous else None
    if cmd.action == "execute" and state.get("definition"):
        if state["definition"]["plan"]["binding"] != binding:
            raise HTTPException(409, "method_draft_binding_changed")
        pinned = state["definition"]["plan"]["binding_version"]
    spec = resolve_binding(session, user, datasource, binding, pinned, history=bool(pinned))
    trace = None
    try:
        if cmd.action == "question":
            if not cmd.question:
                raise ValueError("method_question_required")
            choice, trace = from_thread.run(interpret, cmd.question, spec, previous, bool(saved))
            outcome = {"model": trace}
            if choice is None:
                outcome["outcome"] = "invalid_plan"
            elif choice.action in {"clarify", "unsupported"}:
                outcome.update(outcome=choice.action, clarification=choice.clarification)
            elif choice.action == "inspect":
                if not current or saved or not previous or choice.changes.model_fields_set:
                    raise ValueError("member_scope_invalid")
                members = set(choice.members)
                records = current["result"]["trace"]
                outcome.update(outcome="read_members", run_id=current["id"], members=[
                    row for row in records if not members or (row.get("attempt") or row.get("customer")) in members
                ])
            else:
                if saved and (choice.action != "reread" or choice.changes.model_fields_set):
                    raise ValueError("saved_reread_scope_mismatch")
                if choice.action == "reread" and not (previous and not choice.changes.model_fields_set):
                    raise ValueError("saved_reread_scope_mismatch")
                if choice.members:
                    raise ValueError("member_scope_invalid")
                plan = build_plan(spec, choice.changes, previous, previous_id)
                definition = compose_definition(plan, spec, choice.changes, previous_id, "model", cmd.request_id)
                state["definition"] = definition
                item = workflow.run(session, user, chat, state, topic, datasource, definition)
                item["question"] = cmd.question
                outcome.update(outcome="ran", run_id=item["id"], record_id=item["record_id"])
        elif cmd.action == "save":
            if not current:
                raise ValueError("method_run_required")
            if current["id"] in state.get("quality", {}):
                raise ValueError("method_result_corrected")
            plan = current["definition"]["plan"]
            build_plan(spec, Change(), plan, current["id"])
            provenance = current["definition"]["sources"]
            if set(provenance) != set(plan) or any(
                source["kind"] == "run" and source.get("id") != current["definition"]["parent_run"]
                for source in provenance.values()
            ):
                raise ValueError("parameter_provenance_incomplete")
            if not current.get("record_id"):
                raise ValueError("no_published_record")
            state.setdefault("saved", {}).setdefault(current["id"], {
                "run_id": current["id"], "record_id": current["record_id"], "at": timestamp(),
            })
            outcome = {"outcome": "saved", **state["saved"][current["id"]]}
        elif cmd.action == "configure":
            change = cmd.changes or Change()
            plan = build_plan(spec, change, previous, previous_id)
            state["definition"] = compose_definition(plan, spec, change, previous_id, "controls", cmd.request_id)
            outcome = {"outcome": "configured"}
        else:
            diagnostic = cmd.action == "diagnostic"
            if diagnostic and (os.environ.get("SQLBOT_CBI_METHOD_DIAGNOSTIC") != "1" or binding != "r11_repeat"):
                raise not_found()
            if cmd.action == "execute":
                definition = state["definition"]
                if not definition or definition["plan"]["binding"] != binding:
                    raise ValueError("method_draft_required")
            else:
                if cmd.action == "reread" and not previous:
                    raise ValueError("method_run_required")
                change = Change(cohort_channel="ad", repeat_channel=None, eligibility="paid") if diagnostic else Change()
                plan = build_plan(spec, change, previous, previous_id)
                definition = compose_definition(plan, spec, change, previous_id, "controls", cmd.request_id)
                state["definition"] = definition
            item = workflow.run(session, user, chat, state, topic, datasource, definition, diagnostic=diagnostic)
            outcome = {"outcome": "ran", "run_id": item["id"], "record_id": item["record_id"]}
    except ValueError as exc:
        if trace is None:
            raise HTTPException(422, problem(exc)) from exc
        # Keep first model output, while a rejected plan leaves the old definition and result intact.
        state = deepcopy(task.state)
        outcome = {"outcome": "invalid_plan", "problem": problem(exc), "model": trace}
    if state.get("work") and outcome.get("outcome") == "ran":
        state["scope"] = state["runs"][state["work"]]["definition"]["plan"]["store"]
    got = receipt(state, cmd, key, digest, outcome)
    persist(session, task, state)
    return {"receipt": got, "task": render(chat, task, topic, role)}
