from copy import deepcopy
from hashlib import sha256
from typing import Literal
from uuid import uuid4

import orjson
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from apps.chat.task.cbi_analysis import _record
from apps.chat.task.cbi_contract import timestamp
from apps.chat.task.cbi_methods import workflow as methods
from apps.chat.task.cbi_multi_execution import execute
from apps.chat.task.cbi_multi_files import FileIssue, fee_inputs, serialize_amount
from apps.chat.task.cbi_multi_store import collaborator, decode_file, not_found, selected_files, source_access
from apps.system.schemas.system_schema import UserInfoDTO

Role = Literal["roster", "costs", "mapping"]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["plan", "apply_file", "run", "confirm", "request", "accept", "return", "apply", "adopt"]
    revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    inputs: dict[Role, str] | None = None
    file_id: str | None = Field(default=None, max_length=80)
    question: str | None = Field(default=None, max_length=800)
    base_run: str | None = Field(default=None, max_length=80)
    receiver: int | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=800, pattern=r"\S")
    share: bool = False
    external: str | None = Field(default=None, max_length=250)
    sku: str | None = Field(default=None, max_length=250)
    roster_field: str | None = Field(default=None, max_length=80)


def rejection(code):
    return HTTPException(409, code)


def check_command(state, user, cmd):
    key = f"{user.id}:{cmd.request_id}"
    digest = sha256(cmd.model_dump_json().encode()).hexdigest()
    prior = state["actions"].get(key)
    if prior:
        if prior["digest"] != digest:
            raise rejection("request_already_used")
        return key, digest, prior["receipt"]
    if cmd.revision != state["revision"]:
        raise rejection("revision_changed")
    if len(state["actions"]) >= 200:
        raise rejection("action_limit")
    return key, digest, None


def receipt(state, cmd, key, digest, outcome):
    state["revision"] += 1
    result = {"action": cmd.action, "revision": state["revision"], **outcome}
    state["actions"][key] = {"digest": digest, "receipt": result}
    return result


def set_definition(state, definition, files):
    definition["inputs"] = {role: file["id"] for role, file in files.items()}
    definition["id"] = uuid4().hex
    definition["revision"] = state["revision"] + 1
    state["definition"] = definition


def file_delta(state, definition, cmd):
    if not definition:
        raise rejection("plan_required")
    file = state["files"].get(cmd.file_id)
    if not file or file["purpose"] != "costs":
        raise not_found()
    original = definition["inputs"]["costs"]
    if file["replaces"] != original:
        raise rejection("replacement_base_changed")
    candidate = selected_files(state, {**definition["inputs"], "costs": file["id"]})
    fields = {**definition["fields"], **file["fields"]}
    old = fee_inputs(decode_file(state["files"][original]), definition["fields"],
                     definition["store"], definition["currency"])
    new = fee_inputs(decode_file(file), fields, definition["store"], definition["currency"])
    changes = [
        {"fee_id": key, "old": [old[key][0], serialize_amount(old[key][1])] if key in old else None,
         "new": [new[key][0], serialize_amount(new[key][1])] if key in new else None}
        for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)
    ]
    return candidate, fields, changes


def save_record(session, user, chat, state, run):
    result = run["result"]
    row = _record(session, user, chat, state["question"], state={
        "multi": {"run_id": run["id"], "task_id": str(chat.id), "window": run["definition"]["window"],
                  "revision": run["definition"]["id"], "currency": result["currency"],
                  "sales": result.get("sales"), "recorded_fee": result.get("recorded_fee")},
    })
    row.analysis_state = {"multi": {**row.analysis_state["multi"], "record_id": str(row.id)}}
    if result.get("rows"):
        labels = {"sku": "商品", "net": "净销售额（元）", "fee": "已记录费用（元）"}
        chart = {"type": "table", "title": "名单销售与已记录费用",
                 "columns": [{"name": label, "value": field} for field, label in labels.items()]}
        data = {"datasource": chat.datasource, "fields": list(labels), "data": result["rows"]}
        # This SQL is shown with bound parameters in the run evidence, not used as live chart SQL.
        row.chart = orjson.dumps(chart).decode()
        row.data = orjson.dumps(data).decode()
    row.finish = True
    session.add(row)
    session.flush()
    return str(row.id)


