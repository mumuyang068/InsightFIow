"""Theme-scoped management for fixed suites and reports."""

import json
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Response
from fastapi.encoders import jsonable_encoder
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from apps.chat.api.cbi_theme import Id, _bind
from apps.chat.curd import cbi_eval as store
from apps.chat.models.chat_model import Chat, ChatRecord
from apps.chat.task import cbi_eval_runtime as runtime
from apps.chat.task.cbi_analysis import check_saved_access
from common.core.deps import CurrentAssistant, CurrentUser, SessionDep

router = APIRouter(
    prefix="/cbi/themes/{theme_id}/evaluation", tags=["Theme evaluation"]
)

MAX_ID = 2**63 - 1


def _source_id(value: str) -> str:
    if int(value) > MAX_ID:
        raise ValueError("evaluation_invalid_source_id")
    return value


ThemeId = Annotated[int, Path(ge=1, le=MAX_ID)]
SourceId = Annotated[
    str, Field(pattern=r"^[1-9][0-9]{0,18}$"), AfterValidator(_source_id)
]
Question = Annotated[str, Field(min_length=1, max_length=3000)]
CaseIds = Annotated[list[Id], Field(min_length=1, max_length=100)]
Document = dict[str, JsonValue]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Turn(Command):
    question: Question
    parent: int | None = Field(default=None, ge=0, strict=True)


class Case(Command):
    id: Id
    title: str | None = Field(default=None, max_length=160)
    datasource_id: SourceId
    category: str = Field(min_length=1, max_length=100)
    turns: list[Turn] = Field(min_length=1, max_length=10)
    fixture: Id = "clean"
    expected: Document | None = None
    protects: list[Id] = Field(default_factory=list, max_length=100)
    origin: Document = Field(default_factory=dict)


class SuiteIn(Command):
    request_id: Id
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    cases: list[Case] = Field(min_length=1, max_length=100)
    previous_id: Id | None = None
    confirm: bool = False

    @model_validator(mode="after")
    def validate_members(self):
        keys = {case.id for case in self.cases}
        if len(keys) != len(self.cases):
            raise ValueError("evaluation_duplicate_case")
        for case in self.cases:
            if (
                len(case.protects) != len(set(case.protects))
                or case.id in case.protects
                or set(case.protects) - keys
            ):
                raise ValueError("evaluation_invalid_protections")
            for number, turn in enumerate(case.turns):
                if (number == 0 and turn.parent is not None) or (
                    number > 0 and (turn.parent is None or turn.parent >= number)
                ):
                    raise ValueError("evaluation_invalid_parent_turn")
            if self.confirm:
                expected = case.expected.get("turns") if case.expected else None
                if (
                    not isinstance(expected, list)
                    or len(expected) != len(case.turns)
                    or any(
                        not _valid_standard(spec, turn.parent)
                        for spec, turn in zip(expected, case.turns, strict=True)
                    )
                ):
                    raise ValueError("evaluation_invalid_expected_turn")
        if len(self.model_dump_json().encode()) > 1024 * 1024:
            raise ValueError("evaluation_document_too_large")
        return self


def _valid_standard(spec, parent):
    if (
        not isinstance(spec, dict)
        or not isinstance(spec.get("task_status"), str)
        or not spec["task_status"].strip()
        or (parent is None and "parent" in spec)
        or spec.get("parent") != parent
        or (parent is not None and type(spec.get("parent")) is not int)
    ):
        return False
    scope = spec.get("query_scope")
    return (
        isinstance(scope, dict)
        and {"business", "drill"} <= scope.keys() <= {"business", "drill", "quality"}
        and all(type(value) is int and value >= 0 for value in scope.values())
        and scope["drill"] <= scope["business"]
    )


class BatchIn(Command):
    request_id: Id
    suite_id: Id
    versions: dict[SourceId, Id] = Field(min_length=1, max_length=100)
    case_ids: CaseIds | None = None
    parent_batch_id: Id | None = None
    label: str = Field(min_length=1, max_length=160)
    budget: int = Field(
        default=store.MAX_BUDGET, ge=1, le=store.MAX_BUDGET, strict=True
    )
    declared_variable: Literal["theme_configuration", "none"] = "theme_configuration"


class RunDraftIn(Command):
    request_id: Id
    batch_id: Id | None = None
    case_id: Id | None = None
    record_id: SourceId | None = None

    @model_validator(mode="after")
    def validate_origin(self):
        if not (
            (self.batch_id and self.case_id and not self.record_id)
            or (self.record_id and not self.batch_id and not self.case_id)
        ):
            raise ValueError("evaluation_invalid_draft_origin")
        return self


class BatchCommand(Command):
    request_id: Id


