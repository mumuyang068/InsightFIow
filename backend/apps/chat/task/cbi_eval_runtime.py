"""Run confirmed evaluation cases on the ordinary, permission-checked analysis path."""

import asyncio
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic

from fastapi import HTTPException
from httpx import AsyncClient
from langchain_core.language_models import BaseChatModel
from sqlmodel import Session, select

from apps.ai_model.model_factory import LLMConfig, LLMFactory, get_default_config
from apps.chat.api import cbi_theme as theme_api
from apps.chat.curd import cbi_theme as themes
from apps.chat.curd.chat import create_chat
from apps.chat.models.cbi_theme import Version
from apps.chat.models.chat_model import (
    ChatLog,
    ChatQuestionBase,
    ChatRecord,
    CreateChat,
)
from apps.chat.task import cbi_analysis
from apps.chat.task.cbi_contract import is_managed_topic, load_effective_topic
from apps.db.db import get_session
from apps.system.models.system_model import AiModelDetail, UserWsModel
from apps.system.models.user import UserModel
from apps.system.schemas.system_schema import UserInfoDTO
from common.core.db import engine
from common.utils.utils import SQLBotLogUtil


@dataclass(frozen=True)
class EvaluationBinding:
    version: str
    model: LLMConfig
    llm: BaseChatModel
    before_model: Callable[[], None]


_scheduler = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cbi-evaluation")
_scheduling_lock = Lock()
_scheduled: set[str] = set()
_fixture: Callable[[str], None] | None = None
_fixture_modes: tuple[str, ...] = ("clean",)

# Source files that define the managed question, its authorized execution, and the
# persisted evaluation. The schema and ordered relative paths are part of the ID.
EXECUTION_FILES = (
    "apps/ai_model/model_factory.py",
    "apps/ai_model/openai/llm.py",
    "apps/chat/api/chat.py",
    "apps/chat/api/cbi_eval.py",
    "apps/chat/api/cbi_theme.py",
    "apps/chat/curd/cbi_eval.py",
    "apps/chat/curd/cbi_theme.py",
    "apps/chat/curd/chat.py",
    "apps/chat/models/cbi_eval.py",
    "apps/chat/models/cbi_theme.py",
    "apps/chat/models/chat_model.py",
    "apps/chat/task/cbi_analysis.py",
    "apps/chat/task/cbi_contract.py",
    "apps/chat/task/cbi_eval_runtime.py",
    "apps/chat/task/cbi_investigation.py",
    "apps/chat/task/cbi_outputs.py",
    "apps/chat/task/cbi_quality.py",
    "apps/chat/task/cbi_quality_runtime.py",
    "apps/chat/task/cbi_semantic.py",
    "apps/chat/task/cbi_theme_validation.py",
    "apps/datasource/api/datasource.py",
    "apps/datasource/crud/datasource.py",
    "apps/datasource/models/datasource.py",
    "apps/db/db.py",
)


def configure_local_fixtures(modes: tuple[str, ...], handler: Callable[[str], None]) -> None:
    """Isolated launchers may register fixed recipes. HTTP input cannot register one."""
    global _fixture, _fixture_modes
    if _fixture is not None or "clean" not in modes:
        raise RuntimeError("evaluation_fixture_binding_invalid")
    _fixture = handler
    _fixture_modes = modes


def _digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _source_code(*names: str) -> str:
    digest = sha256()
    directory = Path(__file__).parent
    for name in names:
        digest.update((directory / name).read_bytes())
    return digest.hexdigest()


def execution_code_version(root: Path = Path(__file__).resolve().parents[3]) -> str:
    manifest = {name: sha256((root / name).read_bytes()).hexdigest() for name in EXECUTION_FILES}
    return f"execution-manifest-v1:{_digest(manifest)}"


def _current_execution_versions() -> dict[str, str] | None:
    try:
        return {"code_version": execution_code_version(), "evaluator_version": _source_code("cbi_eval_score.py")}
    except OSError:
        return None


_LOADED_EXECUTION_VERSIONS = _current_execution_versions()


def execution_versions() -> dict[str, str]:
    current = _current_execution_versions()
    # Changed files require an application restart. Otherwise old loaded Python
    # could be attributed to the new on-disk manifest.
    if current is None or current != _LOADED_EXECUTION_VERSIONS:
        raise HTTPException(409, "evaluation_code_changed")
    return current


