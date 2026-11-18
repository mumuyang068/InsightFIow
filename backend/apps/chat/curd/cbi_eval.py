"""Versioned evaluation registry and durable single-batch dispatch state."""

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from apps.chat.curd.cbi_theme import StoreError, _lock_theme
from apps.chat.models.cbi_eval import (
    EvaluationAttempt,
    EvaluationBatch,
    EvaluationCommand,
    EvaluationSuite,
)

PROCESS_EPOCH = str(uuid4())
ACTIVE = {"pending", "running"}
SCORED = {"passed", "failed", "review", "indeterminate"}
OUTCOMES = SCORED | {"system_error", "evaluator_error", "interrupted"}
MAX_BUDGET = 48


def _digest(value: dict | list) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return sha256(payload.encode()).hexdigest()
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise StoreError("evaluation_invalid_document", 422) from exc


def get_suite(
    session: Session, theme_id: int, workspace_id: int, suite_id: str
) -> EvaluationSuite:
    row = session.get(EvaluationSuite, suite_id)
    if row is None or (row.theme_id, row.workspace_id) != (theme_id, workspace_id):
        raise StoreError("evaluation_suite_unavailable", 404)
    return row


def list_suites(
    session: Session, theme_id: int, workspace_id: int
) -> list[EvaluationSuite]:
    return list(
        session.scalars(
            select(EvaluationSuite)
            .where(
                EvaluationSuite.theme_id == theme_id,
                EvaluationSuite.workspace_id == workspace_id,
            )
            .order_by(EvaluationSuite.created_at.desc(), EvaluationSuite.id)
        )
    )


def save_suite(
    session: Session,
    theme_id: int,
    workspace_id: int,
    actor_id: int,
    request_id: str,
    *,
    title: str,
    description: str,
    cases: list[dict],
    previous_id: str | None,
    confirm: bool,
) -> EvaluationSuite:
    theme = _lock_theme(session, theme_id)
    if theme.oid != workspace_id:
        raise StoreError("evaluation_suite_unavailable", 404)
    request_digest = _digest(
        {
            "title": title,
            "description": description,
            "cases": cases,
            "previous_id": previous_id,
            "confirm": confirm,
        }
    )
    replay = session.scalar(
        select(EvaluationSuite).where(
            EvaluationSuite.theme_id == theme_id,
            EvaluationSuite.actor_id == actor_id,
            EvaluationSuite.request_id == request_id,
        )
    )
    if replay is not None:
        if replay.request_digest != request_digest:
            raise StoreError("evaluation_request_conflict")
        return replay
    previous = (
        get_suite(session, theme_id, workspace_id, previous_id) if previous_id else None
    )
    row = EvaluationSuite(
        id=str(uuid4()),
        theme_id=theme_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        request_id=request_id,
        request_digest=request_digest,
        previous_id=previous_id,
        revision=previous.revision + 1 if previous else 1,
        title=title,
        description=description,
        cases=deepcopy(cases),
        digest=_digest(cases),
        status="confirmed" if confirm else "draft",
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    return row


def public_suite(row: EvaluationSuite, *, detail=False) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "revision": row.revision,
        "status": row.status,
        "digest": row.digest,
        "case_count": len(row.cases),
        "case_ids": [case["id"] for case in row.cases],
        "categories": sorted({case["category"] for case in row.cases}),
        "previous_id": row.previous_id,
        "created_at": row.created_at,
        "confirmed_by": str(row.actor_id) if row.status == "confirmed" else None,
        **({"cases": row.cases} if detail else {}),
    }


def get_batch(session: Session, batch_id: str) -> EvaluationBatch | None:
    return session.get(EvaluationBatch, batch_id, populate_existing=True)


def scoped_batch(
    session: Session, theme_id: int, workspace_id: int, batch_id: str
) -> EvaluationBatch:
    item = get_batch(session, batch_id)
    if not item or (item.theme_id, item.workspace_id) != (theme_id, workspace_id):
        raise StoreError("evaluation_batch_unavailable", 404)
    return item


def _locked_batch(session: Session, batch_id: str) -> EvaluationBatch | None:
    return session.get(
        EvaluationBatch, batch_id, populate_existing=True, with_for_update=True
    )