class ResumeIn(BatchCommand):
    case_ids: CaseIds | None = None


class FreezeIn(Command):
    request_id: Id
    draft_id: Id
    revision: int = Field(ge=1)
    validation_id: Id


@contextmanager
def _errors():
    try:
        yield
    except store.StoreError as exc:
        raise HTTPException(exc.status, {"reason": exc.reason}) from exc


def _authorize_sources(session, user, assistant, theme_id, cases):
    for source in {int(case["datasource_id"]) for case in cases} - {theme_id}:
        _bind(session, user, assistant, source)


def _suite(session, user, assistant, theme_id, suite_id):
    row = store.get_suite(session, theme_id, user.oid, suite_id)
    _authorize_sources(session, user, assistant, theme_id, row.cases)
    return row


def _batch(session, user, assistant, theme_id, batch_id):
    from apps.chat.task.cbi_eval_runtime import check_read

    row = store.scoped_batch(session, theme_id, user.oid, batch_id)
    _suite(session, user, assistant, theme_id, row.suite_id)
    check_read(session, user, theme_id, row.context)
    return row


def _visible_items(items, authorize, serialize):
    visible = []
    for row in items:
        try:
            authorize(row)
        except HTTPException as error:
            if error.status_code not in {403, 404, 409}:
                raise
        else:
            visible.append(serialize(row))
    return visible