def require_execution_versions(context: dict) -> None:
    if any(context.get(key) != value for key, value in execution_versions().items()):
        raise HTTPException(409, "evaluation_code_changed")


def execution_turns(case: dict) -> list[tuple[str, int | None]]:
    turns = []
    for index, turn in enumerate(case["turns"]):
        parent = turn.get("parent")
        if parent is not None and (type(parent) is not int or not 0 <= parent < index):
            raise ValueError("evaluation_parent_invalid")
        turns.append((turn["question"], parent))
    return turns


def counted_model(model: LLMConfig) -> LLMConfig:
    if model.model_type != "openai":
        raise HTTPException(409, "evaluation_model_retry_control_unavailable")
    params = {**model.additional_params, "max_retries": 0}
    if "timeout" not in params and "request_timeout" not in params:
        params["timeout"] = 75
    return model.model_copy(update={"additional_params": params})


@asynccontextmanager
async def managed_llm(model: LLMConfig):
    # Each evaluation task opens and closes its pool on the worker's live loop.
    # A cached async client may still have connections belonging to a previous loop.
    async with AsyncClient() as client:
        isolated = model.model_copy(update={"additional_params": {
            **model.additional_params, "http_async_client": client,
        }})
        yield LLMFactory.create_scoped_llm(isolated).llm


def bound_identity(session: Session, workspace: int) -> UserInfoDTO:
    account = os.getenv("SQLBOT_CBI_EVAL_ACCOUNT")
    if not account or account == "admin":
        raise HTTPException(409, "evaluation_identity_unavailable")
    pair = session.execute(
        select(UserModel, UserWsModel.weight)
        .join(UserWsModel, UserWsModel.uid == UserModel.id)
        .where(UserModel.account == account, UserModel.oid == workspace, UserModel.status == 1,
               UserWsModel.oid == workspace, UserWsModel.weight == 0, UserModel.id != 1)
    ).one_or_none()
    if pair is None:
        raise HTTPException(409, "evaluation_identity_unavailable")
    user, weight = pair
    return UserInfoDTO.model_validate({**user.model_dump(), "weight": weight, "isAdmin": False})


def permission_fingerprint(source, user_id: int, fields: dict) -> str:
    return _digest({"workspace": source.oid, "source": source.id, "kind": source.type,
                    "actor": user_id, "fields": {view: sorted(keys) for view, keys in fields.items()}})


def _access(session, manager, runner, datasource_id: int, version: str):
    from apps.chat.task.cbi_quality import quality_required_fields

    source, _, baseline, view, _, _ = theme_api._bind(session, manager, None, datasource_id)
    topic = load_effective_topic(session, datasource_id, workspace=manager.oid, version=version)
    if not topic or topic.by_id(topic.default_definition).view != view:
        raise HTTPException(409, "evaluation_configuration_unavailable")
    required = topic.semantic.fields | quality_required_fields(topic.semantic)
    allowed = cbi_analysis._require_assets(session, runner, source, {view: required})
    # Feed the digest the complete effective projection so grants and removals are detectable.
    return source, topic, baseline, allowed


def _version_option(row):
    label = row.version
    if row.version.startswith("eval-"):
        label = f"{row.content['semantic']['title']} · {row.version}" if row.content["semantic"].get("title") else row.version
    return {"id": row.version, "label": label}


def _model_descriptor(session, model_id=None):
    row = (session.get(AiModelDetail, int(model_id)) if model_id is not None else
           session.scalars(select(AiModelDetail).where(AiModelDetail.default_model.is_(True))).first())
    if row is None or row.protocol != 1:
        raise HTTPException(409, "evaluation_model_unavailable")
    return {"id": str(row.id), "name": row.base_model, "retry_limit": 0,
            "configuration": _digest([row.base_model, row.api_domain, row.config, row.protocol]),
            "prompt": _source_code("cbi_analysis.py", "cbi_semantic.py")}


def _data_key() -> str:
    return os.getenv("SQLBOT_CBI_EVAL_DATA_KEY") or "registered-source"