def list_batches(
    session: Session, theme_id: int, workspace_id: int
) -> list[EvaluationBatch]:
    return list(
        session.scalars(
            select(EvaluationBatch)
            .where(
                EvaluationBatch.theme_id == theme_id,
                EvaluationBatch.workspace_id == workspace_id,
            )
            .order_by(EvaluationBatch.created_at.desc(), EvaluationBatch.id)
        )
    )


def start_batch(
    session: Session,
    theme_id: int,
    workspace_id: int,
    actor_id: int,
    request_id: str,
    command: dict,
    prepare: Callable[[list[dict]], dict],
) -> tuple[EvaluationBatch, bool]:
    theme = _lock_theme(session, theme_id)
    if theme.oid != workspace_id:
        raise StoreError("evaluation_batch_unavailable", 404)
    request_digest = _digest(command)
    replay = session.scalar(
        select(EvaluationBatch).where(
            EvaluationBatch.theme_id == theme_id,
            EvaluationBatch.actor_id == actor_id,
            EvaluationBatch.request_id == request_id,
        )
    )
    if replay is not None:
        if replay.request_digest != request_digest:
            raise StoreError("evaluation_request_conflict")
        return replay, False

    suite = get_suite(session, theme_id, workspace_id, command["suite_id"])
    if suite.status != "confirmed":
        raise StoreError("evaluation_suite_unconfirmed")
    if parent_id := command.get("parent_batch_id"):
        parent = scoped_batch(session, theme_id, workspace_id, parent_id)
        if parent.suite_id != suite.id:
            raise StoreError("evaluation_parent_suite_mismatch")
    cases = {case["id"]: case for case in suite.cases}
    requested = command.get("case_ids")
    if requested is not None and (not requested or set(requested) - cases.keys()):
        raise StoreError("evaluation_invalid_case_selection", 422)
    selected = [key for key in cases if requested is None or key in requested]
    if any(set(cases[key].get("protects", ())) - set(selected) for key in selected):
        raise StoreError("evaluation_protection_cases_required", 422)
    if set(command["versions"]) != {case["datasource_id"] for case in cases.values()}:
        raise StoreError("evaluation_incomplete_configuration", 422)
    budget = command["budget"]
    if not 1 <= budget <= MAX_BUDGET:
        raise StoreError("evaluation_invalid_budget", 422)

    context = prepare(suite.cases)
    stamp = datetime.now(UTC)
    row = EvaluationBatch(
        id=str(uuid4()),
        theme_id=theme_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        request_id=request_id,
        request_digest=request_digest,
        suite_id=suite.id,
        parent_batch_id=parent_id,
        label=command["label"],
        budget=budget,
        declared_variable=command["declared_variable"],
        context=context,
        case_ids=selected,
        remaining=selected.copy(),
        status="pending",
        worker_epoch=PROCESS_EPOCH,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(row)
    session.commit()
    return row, True


def _attempts(session: Session, batch_id: str) -> list[EvaluationAttempt]:
    return list(
        session.scalars(
            select(EvaluationAttempt)
            .where(
                EvaluationAttempt.batch_id == batch_id,
            )
            .order_by(EvaluationAttempt.case_id, EvaluationAttempt.number)
            .execution_options(populate_existing=True)
        )
    )


def _running_attempt(session: Session, batch_id: str) -> EvaluationAttempt | None:
    return session.scalar(
        select(EvaluationAttempt)
        .where(
            EvaluationAttempt.batch_id == batch_id,
            EvaluationAttempt.status == "running",
        )
        .execution_options(populate_existing=True)
    )


def _stop(batch: EvaluationBatch, reason: str) -> None:
    batch.status = "stopped"
    batch.stop_reason = reason
    batch.updated_at = datetime.now(UTC)


def _status(batch: EvaluationBatch) -> tuple[str, str | None]:
    if batch.worker_epoch != PROCESS_EPOCH and batch.status in ACTIVE:
        return "stopped", "evaluation_process_restarted"
    return batch.status, batch.stop_reason


def change_batch(
    session: Session,
    batch_id: str,
    actor_id: int,
    request_id: str,
    action: str,
    case_ids: list[str] | None = None,
    *,
    verify_resume: Callable[[dict], None] | None = None,
) -> tuple[EvaluationBatch, bool]:
    row = _locked_batch(session, batch_id)
    if row is None:
        raise StoreError("evaluation_batch_unavailable", 404)
    digest = _digest({"action": action, "actor": str(actor_id), "cases": case_ids})
    existing = session.get(EvaluationCommand, (batch_id, request_id))
    if existing:
        if existing.request_digest != digest:
            raise StoreError("evaluation_request_conflict")
        return row, False

    started = False
    if action == "stop":
        if _status(row)[0] in ACTIVE:
            _stop(row, "evaluation_stopped_by_user")
    elif action == "resume":
        running = _running_attempt(session, batch_id)
        if row.worker_epoch == PROCESS_EPOCH and (
            running is not None or row.status in ACTIVE
        ):
            raise StoreError("evaluation_already_running")
        queue = row.remaining
        if case_ids is not None:
            if not case_ids or set(case_ids) - set(row.case_ids):
                raise StoreError("evaluation_invalid_case_selection", 422)
            prior = {
                attempt.case_id: attempt.status
                for attempt in _attempts(session, batch_id)
            }
            if any(prior.get(key) == "passed" and key not in queue for key in case_ids):
                raise StoreError("evaluation_completed_case_retry", 422)
            queue = [key for key in row.case_ids if key in case_ids or key in queue]
        if not queue:
            raise StoreError("evaluation_no_remaining_cases")
        if verify_resume is not None:
            verify_resume(row.context)
        if running is not None:
            running.status = "interrupted"
            running.finished_at = datetime.now(UTC)
        row.remaining = queue
        row.status = "pending"
        row.stop_reason = None
        row.worker_epoch = PROCESS_EPOCH
        row.updated_at = datetime.now(UTC)
        started = True
    else:
        raise ValueError("Unknown evaluation command")

    session.add(
        EvaluationCommand(
            batch_id=batch_id,
            request_id=request_id,
            request_digest=digest,
            actor_id=actor_id,
            action=action,
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    return row, started


def claim_next(session: Session, batch_id: str) -> dict | None:
    batch = _locked_batch(session, batch_id)
    if (
        batch is None
        or _status(batch)[0] not in ACTIVE
        or _running_attempt(session, batch_id)
    ):
        return None
    if not batch.remaining:
        batch.status = "completed"
        batch.updated_at = datetime.now(UTC)
        session.commit()
        return None
    case_id, *remaining = batch.remaining
    previous = session.scalar(
        select(func.max(EvaluationAttempt.number)).where(
            EvaluationAttempt.batch_id == batch_id,
            EvaluationAttempt.case_id == case_id,
        )
    )
    suite = get_suite(session, batch.theme_id, batch.workspace_id, batch.suite_id)
    case = next(case for case in suite.cases if case["id"] == case_id)
    now = datetime.now(UTC)
    attempt = EvaluationAttempt(
        id=str(uuid4()),
        batch_id=batch_id,
        case_id=case_id,
        number=(previous or 0) + 1,
        status="running",
        started_at=now,
    )
    batch.remaining = remaining
    batch.status = "running"
    batch.updated_at = now
    session.add(attempt)
    claimed = {
        "batch_id": batch_id,
        "attempt_id": attempt.id,
        "case_id": case_id,
        "theme_id": batch.theme_id,
        "context": batch.context,
        "budget": batch.budget,
        "case": case,
    }
    session.commit()
    return claimed


def _active_attempt(
    session: Session,
    batch_id: str,
    attempt_id: str,
) -> tuple[EvaluationBatch, EvaluationAttempt]:
    batch = _locked_batch(session, batch_id)
    attempt = session.get(EvaluationAttempt, attempt_id, populate_existing=True)
    if (
        batch is None
        or batch.worker_epoch != PROCESS_EPOCH
        or attempt is None
        or attempt.batch_id != batch_id
        or attempt.status != "running"
    ):
        raise StoreError("evaluation_attempt_unavailable", 409)
    return batch, attempt


def save_observation(
    session: Session, batch_id: str, attempt_id: str, observation: dict
) -> None:
    _, attempt = _active_attempt(session, batch_id, attempt_id)
    if attempt.observation is not None and attempt.observation != observation:
        raise StoreError("evaluation_observation_already_saved")
    attempt.observation = deepcopy(observation)
    session.commit()


def finish_attempt(
    session: Session,
    batch_id: str,
    attempt_id: str,
    *,
    status: str,
    observation: dict | None,
    judgement: dict | None,
    halt_reason: str | None = None,
) -> None:
    if status not in OUTCOMES:
        raise ValueError("Unknown evaluation outcome")
    batch, attempt = _active_attempt(session, batch_id, attempt_id)
    if observation is not None:
        if attempt.observation is not None and attempt.observation != observation:
            raise StoreError("evaluation_observation_already_saved")
        attempt.observation = deepcopy(observation)
    attempt.judgement = deepcopy(judgement)
    attempt.status = status
    attempt.finished_at = datetime.now(UTC)
    batch.updated_at = attempt.finished_at
    if halt_reason:
        _stop(batch, halt_reason)
    elif batch.status != "stopped" and not batch.remaining:
        batch.status = "completed"
    session.commit()


def halt_batch(session: Session, batch_id: str, reason: str) -> None:
    batch = _locked_batch(session, batch_id)
    if batch is None or batch.worker_epoch != PROCESS_EPOCH:
        return
    if attempt := _running_attempt(session, batch_id):
        attempt.status = "interrupted"
        attempt.finished_at = datetime.now(UTC)
    if batch.status in ACTIVE:
        _stop(batch, reason)
    session.commit()


def record_model_call(
    session: Session, batch_id: str, attempt_id: str, limit: int
) -> int:
    if session.get_bind().dialect.name == "postgresql":
        # Reserve before the provider request. An unknown receipt still consumes a reservation.
        session.execute(text("SELECT pg_advisory_xact_lock(5261186520778499155)"))
    batch, attempt = _active_attempt(session, batch_id, attempt_id)
    scope = batch.context.get("budget_scope")
    scope_filter = (
        EvaluationBatch.context["budget_scope"].as_string() == scope
        if scope
        else EvaluationBatch.id == batch_id
    )
    totals = session.execute(
        select(
            func.coalesce(func.sum(EvaluationAttempt.model_calls), 0),
            func.coalesce(
                func.sum(EvaluationAttempt.model_calls).filter(
                    EvaluationAttempt.batch_id == batch_id
                ),
                0,
            ),
        )
        .join(EvaluationBatch, EvaluationBatch.id == EvaluationAttempt.batch_id)
        .where(scope_filter)
    ).one()
    overall, in_batch = totals
    if overall >= min(limit, MAX_BUDGET) or in_batch >= batch.budget:
        raise StoreError("evaluation_budget_exhausted")
    attempt.model_calls += 1
    session.commit()
    return int(overall) + 1


def _public_attempt(attempt: EvaluationAttempt, stale: bool, *, detail: bool) -> dict:
    return {
        "id": attempt.id,
        "number": attempt.number,
        "status": "interrupted"
        if stale and attempt.status == "running"
        else attempt.status,
        "model_calls": attempt.model_calls,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        **(
            {"observation": attempt.observation, "judgement": attempt.judgement}
            if detail
            else {}
        ),
    }


def public_batch(session: Session, row: EvaluationBatch, *, detail=False) -> dict:
    suite = get_suite(session, row.theme_id, row.workspace_id, row.suite_id)
    by_case = defaultdict(list)
    stale = row.worker_epoch != PROCESS_EPOCH
    executed_cases = set()
    for attempt in _attempts(session, row.id):
        by_case[attempt.case_id].append(_public_attempt(attempt, stale, detail=detail))
        if attempt.observation and attempt.observation.get("turns"):
            executed_cases.add(attempt.case_id)
    states = Counter()
    rows = []
    first_passed = 0
    model_calls = 0
    for case in suite.cases:
        key = case["id"]
        attempts = by_case[key]
        first = attempts[0] if attempts else None
        latest = attempts[-1] if attempts else None
        selected = key in row.case_ids
        status = latest["status"] if latest else ("pending" if selected else "not_run")
        states[status] += 1
        first_passed += bool(first and first["status"] == "passed")
        model_calls += sum(item["model_calls"] for item in attempts)
        if detail:
            rows.append(
                {
                    "case_id": key,
                    "question": case["turns"][0]["question"],
                    "category": case["category"],
                    "status": status,
                    "selected": selected,
                    "first": first,
                    "latest": latest,
                    "attempts": attempts,
                }
            )
    status, stop_reason = _status(row)
    counts = {key: states[key] for key in OUTCOMES | {"pending", "running", "not_run"}}
    counts.update(
        planned=len(suite.cases),
        selected=len(row.case_ids),
        attempted=sum(bool(v) for v in by_case.values()),
        executed=len(executed_cases),
        unexecuted=len(suite.cases) - len(executed_cases),
        scored=sum(states[key] for key in SCORED),
        first_passed=first_passed,
        model_calls=model_calls,
    )
    return {
        "id": row.id,
        "label": row.label,
        "suite_id": row.suite_id,
        "suite_digest": suite.digest,
        "status": status,
        "stop_reason": stop_reason,
        "counts": counts,
        "context": row.context,
        "parent_batch_id": row.parent_batch_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "declared_variable": row.declared_variable,
        "budget": row.budget,
        "planned_submissions": sum(
            len(case["turns"]) for case in suite.cases if case["id"] in row.case_ids
        ),
        **({"rows": rows} if detail else {}),
    }


def comparison_differences(
    left: EvaluationBatch, right: EvaluationBatch, first: dict, second: dict
) -> list[dict]:
    differences = []

    def append(field, old, new, *, allowed=False):
        if old != new:
            differences.append(
                {"field": field, "baseline": old, "candidate": new, "allowed": allowed}
            )

    append("suite_id", left.suite_id, right.suite_id)
    append("suite_digest", first["suite_digest"], second["suite_digest"])
    append("declared_variable", left.declared_variable, right.declared_variable)
    allow_config = (
        left.declared_variable == right.declared_variable == "theme_configuration"
    )
    for field in (
        "workspace_id",
        "identity",
        "model",
        "data_version",
        "source_versions",
        "permissions",
        "code_version",
        "evaluator_version",
        "versions",
        "configurations",
    ):
        append(
            field,
            left.context.get(field),
            right.context.get(field),
            allowed=allow_config and field in {"versions", "configurations"},
        )
    return differences


def _pair_status(old: dict | None, new: dict | None, comparable: bool) -> str:
    if not comparable:
        return "incomparable"
    if old is None or new is None or not old["latest"] or not new["latest"]:
        return "unpaired"
    a, b = old["status"], new["status"]
    return {
        ("passed", "passed"): "both_passed",
        ("failed", "passed"): "improved",
        ("passed", "failed"): "regressed",
        ("failed", "failed"): "both_failed",
    }.get((a, b), "unchanged" if a == b else "changed")


def compare(session: Session, left: EvaluationBatch, right: EvaluationBatch) -> dict:
    first = public_batch(session, left, detail=True)
    second = public_batch(session, right, detail=True)
    differences = comparison_differences(left, right, first, second)
    comparable = all(difference["allowed"] for difference in differences)
    a = {row["case_id"]: row for row in first.pop("rows")}
    b = {row["case_id"]: row for row in second.pop("rows")}
    rows = []
    for key in a.keys() | b.keys():
        old, new = a.get(key), b.get(key)
        identity = old or new
        rows.append(
            {
                "case_id": key,
                "question": identity["question"],
                "category": identity["category"],
                "status": _pair_status(old, new, comparable),
                "baseline": old,
                "candidate": new,
            }
        )
    rows.sort(key=lambda row: row["case_id"])
    return {
        "baseline": first,
        "candidate": second,
        "comparable": comparable,
        "differences": differences,
        "rows": rows,
        "counts": dict(Counter(row["status"] for row in rows)),
    }


def failure_case(session: Session, batch: EvaluationBatch, case_id: str) -> dict:
    attempts = [
        row
        for row in _attempts(session, batch.id)
        if row.case_id == case_id and row.status in OUTCOMES - {"passed"}
    ]
    if not attempts:
        raise StoreError("evaluation_failed_case_unavailable", 404)
    attempt = attempts[-1]
    suite = get_suite(session, batch.theme_id, batch.workspace_id, batch.suite_id)
    case = next(item for item in suite.cases if item["id"] == case_id)
    turns = (attempt.observation or {}).get("turns", [])
    return {
        **case,
        "expected": None,
        "protects": [],
        "origin": {
            "kind": "failed_evaluation",
            "batch_id": batch.id,
            "case_id": case_id,
            "attempt_id": attempt.id,
            "configuration": batch.context.get("versions", {}).get(
                case["datasource_id"]
            ),
            "record_ids": [
                turn["record_id"] for turn in turns if turn.get("record_id")
            ],
            "scope": [
                turn["state"]["scope"]
                for turn in turns
                if (turn.get("state") or {}).get("scope")
            ],
        },
    }