@router.get("", summary="List evaluation suites and batches")
def overview(
    theme_id: ThemeId,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    from apps.chat.task.cbi_eval_runtime import options

    _bind(session, user, assistant, theme_id)
    with _errors():
        suites = _visible_items(
            store.list_suites(session, theme_id, user.oid),
            lambda row: _authorize_sources(
                session, user, assistant, theme_id, row.cases
            ),
            store.public_suite,
        )
        batches = _visible_items(
            store.list_batches(session, theme_id, user.oid),
            lambda row: _batch(session, user, assistant, theme_id, row.id),
            lambda row: store.public_batch(session, row),
        )
        return {
            "suites": suites,
            "batches": batches,
            "options": options(session, user, theme_id),
        }


@router.get("/suites/{suite_id}", summary="Read an immutable evaluation suite")
def suite_detail(
    theme_id: ThemeId,
    suite_id: Id,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _bind(session, user, assistant, theme_id)
    with _errors():
        return store.public_suite(
            _suite(session, user, assistant, theme_id, suite_id), detail=True
        )


@router.post("/suites", summary="Save an evaluation suite revision")
def save_suite(
    theme_id: ThemeId,
    command: SuiteIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _bind(session, user, assistant, theme_id)
    cases = [case.model_dump(mode="json", exclude_none=True) for case in command.cases]
    _authorize_sources(session, user, assistant, theme_id, cases)
    with _errors():
        if command.previous_id:
            _suite(session, user, assistant, theme_id, command.previous_id)
        row = store.save_suite(
            session,
            theme_id,
            user.oid,
            user.id,
            command.request_id,
            title=command.title,
            description=command.description,
            cases=cases,
            previous_id=command.previous_id,
            confirm=command.confirm,
        )
        return store.public_suite(row, detail=True)


def _analysis_case(session, user, theme_id, record_id):
    case = {
        "id": f"run-{record_id}",
        "datasource_id": str(theme_id),
        "category": "analysis",
        "fixture": "clean",
        "expected": None,
        "protects": [],
    }
    records = []
    seen = set()
    first_chat_id = None
    current = int(record_id)
    while current:
        if current in seen or len(records) >= 10:
            raise HTTPException(422, {"reason": "evaluation_invalid_parent_turn"})
        seen.add(current)
        row = session.get(ChatRecord, current)
        chat = session.get(Chat, row.chat_id) if row else None
        if (
            not row
            or not chat
            or chat.oid != user.oid
            or chat.create_by != user.id
            or (chat.origin or 0) != 0
            or row.create_by != user.id
            or row.datasource != theme_id
            or chat.datasource != theme_id
            or (first_chat_id is not None and first_chat_id != chat.id)
            or not row.analysis_state
            or not row.question
            or not row.finish
        ):
            raise HTTPException(404, {"reason": "evaluation_analysis_unavailable"})
        check_saved_access(session, user, row)
        if not records:
            case["origin"] = {
                "kind": "analysis_run",
                "record_id": str(row.id),
                "chat_id": str(row.chat_id),
                "configuration": row.analysis_state.get("config_version"),
                "scope": row.analysis_state.get("scope"),
            }
        first_chat_id = chat.id
        records.append(row.question)
        request = row.analysis_state.get("request", {})
        parent = request.get("selected_parent") or request.get("parent_record_id")
        current = int(parent) if parent else 0
    case["turns"] = [
        {"question": question, **({"parent": index - 1} if index else {})}
        for index, question in enumerate(reversed(records))
    ]
    return case


@router.post("/draft-from-run", summary="Create an evaluation draft from a saved run")
def draft_from_run(
    theme_id: ThemeId,
    command: RunDraftIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _bind(session, user, assistant, theme_id)
    with _errors():
        if command.record_id:
            case = _analysis_case(session, user, theme_id, command.record_id)
        else:
            row = _batch(session, user, assistant, theme_id, command.batch_id)
            case = store.failure_case(session, row, command.case_id)
        draft = store.save_suite(
            session,
            theme_id,
            user.oid,
            user.id,
            command.request_id,
            title=case["id"],
            description="",
            cases=[case],
            previous_id=None,
            confirm=False,
        )
        return store.public_suite(draft, detail=True)


@router.post("/configurations", summary="Freeze a theme draft for evaluation")
def freeze_configuration(
    theme_id: ThemeId,
    command: FreezeIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    from apps.chat.task.cbi_eval_runtime import freeze_configuration as freeze

    _bind(session, user, assistant, theme_id)
    with _errors():
        return freeze(
            session,
            user,
            theme_id,
            command.draft_id,
            command.revision,
            command.validation_id,
        )


@router.post("/batches", summary="Start a fixed evaluation batch")
def start_batch(
    theme_id: ThemeId,
    command: BatchIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    from apps.chat.task import cbi_eval_runtime as runtime

    _bind(session, user, assistant, theme_id)
    with _errors():
        _suite(session, user, assistant, theme_id, command.suite_id)
        if command.parent_batch_id:
            _batch(session, user, assistant, theme_id, command.parent_batch_id)
        row, started = store.start_batch(
            session,
            theme_id,
            user.oid,
            user.id,
            command.request_id,
            command.model_dump(mode="json", exclude={"request_id"}),
            lambda cases: runtime.prepare(
                session, user, theme_id, cases, command.versions
            ),
        )
        if started:
            runtime.schedule(row.id)
        else:
            runtime.check_read(session, user, theme_id, row.context)
        return store.public_batch(session, row, detail=True)


@router.get("/batches/{batch_id}", summary="Read a fixed evaluation report")
def batch_detail(
    theme_id: ThemeId,
    batch_id: Id,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _bind(session, user, assistant, theme_id)
    with _errors():
        row = _batch(session, user, assistant, theme_id, batch_id)
        return store.public_batch(session, row, detail=True)


@router.post("/batches/{batch_id}/stop", summary="Stop dispatching an evaluation batch")
def stop_batch(
    theme_id: ThemeId,
    batch_id: Id,
    command: BatchCommand,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _bind(session, user, assistant, theme_id)
    with _errors():
        _batch(session, user, assistant, theme_id, batch_id)
        row, _ = store.change_batch(
            session, batch_id, user.id, command.request_id, "stop"
        )
        return store.public_batch(session, row, detail=True)


@router.post("/batches/{batch_id}/resume", summary="Resume an evaluation batch")
def resume_batch(
    theme_id: ThemeId,
    batch_id: Id,
    command: ResumeIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _bind(session, user, assistant, theme_id)
    with _errors():
        _batch(session, user, assistant, theme_id, batch_id)
        row, started = store.change_batch(
            session, batch_id, user.id, command.request_id, "resume", command.case_ids,
            verify_resume=runtime.require_execution_versions,
        )
        if started:
            runtime.schedule(row.id)
        return store.public_batch(session, row, detail=True)


@router.get("/compare", summary="Compare saved evaluation reports")
def compare(
    theme_id: ThemeId,
    baseline: Id,
    candidate: Id,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    _bind(session, user, assistant, theme_id)
    with _errors():
        left = _batch(session, user, assistant, theme_id, baseline)
        right = _batch(session, user, assistant, theme_id, candidate)
        return store.compare(session, left, right)


@router.get("/batches/{batch_id}/export", summary="Export a fixed evaluation report")
def export(
    theme_id: ThemeId,
    batch_id: Id,
    session: SessionDep,
    user: CurrentUser,
    assistant: CurrentAssistant,
):
    report = batch_detail(theme_id, batch_id, session, user, assistant)
    with _errors():
        suite = store.get_suite(session, theme_id, user.oid, report["suite_id"])
        report["suite"] = store.public_suite(suite, detail=True)
    return Response(
        json.dumps(
            jsonable_encoder(report), indent=2, ensure_ascii=False, allow_nan=False
        ),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="evaluation-{report["id"]}.json"',
            "Cache-Control": "private, no-store",
        },
    )