def options(session, user, theme_id):
    try:
        runner = bound_identity(session, user.oid)
    except HTTPException:
        runner = None
    sources = []
    for item in sorted(themes.list_themes(session, user.oid), key=lambda item: (item.id != theme_id, item.id)):
        if not is_managed_topic(session, item.id):
            continue
        try:
            topic = load_effective_topic(session, item.id, workspace=user.oid)
            source, *_ = theme_api._bind(session, user, None, item.id)
            if runner is not None:
                _access(session, user, runner, item.id, item.active_version)
        except HTTPException:
            continue
        sources.append({"id": str(item.id), "name": topic.label or source.name, "active_version": item.active_version,
                        "versions": [_version_option(row) for row in
                                     themes.list_versions(session, item.id, include_evaluation=True)
                                     if row.version != item.initial_version]})
    result = {"identity": {"id": str(runner.id), "name": runner.name} if runner else None,
              "datasources": sources, "data_version": _data_key(), "budget_limit": 48,
              "fixtures": list(_fixture_modes), "can_manage": True, "can_run": runner is not None}
    try:
        result["model"] = _model_descriptor(session)
    except HTTPException:
        result["can_run"] = False
    return result


def prepare(session, user, theme_id, suite_cases, versions):
    build = execution_versions()
    runner = bound_identity(session, user.oid)
    needed = {str(case["datasource_id"]) for case in suite_cases}
    if not needed or needed != set(versions):
        raise HTTPException(422, "evaluation_configuration_coverage_mismatch")
    if any(case.get("fixture", "clean") not in _fixture_modes for case in suite_cases):
        raise HTTPException(422, "evaluation_fixture_unavailable")
    for case in suite_cases:
        execution_turns(case)
    permissions = {}
    configurations = {}
    source_versions = {}
    for key, version in sorted(versions.items()):
        load_effective_topic(session, int(key), workspace=user.oid)
        source, topic, baseline, allowed = _access(session, user, runner, int(key), version)
        permissions[key] = permission_fingerprint(source, runner.id, allowed)
        source_versions[key] = baseline.source_version
        configurations[key] = {"id": topic.version, "digest": topic.config_digest,
                               "quality_rules": [rule.model_dump(mode="json")
                                                 for rule in topic.semantic.quality.rules]
                               if topic.semantic.quality else []}
    return {
        "workspace_id": str(user.oid), "theme_id": str(theme_id), "initiator_id": str(user.id),
        "identity": {"id": str(runner.id), "name": runner.name, "account": runner.account},
        "versions": dict(versions), "configurations": configurations, "permissions": permissions,
        "budget_scope": os.getenv("SQLBOT_CBI_EVAL_BUDGET_SCOPE") or None,
        "data_version": _data_key(), "source_versions": source_versions,
        "model": _model_descriptor(session), **build,
    }


def check_read(session, user, theme_id, context):
    if str(user.oid) != str(context["workspace_id"]) or str(theme_id) != context.get("theme_id", str(theme_id)):
        raise HTTPException(404, "evaluation_batch_not_found")
    runner = bound_identity(session, user.oid)
    if str(runner.id) != context["identity"]["id"]:
        raise HTTPException(403, "evaluation_identity_changed")
    for key, version in context["versions"].items():
        source, _, _, allowed = _access(session, user, runner, int(key), version)
        if permission_fingerprint(source, runner.id, allowed) != context["permissions"][key]:
            raise HTTPException(403, "evaluation_permissions_changed")
    return runner


def freeze_configuration(session, user, theme_id, draft_id, revision, validation_id):
    source, _, baseline, view, allowed, asset = theme_api._bind(session, user, None, theme_id)
    theme_api._call(themes._lock_theme, session, theme_id)
    draft = themes.get_draft(session, theme_id, draft_id)
    validation = themes.get_preview(session, theme_id, validation_id)
    if (not draft or draft.actor_id != user.id or draft.revision != revision
            or not validation or validation.actor_id != user.id
            or (validation.draft_id, validation.revision) != (draft_id, revision)
            or validation.receipt.get("kind") != "static"):
        raise HTTPException(409, "evaluation_draft_validation_invalid")
    version = "eval-" + _digest([draft_id, revision, validation_id])[:32]
    existing = session.get(Version, (theme_id, version))
    if existing:
        return _version_option(existing)
    with get_session(source) as read:
        theme_api._start_read(read)
        types = theme_api._columns(view, allowed, read)
    dependency = theme_api._version_dependency(source, baseline, view, asset, types)
    if dependency != validation.dependency:
        raise HTTPException(409, "evaluation_draft_validation_invalid")
    theme_api._validate(draft.content["semantic"], baseline, types)
    content = {**deepcopy(draft.content), "version": version}
    row = Version(theme_id=theme_id, version=version, content=content, content_digest=themes._digest(content),
                  dependency=dependency, created_by=user.id, created_at=datetime.now(UTC))
    session.add(row)
    session.flush()
    return _version_option(row)