def run_definition(session, user, chat, state, topic, datasource, definition, *, allowed=None, publish=True):
    if len(state["runs"]) >= 35:
        raise rejection("run_limit")
    snapshot = next(
        (run["snapshot"] for run in state["runs"].values()
         if run["definition"]["store"] == definition["store"]
         and run["definition"]["data_version"] == definition["data_version"]
         and run["definition"]["source_sha256"] == definition["source_sha256"]),
        None,
    ) if publish else None
    start = timestamp()
    result, snapshot, evidence = execute(state, topic, datasource, definition, snapshot, allowed)
    run = {"id": uuid4().hex, "definition": deepcopy(definition), "actor": str(user.id),
           "started": start, "finished": timestamp(), "result": result,
           "snapshot": snapshot, "evidence": evidence}
    if publish:
        run["record_id"] = save_record(session, user, chat, state, run)
        state["work"] = run["id"]
        if state["adopted"] is None and result["state"] != "awaiting_confirmation":
            state["adopted"] = run["id"]
    run["summary"] = (
        {"kind": "awaiting_mapping", "codes": sorted(result["decisions"])}
        if result["state"] == "awaiting_confirmation" else
        {"kind": "observed_amounts", "sales": result["sales"], "recorded_fee": result["recorded_fee"],
         "currency": result["currency"], "missing_fee": result["fee_coverage"][1] - result["fee_coverage"][0]}
    )
    state["runs"][run["id"]] = run
    return run


def publish_adoption(session, user, chat, state, candidate, note):
    adopted = {
        **candidate, "id": uuid4().hex, "accepted_from": candidate["id"],
        "actor": str(user.id), "adopted_at": timestamp(), "note": note,
    }
    saver = methods.save_record if "plan" in adopted["definition"] else save_record
    adopted["record_id"] = saver(session, user, chat, state, adopted)
    state["runs"][adopted["id"]] = adopted
    state.update(adopted=adopted["id"], work=adopted["id"], definition=deepcopy(adopted["definition"]))
    return adopted


def choose(run, definition, cmd):
    options = run["result"]["decisions"].get(cmd.external, [])
    if cmd.sku not in options or not cmd.reason:
        raise rejection("mapping_confirmation_required")
    definition["choices"][cmd.external] = {"sku": cmd.sku, "reason": cmd.reason}


def ensure_handoff_current(state, handoff):
    if handoff["definition_id"] != (state["definition"] or {}).get("id") or (
        state["work"] != handoff["base_run"] and state["work"] != handoff.get("applied")
    ):
        raise rejection("handoff_base_changed")


def request_handoff(session, user, chat, state, cmd):
    base = state["runs"].get(cmd.base_run)
    if not base or cmd.base_run != state["work"] or base["definition"]["id"] != (state["definition"] or {}).get("id"):
        raise rejection("handoff_base_changed")
    if not (cmd.share and cmd.reason and cmd.receiver):
        raise rejection("select_receiver_and_confirm_share")
    if state["handoff"] and state["handoff"]["status"] not in {"adopted", "requested"}:
        candidate = state["handoff"]
        try:
            ensure_handoff_current(state, candidate)
        except HTTPException:
            pass
        else:
            raise rejection("handoff_already_active")
    target = collaborator(session, user, chat, cmd.receiver)
    # Check the intended receiver at share time and check again under their actual session.
    receiver = UserInfoDTO.model_construct(id=target.id, oid=chat.oid, isAdmin=False)
    source_access(session, receiver, chat)
    state["handoff"] = {
        "id": uuid4().hex, "source": str(user.id), "receiver": str(target.id),
        "receiver_name": target.name, "question": cmd.reason, "base_run": base["id"],
        "definition_id": base["definition"]["id"], "files": list(base["definition"]["inputs"].values()),
        "status": "requested", "requested_at": timestamp(),
    }
    return {"handoff_id": state["handoff"]["id"]}


