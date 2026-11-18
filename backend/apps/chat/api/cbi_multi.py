import io
import json
from base64 import b64decode
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from anyio import from_thread
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from apps.chat.models.chat_model import Chat
from apps.chat.task.cbi_contract import timestamp
from apps.chat.task import cbi_multi_files
from apps.chat.task.cbi_multi_execution import plan
from apps.chat.task.cbi_multi_files import MAX_BYTES, FileIssue
from apps.chat.task.cbi_multi_store import (
    MultiTask, Start, add_file, bind_scope, load_task, not_found, selected_files, source_access,
    topic_for, visible_state,
)
from apps.chat.task.cbi_multi_workflow import (
    Command, Role, check_command, file_delta, perform, receipt, set_definition,
)
from apps.system.models.system_model import UserWsModel
from apps.system.models.user import UserModel
from common.core.deps import CurrentAssistant, CurrentUser, SessionDep

router = APIRouter(prefix="/chat/multi", tags=["Task file analysis"])


def direct(assistant):
    if assistant:
        raise not_found()


def render(chat, task, topic, role):
    return visible_state(chat, task.state, topic, role)


def persist(session, task, state):
    task.state = state
    session.add(task)
    session.commit()


@router.get("/inbox")
def inbox(session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    query = select(MultiTask, Chat).join(Chat, Chat.id == MultiTask.chat_id).where(
        Chat.oid == user.oid, MultiTask.state["handoff"]["receiver"].as_string() == str(user.id)
    )
    result = []
    for task, chat in session.execute(query):
        try:
            topic, _ = source_access(session, user, chat)
        except HTTPException:
            continue
        handoff = task.state["handoff"]
        result.append({"id": str(chat.id), "status": handoff["status"], "question": handoff["question"], "kind": topic.kind})
    return result


@router.get("/available/{chat_id}")
def available(chat_id: int, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat = session.get(Chat, chat_id)
    if not chat or chat.create_by != user.id or not topic_for(chat.datasource):
        return {"enabled": False}
    topic, _ = source_access(session, user, chat)
    return {"enabled": True, "label": topic.label, "kind": topic.kind}


@router.get("/task/{chat_id}")
def inspect(chat_id: int, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat, task, topic, _, role = load_task(session, user, chat_id)
    return render(chat, task, topic, role)


@router.post("/task/{chat_id}")
def start(chat_id: int, cmd: Start, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat, task, topic, _, role = load_task(session, user, chat_id, lock=True, create=True)
    if role != "owner":
        raise not_found()
    state = deepcopy(task.state)
    bind_scope(session, user, chat, state, cmd)
    chat.brief = state["question"][:48]
    chat.brief_generate = True
    session.add(chat)
    persist(session, task, state)
    return render(chat, task, topic, role)


@router.post("/task/{chat_id}/files")
def upload(chat_id: int, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant,
           purpose: Role = Form(), replaces: str | None = Form(None), file: UploadFile = File()):
    direct(assistant)
    chat, task, topic, _, role = load_task(session, user, chat_id, lock=True)
    if role != "owner" or topic.kind != "files":
        raise not_found()
    state = deepcopy(task.state)
    raw = file.file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "file_too_large")
    id_ = add_file(state, user, purpose=purpose, filename=file.filename or "", raw=raw, replaces=replaces)
    state["revision"] += 1
    persist(session, task, state)
    return {"file_id": id_, "task": render(chat, task, topic, role)}


@router.get("/task/{chat_id}/file/{file_id}")
def original(chat_id: int, file_id: str, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    _, task, _, _, role = load_task(session, user, chat_id)
    handoff = task.state["handoff"]
    if role != "owner" and (handoff["status"] == "requested" or file_id not in handoff["files"]):
        raise not_found()
    file = task.state["files"].get(file_id)
    if not file:
        raise not_found()
    return {"base64": file["content"], "name": file["name"], "sha256": file["sha256"]}


@router.get("/task/{chat_id}/reviewers")
def reviewers(chat_id: int, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat, _, _, _, role = load_task(session, user, chat_id)
    if role != "owner":
        raise not_found()
    query = select(UserModel.id, UserModel.name).join(UserWsModel, UserWsModel.uid == UserModel.id)
    query = query.where(UserWsModel.oid == chat.oid, UserModel.id != user.id, UserModel.status == 1)
    return [{"id": str(row.id), "name": row.name} for row in session.execute(query)]


@router.get("/task/{chat_id}/replacement/{file_id}")
def replacement(chat_id: int, file_id: str, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    _, task, _, _, role = load_task(session, user, chat_id)
    if role != "owner":
        raise not_found()
    try:
        _, _, changes = file_delta(task.state, task.state["definition"],
                                   Command(action="apply_file", revision=0, request_id="preview", file_id=file_id))
    except FileIssue as exc:
        raise HTTPException(422, exc.detail) from exc
    return changes


@router.post("/task/{chat_id}/action")
def action(chat_id: int, cmd: Command, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat, task, topic, datasource, role = load_task(session, user, chat_id, lock=True)
    state = deepcopy(task.state)
    key, digest, prior = check_command(state, user, cmd)
    if prior:
        return {"receipt": prior, "task": render(chat, task, topic, role)}
    try:
        if cmd.action == "plan":
            if role != "owner" or topic.kind != "files":
                raise not_found()
            files = selected_files(state, cmd.inputs or {})
            definition, trace = from_thread.run(plan, state, files, topic, cmd.question or state["question"])
            trace.update(id=uuid4().hex, at=timestamp())
            state.setdefault("plans", []).append(trace)
            if definition:
                definition["plan_trace"] = trace["id"]
                set_definition(state, definition, files)
            result = {"outcome": trace["outcome"], "plan": trace,
                      "definition_id": definition["id"] if definition else None}
        else:
            result = perform(session, user, chat, state, topic, datasource, role, cmd)
    except FileIssue as exc:
        raise HTTPException(422, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(422, "method_action_invalid") from exc
    outcome = receipt(state, cmd, key, digest, result)
    persist(session, task, state)
    return {"receipt": outcome, "task": render(chat, task, topic, role)}


@router.get("/task/{chat_id}/notebook/{run_id}")
def notebook(chat_id: int, run_id: str, session: SessionDep, user: CurrentUser, assistant: CurrentAssistant):
    direct(assistant)
    chat, task, topic, _, role = load_task(session, user, chat_id)
    visible = render(chat, task, topic, role)
    if topic.kind != "files" or run_id not in visible["runs"]:
        raise not_found()
    run = task.state["runs"][run_id]
    files = selected_files(task.state, run["definition"]["inputs"],
                           task.state["handoff"]["files"] if role == "analyst" else None)
    source = [
        "# Recalculate from fixed input references in a new Jupyter kernel.\n",
        "import csv, json\n",
        "from pathlib import Path\n",
        "from analysis_calc import calculate, parse_csv\n",
        'definition = json.loads(Path("definition.json").read_text(encoding="utf-8"))\n',
        'roles = definition["inputs"]\n',
        'inputs = {role: parse_csv(Path(role + ".csv").read_bytes()) for role in roles}\n',
        'with Path("sales.csv").open(encoding="utf-8", newline="") as file:\n',
        '    sales = [(r["store"], r["sku"], r["net"]) for r in csv.DictReader(file)]\n',
        'result = calculate(sales, definition["store"], inputs["roster"], inputs["costs"],\n',
        '                   definition["fields"], inputs.get("mapping"), definition["choices"], definition["currency"])\n',
        'result\n',
    ]
    document = {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    }, "cells": [{"id": "input-review", "cell_type": "code", "execution_count": None,
                  "metadata": {}, "outputs": [], "source": source}]}
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("analysis.ipynb", json.dumps(document, ensure_ascii=False))
        archive.writestr("analysis_calc.py", Path(cbi_multi_files.__file__).read_bytes())
        archive.writestr("definition.json", json.dumps(run["definition"], ensure_ascii=False, indent=2))
        archive.writestr("run.json", json.dumps(visible["runs"][run_id], ensure_ascii=False, indent=2))
        archive.writestr("sales.csv", "store,sku,net\n" + "\n".join(
            ",".join('"' + value.replace('"', '""') + '"' for value in row) for row in run["snapshot"]["rows"]
        ) + "\n")
        for name, file in files.items():
            archive.writestr(f"{name}.csv", b64decode(file["content"]))
    return Response(stream.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="analysis-notebook.zip"'})