def _initiator(session, context):
    row = session.get(UserModel, int(context["initiator_id"]))
    if not row or row.status != 1 or str(row.oid) != context["workspace_id"]:
        raise HTTPException(403, "evaluation_initiator_unavailable")
    user = UserInfoDTO.model_validate({**row.model_dump(), "isAdmin": row.id == 1 and row.account == "admin"})
    theme_api._admin(session, user, None)
    return user


def _case_binding(session, claim):
    context = claim["context"]
    require_execution_versions(context)
    user = _initiator(session, context)
    for key in context["versions"]:
        load_effective_topic(session, int(key), workspace=user.oid)
    runner = check_read(session, user, claim["theme_id"], context)
    if _model_descriptor(session, context["model"]["id"]) != context["model"] or _data_key() != context["data_version"]:
        raise HTTPException(409, "evaluation_context_changed")
    return runner


def _record_rows(record):
    if record.data is None:
        return None
    stored = json.loads(record.data)
    return stored.get("query_data", stored.get("data"))


def _turn_receipt(session, runner, row, events):
    cbi_analysis.check_saved_access(session, runner, row)
    state = row.analysis_state
    queries = []
    refs = {str(step["record_id"]): step for step in state.get("steps", ()) if step.get("record_id")}
    if row.sql and str(row.id) not in refs:
        refs[str(row.id)] = {"purpose": "result", "status": "failed" if row.error else "recorded"}
    for report_id in (state.get("quality") or {}).get("report_ids", ()):
        refs.setdefault(str(report_id), {"purpose": "quality_check"})
    for record_id, step in refs.items():
        linked = session.get(ChatRecord, int(record_id))
        if (linked and linked.create_by == runner.id and linked.chat_id == row.chat_id
                and linked.datasource == row.datasource and (linked.sql or (linked.analysis_state or {}).get("internal"))):
            queries.append({"record_id": record_id, "sql": linked.sql, "rows": _record_rows(linked),
                            "state": linked.analysis_state if (linked.analysis_state or {}).get("internal") else None,
                            "purpose": step.get("purpose"), "status": step.get("status")})
    logs = session.scalars(select(ChatLog).where(
        ChatLog.pid == row.id, ChatLog.local_operation.is_(False)
    ).order_by(ChatLog.id)).all()
    models = [{"kind": item.operate.value, "message": item.messages, "usage": item.token_usage or None,
               "error": item.error, "started_at": str(item.start_time), "completed_at": str(item.finish_time)}
              for item in logs]
    return {"record_id": str(row.id), "chat_id": str(row.chat_id), "state": state,
            "raw_rows": _record_rows(row), "queries": queries, "model": models, "events": events}


async def _run_case(claim, observation):
    from apps.chat.curd import cbi_eval

    context = claim["context"]
    case = claim["case"]
    datasource = int(case["datasource_id"])
    version = context["versions"][str(datasource)]
    fixture = case.get("fixture", "clean")
    turns = execution_turns(case)

    def reserve():
        with Session(engine) as session:
            cbi_eval.record_model_call(session, claim["batch_id"], claim["attempt_id"], 48)
        observation["usage"]["model_calls"] += 1

    with Session(engine) as session:
        runner = _case_binding(session, claim)
        chat = create_chat(session, runner, CreateChat(datasource=datasource))
    model = counted_model(await get_default_config(int(context["model"]["id"])))
    if str(model.model_id) != context["model"]["id"]:
        raise HTTPException(409, "evaluation_model_changed")
    started = monotonic()
    if _fixture is not None:
        await asyncio.to_thread(_fixture, fixture)
    try:
        async with managed_llm(model) as llm:
            binding = EvaluationBinding(version, model, llm, reserve)
            for index, (text, parent) in enumerate(turns):
                receipt = await _run_turn(claim, chat.id, binding, observation["turns"], index, text, parent)
                observation["turns"].append(receipt)
                _check_provider(receipt)
    finally:
        observation["usage"]["duration_ms"] = round((monotonic() - started) * 1000)
        calls = [entry for turn in observation["turns"] for entry in turn["model"]]
        known = [entry["usage"].get("total_tokens") for entry in calls if isinstance(entry["usage"], dict)]
        observation["usage"]["tokens"] = sum(value for value in known if isinstance(value, int))
        observation["usage"]["unknown_model_usage"] = max(0, observation["usage"]["model_calls"] - sum(
            isinstance(value, int) for value in known))
        observation["usage"]["cost"] = None
        if _fixture is not None:
            await asyncio.to_thread(_fixture, "clean")