def perform(session, user, chat, state, topic, datasource, role, cmd):
    analyst_action = cmd.action in {"accept", "return"}
    if (role == "analyst") != analyst_action:
        raise not_found()
    handoff = state["handoff"]
    definition = deepcopy(state["definition"])
    if topic.kind == "methods" and cmd.action in {"apply_file", "run", "confirm"}:
        raise rejection("use_method_contract")
    if cmd.action == "apply_file":
        files, fields, changes = file_delta(state, definition, cmd)
        definition["fields"] = fields
        set_definition(state, definition, files)
        return {"definition_id": definition["id"], "changes": changes}
    if cmd.action == "run":
        if not definition:
            raise rejection("plan_required")
        run = run_definition(session, user, chat, state, topic, datasource, definition)
        return {"run_id": run["id"], "record_id": run["record_id"]}
    if cmd.action == "confirm":
        run = state["runs"].get(state["work"])
        if not run or run["definition"]["id"] != (definition or {}).get("id"):
            raise rejection("confirm_current_run")
        choose(run, definition, cmd)
        set_definition(state, definition, selected_files(state, definition["inputs"]))
        run = run_definition(session, user, chat, state, topic, datasource, definition)
        return {"run_id": run["id"], "record_id": run["record_id"]}
    if cmd.action == "request":
        return request_handoff(session, user, chat, state, cmd)
    if not handoff:
        raise not_found()
    ensure_handoff_current(state, handoff)
    base = state["runs"][handoff["base_run"]]
    if cmd.action == "accept" and handoff["status"] == "requested":
        handoff.update(status="accepted", accepted_at=timestamp())
    elif cmd.action == "return" and handoff["status"] == "accepted" and topic.kind == "methods":
        if len(state["runs"]) > 31:
            raise rejection("run_limit")
        run = methods.correct(session, user, chat, state, topic, datasource, base, cmd.reason)
        handoff.update(status="returned", returned_at=timestamp(), candidate=run["id"], change=cmd.reason)
    elif cmd.action == "return" and handoff["status"] == "accepted":
        definition = deepcopy(base["definition"])
        if cmd.roster_field:
            file = state["files"][definition["inputs"]["roster"]]
            if cmd.roster_field not in file["summary"]["columns"]:
                raise rejection("invalid_roster_field")
            definition["fields"]["roster_sku"] = cmd.roster_field
        if base["result"]["decisions"]:
            choose(base, definition, cmd)
        if not cmd.reason or (definition["fields"], definition["choices"]) == (
            base["definition"]["fields"], base["definition"]["choices"],
        ):
            raise rejection("professional_change_required")
        definition.update(id=uuid4().hex, revision=state["revision"] + 1, parent=definition["id"])
        run = run_definition(session, user, chat, state, topic, datasource, definition,
                             allowed=handoff["files"], publish=False)
        if run["result"]["state"] == "awaiting_confirmation":
            raise rejection("professional_result_not_ready")
        handoff.update(status="returned", returned_at=timestamp(), candidate=run["id"], change=cmd.reason)
    elif cmd.action == "apply" and handoff["status"] == "returned":
        handoff.update(status="applied", applied=handoff["candidate"], applied_at=timestamp())
        state["work"] = handoff["candidate"]
    elif cmd.action == "adopt" and handoff["status"] == "applied":
        if not cmd.reason:
            raise rejection("adoption_note_required")
        if len(state["runs"]) >= 35:
            raise rejection("run_limit")
        adopted = publish_adoption(session, user, chat, state, state["runs"][handoff["candidate"]], cmd.reason)
        handoff.update(status="adopted", adopted=adopted["id"], adopted_at=timestamp())
        if topic.kind == "methods":
            state.setdefault("quality", {})[base["id"]] = {
                "status": "corrected", "replacement": adopted["id"], "at": handoff["adopted_at"],
            }
    else:
        raise rejection("handoff_state_changed")
    return {"handoff_id": handoff["id"], "status": handoff["status"], "candidate": handoff.get("candidate"),
            "adopted": handoff.get("adopted")}