async def _run_turn(claim, chat_id, binding, receipts, index, text, parent):
    previous = None if parent is None else int(receipts[parent]["record_id"])
    question = ChatQuestionBase(chat_id=chat_id, question=text, parent_record_id=previous,
                                client_request_id=_digest([claim["batch_id"], claim["attempt_id"], index]))
    with Session(engine) as session:
        runner = _case_binding(session, claim)
        response = await cbi_analysis.question(session, runner, question, None, None, evaluation=binding)
    if response is None:
        raise HTTPException(409, "evaluation_managed_entry_unavailable")
    events = [json.loads(raw.decode().strip().removeprefix("data:"))
              async for raw in response.body_iterator]
    result = next((item for item in events if item["type"] == "analysis-state"), None)
    if result is None:
        raise ValueError("evaluation_response_missing")
    with Session(engine) as session:
        runner = _case_binding(session, claim)
        row = session.get(ChatRecord, int(result["record_id"]))
        return _turn_receipt(session, runner, row, events)


def _check_provider(receipt) -> None:
    errors = {model["message"].get("provider_error") for model in receipt["model"]
              if isinstance(model["message"], dict) and model["message"].get("provider_error")}
    if "evaluation_budget_exhausted" in errors:
        raise HTTPException(409, "evaluation_budget_exhausted")
    if errors & {"AuthenticationError", "PermissionDeniedError"}:
        raise HTTPException(409, "evaluation_model_authentication_failed")
    if errors:
        raise RuntimeError("evaluation_model_request_failed")


async def _drive(batch_id: str) -> None:
    from apps.chat.curd import cbi_eval
    from apps.chat.task.cbi_eval_score import score

    failures = 0
    while True:
        with Session(engine) as session:
            claim = cbi_eval.claim_next(session, batch_id)
        if claim is None:
            return
        observation = {"turns": [], "usage": {"model_calls": 0}}
        halt = None
        try:
            await _run_case(claim, observation)
        except Exception as error:
            failures += 1
            reason = error.detail if isinstance(error, HTTPException) else str(error)
            if not isinstance(reason, str) or not reason.startswith("evaluation_"):
                reason = "evaluation_execution_error"
            verdict = {"status": "system_error", "issues": [{"dimension": "execution", "detail": reason}]}
            if failures >= 2 or isinstance(error, HTTPException):
                halt = reason
        else:
            failures = 0
            verdict = None
        with Session(engine) as session:
            cbi_eval.save_observation(session, batch_id, claim["attempt_id"], observation)
        if verdict is None:
            try:
                verdict = score(claim["case"]["expected"], observation)
            except Exception:
                verdict = {"status": "evaluator_error",
                           "issues": [{"dimension": "evaluation", "detail": "evaluation_scorer_error"}]}
        status = "indeterminate" if verdict["status"] == "unjudgeable" else verdict["status"]
        with Session(engine) as session:
            cbi_eval.finish_attempt(session, batch_id, claim["attempt_id"], status=status,
                                    observation=observation, judgement=verdict, halt_reason=halt)
        if halt:
            return


def _run_and_release(batch_id: str) -> None:
    from apps.chat.curd import cbi_eval

    try:
        asyncio.run(_drive(batch_id))
    except Exception as error:
        SQLBotLogUtil.warning(f"CBI evaluation worker stopped: {type(error).__name__}")
        with Session(engine) as session:
            cbi_eval.halt_batch(session, batch_id, "evaluation_worker_error")
    finally:
        with _scheduling_lock:
            _scheduled.discard(batch_id)
        with Session(engine) as session:
            batch = cbi_eval.get_batch(session, batch_id)
            if batch and batch.status == "pending":
                schedule(batch_id)


def schedule(batch_id: str) -> None:
    with _scheduling_lock:
        if batch_id not in _scheduled:
            _scheduled.add(batch_id)
            _scheduler.submit(_run_and_release, batch_id)
