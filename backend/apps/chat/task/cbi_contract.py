import json
import os
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from apps.chat.task.cbi_semantic import (
    FACT_CLAUSE,
    FACT_EXCLUSION_AFTER,
    FACT_EXCLUSION_BEFORE,
    MONTH_PHRASE,
    TopicSemantics,
)
from apps.chat.task.cbi_theme_validation import signature

OutputId = Literal[
    "current", "baseline", "delta", "rate", "target", "attainment", "refund_members"
]
CompareOutputs = {"baseline", "delta", "rate", "refund_members"}
ResultShape = Literal["summary", "trend", "category"]
ComparisonRule = Literal["fixed", "previous_day"]
Identifier = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")]
Store = Annotated[str, Field(min_length=1, max_length=48, pattern=r"^[\w -]+$")]


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Definition(BaseModel):
    id: str
    label: str
    description: str
    aliases: list[str] = Field(default_factory=list)
    view: Identifier


class Topic(BaseModel):
    version: str
    source_version: str | None = None
    config_digest: str | None = None
    timezone: str
    definitions: list[Definition]
    default_definition: str | None = None
    layout: Literal["store_day", "signed_events"] = "signed_events"
    evidence_view: Identifier | None = None
    compare_definition: str | None = None
    expected_database: str = ""
    expected_reader: str = ""
    expected_port: int = 5432
    expected_host: str = "127.0.0.1"
    asset_id: str | None = None
    connection_digest: str | None = None
    label: str | None = None
    publication_view: Identifier | None = None
    category_enabled: bool = False
    semantic: TopicSemantics | None = None

    @model_validator(mode="after")
    def validate_definitions(self):
        keys = {definition.id for definition in self.definitions}
        if not keys or len(keys) != len(self.definitions):
            raise ValueError("Definition identifiers must be unique")
        if any(
            key and key not in keys
            for key in (self.default_definition, self.compare_definition)
        ):
            raise ValueError("Configured definition was not registered")
        if self.evidence_view and not self.compare_definition:
            raise ValueError("Evidence requires a configured reference definition")
        return self

    def by_id(self, id_: str) -> Definition:
        return next(
            definition for definition in self.definitions if definition.id == id_
        )


class Intent(BaseModel):
    operation: Literal["new", "revise", "unsupported"] = "new"
    store: Store | None = None
    current: date | None = None
    baseline: date | None = None
    comparison_rule: ComparisonRule | None = None
    shape: ResultShape | None = None
    range_start: date | None = None
    current_day_offset: Literal[-2, -1, 0] | None = None
    definition: str | None = None
    requested: list[OutputId] = Field(default_factory=list, max_length=7)


class ScopeChange(BaseModel):
    model_config = {"extra": "forbid"}

    current: date
    baseline: date | None = None
    comparison_rule: ComparisonRule | None = None
    shape: ResultShape | None = None
    range_start: date | None = None


class Action(BaseModel):
    action: Literal[
        "confirm_definition",
        "continue_evidence",
        "recalculate_latest",
        "retry_quality",
        "confirm_publication",
        "cancel_publication",
        "revise_scope",
        "start_investigation",
        "resume_investigation",
        "confirm_investigation_source",
        "cancel_investigation_source",
    ]
    request_id: Annotated[
        str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    ]
    expected_revision: int
    decision_id: str | None = None
    choice_id: str | None = None
    scope_change: ScopeChange | None = None


InvestigationActions = {
    "start_investigation", "resume_investigation", "confirm_investigation_source",
    "cancel_investigation_source",
}


class ActionRejected(ValueError):
    pass


def load_topic(datasource: int | None) -> Topic | None:
    location = os.environ.get("SQLBOT_CBI_TOPICS")
    if not location or not datasource:
        return None
    data = json.loads(Path(location).read_text(encoding="utf-8"))
    item = data.get(str(datasource))
    return Topic.model_validate(item) if item else None


def managed_ids() -> set[int]:
    return {
        int(raw)
        for raw in os.environ.get("SQLBOT_CBI_MANAGED_TOPICS", "").split(",")
        if raw.strip()
    }


def connection_digest(connection):
    return signature(
        {
            "host": connection.host,
            "port": connection.port,
            "database": connection.database,
            "username": connection.username,
            "schema": connection.dbSchema,
            "options": connection.extraJdbc,
        }
    )


def source_matches(topic, connection):
    digest = getattr(topic, "connection_digest", None)
    if digest is not None:
        return connection_digest(connection) == digest
    return (
        connection.database,
        connection.username,
        connection.host,
        connection.port,
    ) == (
        topic.expected_database,
        topic.expected_reader,
        getattr(topic, "expected_host", "127.0.0.1"),
        topic.expected_port,
    )


def is_managed_topic(session, datasource):
    from apps.chat.curd import cbi_theme

    if not datasource:
        return False
    if datasource in managed_ids():
        return True
    try:
        return cbi_theme.get_theme(session, datasource) is not None
    except Exception as error:
        # A missing registry must never turn a managed source into free-form SQL.
        raise HTTPException(503, "cbi_theme_unavailable") from error


def managed_record(session, datasource, version, *, allow_unpublished=False):
    from apps.chat.curd import cbi_theme

    head = cbi_theme.get_theme(session, datasource)
    if not head:
        return None, None
    if not version:
        version = head.active_version or (
            head.initial_version if allow_unpublished else None
        )
    if not version or (version == head.initial_version and not allow_unpublished):
        return head, None
    try:
        return head, cbi_theme.get_version(session, datasource, version)
    except cbi_theme.StoreError as error:
        if error.status in {404, 409}:
            return head, None
        raise


def load_effective_topic(
    session, datasource, *, workspace, version=None, allow_unpublished=False
) -> Topic | None:
    if not datasource:
        return None
    try:
        options = {"allow_unpublished": True} if allow_unpublished else {}
        head, recorded = managed_record(session, datasource, version, **options)
    except Exception as error:
        # A managed binding never consults the legacy file when the store is unavailable.
        raise HTTPException(503, "cbi_theme_unavailable") from error
    if not head:
        if datasource in managed_ids():
            raise HTTPException(503, "cbi_theme_unavailable")
        return load_topic(datasource)
    if head.oid != workspace:
        raise HTTPException(404, "analysis_not_found")
    registered = getattr(head, "initial_version", None) is not None
    if registered and not version and not allow_unpublished:
        if not head.active_version:
            raise HTTPException(409, "cbi_theme_not_published")
        if not head.enabled:
            raise HTTPException(409, "cbi_theme_disabled")
    if not recorded or getattr(recorded, "enabled", True) is False:
        raise HTTPException(409 if version else 503, "cbi_theme_version_unavailable")
    try:
        topic = Topic.model_validate(recorded.content)
        if (
            not topic.semantic
            or (
                registered
                and not (topic.asset_id and topic.connection_digest and topic.label)
            )
            or (not registered and not topic.source_version)
            or topic.version != recorded.version
        ):
            raise ValueError("Invalid managed theme")
    except ValueError as error:
        raise HTTPException(503, "cbi_theme_unavailable") from error
    return topic.model_copy(update={"config_digest": recorded.content_digest})


def historical_metric_catalog(current_ids, versions, allowed_fields):
    """Names are recognition metadata, never executable definitions."""
    catalog = {}
    for metrics in versions:
        for key, metric in metrics.items():
            if key in current_ids or metric["field"] not in allowed_fields:
                continue
            item = {"label": metric["label"], "meaning": metric["description"]}
            names = catalog.setdefault(key, [])
            if item not in names:
                names.append(item)
    return catalog


def _normalized_metric_text(value):
    return unicodedata.normalize("NFKC", value).casefold()


def _metric_occurrences(text, key, names):
    yield from re.finditer(
        r"(?<![a-z0-9_])" + re.escape(_normalized_metric_text(key)) + r"(?![a-z0-9_])",
        text,
    )
    for name in names:
        compact = "".join(_normalized_metric_text(name).split())
        if len(compact) >= 2:
            yield from re.finditer(r"\s*".join(map(re.escape, compact)), text)


GENERIC_COMPARISON_QUOTE = re.compile(
    rf"(?:请)?(?:计算|给出|展示|看)?(?:(?:{MONTH_PHRASE}|本期|两期|这两期)"
    rf"(?:相对(?:于)?(?:{MONTH_PHRASE}|基期|上期|它))?(?:的)?)?"
    r"(?:变化率|变化|差额|增幅|降幅|差异)"
)


def validate_metric_request(
    question, intent, current, default_metric, history, prior=None
):
    model_metrics = list(intent.metrics or ())
    model_overall = list(intent.overall_metrics or ())
    generic_references = []

    def decision(disposition, reason, keys=(), *, basis=None, metrics=None, overall=None):
        return {
            "disposition": disposition,
            "reason": reason,
            "requested_unavailable": sorted(keys),
            "basis": basis,
            "model_metrics": model_metrics,
            "model_overall_metrics": model_overall,
            "adopted_metrics": metrics,
            "adopted_overall_metrics": overall,
            "generic_comparison_references": generic_references,
        }

    roles, quoted_spans = {}, {}
    normalized = _normalized_metric_text(question)
    compact_question = "".join(normalized.split())
    for ref in intent.metric_references:
        compact_quote = "".join(_normalized_metric_text(ref.quote).split())
        if (ref.role == "requested" and ref.metric_id in current
                and compact_quote in compact_question and GENERIC_COMPARISON_QUOTE.fullmatch(compact_quote)):
            generic_references.append(ref.model_dump())
            continue
        if ref.quote not in question or not ref.quote.strip():
            return decision("clarify", "invalid_metric_quote")
        if ref.metric_id not in current and ref.metric_id not in history:
            # A quoted unregistered request may explain a refusal. It can never
            # supply a metric for an executable plan.
            if intent.operation == "unsupported" and ref.role == "requested":
                continue
            return decision("clarify", "unknown_metric_reference")
        if ref.metric_id in roles and roles[ref.metric_id] != ref.role:
            return decision("clarify", "conflicting_metric_roles")
        roles[ref.metric_id] = ref.role
        phrase = _normalized_metric_text(ref.quote)
        spans = quoted_spans.setdefault(ref.metric_id, set())
        spans.update(
            (match.start(), match.start() + len(phrase))
            for match in re.finditer(r"(?=" + re.escape(phrase) + r")", normalized)
        )

    active_names = {
        "".join(_normalized_metric_text(label).split()) for label in current.values()
    }
    for key, metadata in history.items():
        names = [
            item["label"]
            for item in metadata
            if "".join(_normalized_metric_text(item["label"]).split())
            not in active_names
        ]
        for match in _metric_occurrences(normalized, key, names):
            if not any(
                a <= match.start() and b >= match.end()
                for a, b in quoted_spans.get(key, ())
            ):
                return decision("clarify", "missing_metric_reference")

    if "uncertain" in roles.values():
        return decision("clarify", "uncertain_metric_role")
    requested = {key for key, role in roles.items() if role == "requested"}
    unavailable = requested & history.keys()
    if unavailable:
        return decision("unsupported", "metric_not_in_version", unavailable)
    if intent.operation == "unsupported":
        return decision("allow", "metric_references_consistent")

    proposed = set(model_metrics + model_overall)
    if proposed - current.keys():
        return decision("clarify", "metric_selection_conflict")

    excluded = {key for key, role in roles.items() if role in {"excluded", "context"}}
    if proposed & excluded:
        return decision("clarify", "metric_selection_conflict")
    for key, label in current.items():
        if key in roles:
            continue
        for match in _metric_occurrences(normalized, key, [label]):
            before = FACT_CLAUSE.split(normalized[:match.start()])[-1]
            after = FACT_CLAUSE.split(normalized[match.end():])[0]
            if FACT_EXCLUSION_BEFORE.search(before) or FACT_EXCLUSION_AFTER.match(after):
                excluded.add(key)
            elif re.search(r"上次|上轮|曾经|提到", before):
                return decision("clarify", "missing_metric_reference")
            else:
                requested.add(key)

    if requested:
        basis = "explicit_metrics"
        metrics = [key for key in model_metrics if key in requested]
        overall = [key for key in model_overall if key in requested]
        unassigned = requested - set(metrics + overall)
        if overall and unassigned:
            return decision("clarify", "metric_selection_conflict")
        metrics.extend(key for key in current if key in unassigned)
    elif intent.operation == "revise" and prior:
        basis = "parent_metrics"
        metrics = list(prior.get("metrics") or ())
        overall = list(prior.get("overall_metrics") or ())
    else:
        basis = "published_default"
        metrics = [default_metric]
        overall = []
    if not overall and intent.totals:
        overall = metrics
    adopted = set(metrics + overall)
    if not metrics or len(metrics) > 5 or len(overall) > 5 or adopted - current.keys() or adopted & excluded:
        return decision("clarify", "metric_selection_conflict")
    return decision("allow", "metric_references_consistent", basis=basis,
                    metrics=list(dict.fromkeys(metrics)), overall=list(dict.fromkeys(overall)))


def explicitly_chosen(question: str, topic: Topic) -> str | None:
    def mentioned(alias, prefixes):
        return any(
            re.search(prefix + r"\s*" + re.escape(alias), question)
            for prefix in prefixes
        )

    selected = {
        definition.id
        for definition in topic.definitions
        if any(
            mentioned(alias, ("按", "使用", "选择", "采用"))
            for alias in definition.aliases
        )
    }
    if len(selected) == 1:
        return next(iter(selected))
    candidates = [
        definition.id
        for definition in topic.definitions
        if any(alias in question for alias in definition.aliases)
    ]
    return candidates[0] if len(candidates) == 1 else None


def resolve_request(
    question,
    intent: Intent,
    topic: Topic,
    prior=None,
    parent_record_id=None,
    *,
    received_at: datetime | None = None,
):
    received_at = received_at or datetime.now(UTC)
    inherited = prior["scope"] if prior and intent.operation == "revise" else {}
    requested = list(
        dict.fromkeys(
            intent.requested or (prior["request"]["outputs"] if inherited else [])
        )
    )
    shape = (
        intent.shape
        or (prior["request"].get("shape") if inherited else None)
        or "summary"
    )
    needs_baseline = shape == "summary" and bool(CompareOutputs.intersection(requested))
    definition = intent.definition
    definition_ids = {item.id for item in topic.definitions}
    if definition not in definition_ids:
        definition = None
    definition = definition or inherited.get("definition") or topic.default_definition
    source = (
        "question"
        if intent.definition == definition and definition
        else "inherited"
        if inherited.get("definition") == definition and definition
        else "default"
        if definition
        else None
    )
    explicit_current = intent.current
    if intent.current_day_offset is not None:
        business_today = received_at.astimezone(ZoneInfo(topic.timezone)).date()
        explicit_current = business_today + timedelta(days=intent.current_day_offset)
    current = (
        explicit_current.isoformat() if explicit_current else inherited.get("current")
    )
    comparison_rule = intent.comparison_rule or (
        "fixed" if intent.baseline else inherited.get("comparison_rule")
    )
    baseline = None
    if comparison_rule == "previous_day" and current:
        current_day = date.fromisoformat(current)
        baseline = (
            (current_day - timedelta(days=1)).isoformat()
            if current_day > date.min
            else None
        )
    elif intent.baseline:
        baseline = intent.baseline.isoformat()
    elif comparison_rule == "fixed" or current == inherited.get("current"):
        baseline = inherited.get("baseline")
    elif not needs_baseline:
        comparison_rule = None

    if not needs_baseline and intent.baseline is None:
        baseline = None

    if shape != "summary":
        baseline = comparison_rule = None
        requested = []
    start = (
        intent.range_start.isoformat()
        if intent.range_start
        else inherited.get("range_start")
    )

    scope = {
        "store": intent.store or inherited.get("store"),
        "current": current,
        "range_start": start if shape != "summary" else None,
        "baseline": baseline,
        "comparison_rule": comparison_rule,
        "timezone": topic.timezone,
        "category_available": topic.category_enabled,
        "definition": definition,
        "source": source,
    }
    if definition:
        scope["definition_label"] = topic.by_id(definition).label
    status = "running"
    decision = {"kind": "ready"}
    if (
        intent.operation == "unsupported"
        or not (scope["store"] and current and (requested or shape != "summary"))
        or (needs_baseline and not baseline)
    ):
        status, decision = (
            "blocked",
            {"kind": "unsupported", "reason": "scope_required"},
        )
        if (
            needs_baseline
            and current
            and not baseline
            and intent.operation != "unsupported"
        ):
            decision["reason"] = "comparison_required"
    elif shape != "summary" and (
        (shape == "trend" and not start)
        or (
            start
            and not 0
            <= (date.fromisoformat(current) - date.fromisoformat(start)).days
            < 92
        )
        or (
            shape == "category"
            and (not topic.category_enabled or topic.layout != "signed_events")
        )
    ):
        status, decision = "blocked", {"kind": "unsupported", "reason": "series_scope"}
    elif not definition:
        status = "awaiting_confirmation"
        decision = {
            "kind": "needs_confirmation",
            "id": uuid4().hex,
            "field": "definition",
            "options": [
                d.model_dump(exclude={"view", "aliases"}) for d in topic.definitions
            ],
        }
    return {
        "schema_version": 1,
        "revision": 1,
        "config_version": topic.version,
        **({"config_digest": topic.config_digest} if topic.config_digest else {}),
        "request": {
            "text": question,
            "received_at": received_at.isoformat(),
            "outputs": requested,
            "shape": shape,
            "parent_record_id": str(parent_record_id) if inherited else None,
        },
        "scope": scope,
        "decision": decision,
        "task_status": status,
        "outputs": [],
        "steps": [],
        "events": [],
        "presentation": {"series_mode": "none", "series_semantics": "unknown"},
    }


def _accept_action(state, action: Action, source: str, *, allow_continue: bool):
    payload = action.model_dump(mode="json", exclude_none=True)
    previous = next(
        (e for e in state["events"] if e["request_id"] == action.request_id), None
    )
    if previous:
        if (
            previous.get("payload") is not None
            and {
                key: value
                for key, value in previous["payload"].items()
                if value is not None
            }
            != payload
        ):
            raise ActionRejected("request_id_conflict")
        return state, False
    if state["revision"] != action.expected_revision:
        raise ActionRejected("stale_revision")
    if action.action == "recalculate_latest":
        if state["task_status"] not in {"completed", "partial"} or not state[
            "scope"
        ].get("definition"):
            raise ActionRejected("recalculation_unavailable")
        updated = dict(state)
    elif action.action == "retry_quality":
        if (not state.get("semantic_plan") or state.get("investigation")
            or not state.get("quality", {}).get("unavailable_outputs")
            or state["task_status"] not in {"blocked", "partial"}):
            raise ActionRejected("quality_retry_unavailable")
        updated = dict(state)
    elif action.action == "revise_scope":
        if (
            state["task_status"] not in {"completed", "partial"}
            or action.scope_change is None
        ):
            raise ActionRejected("scope_revision_unavailable")
        updated = dict(state)
    elif action.action in {"confirm_publication", "cancel_publication"}:
        pending = state.get("publication_confirmation")
        if (
            state["task_status"] != "partial"
            or not pending
            or action.decision_id != pending["id"]
        ):
            raise ActionRejected("stale_decision")
        updated = dict(state)
        if action.action == "confirm_publication":
            updated["task_status"] = "running"
        else:
            updated.pop("publication_confirmation", None)
    elif action.action in InvestigationActions:
        investigation = state.get("investigation") or {}
        updated = dict(state)
        if action.action == "start_investigation":
            if state["task_status"] != "completed" or not state.get("source_proof"):
                raise ActionRejected("investigation_parent_required")
        elif action.action == "resume_investigation":
            if (state["task_status"] not in {"partial", "blocked"} or not investigation
                or investigation.get("source_confirmation")
                or investigation.get("stop_reason") in {"plan_complete", "refund_facts_unavailable"}):
                raise ActionRejected("investigation_resume_unavailable")
            updated["task_status"] = "running"
            updated["investigation"] = dict(investigation, status="running")
        else:
            pending = investigation.get("source_confirmation")
            if not pending or pending["id"] != action.decision_id:
                raise ActionRejected("stale_decision")
            updated["investigation"] = {
                key: value for key, value in investigation.items() if key != "source_confirmation"
            }

    elif action.action == "continue_evidence":
        if not allow_continue:
            raise ActionRejected("evidence_unavailable")
        updated = dict(state, task_status="running")
    else:
        decision = state["decision"]
        if state[
            "task_status"
        ] != "awaiting_confirmation" or action.decision_id != decision.get("id"):
            raise ActionRejected("stale_decision")
        options = {item["id"]: item["label"] for item in decision["options"]}
        if action.choice_id not in options:
            raise ActionRejected("invalid_option")
        updated = dict(
            state,
            task_status="running",
            scope=dict(
                state["scope"],
                definition=action.choice_id,
                definition_label=options[action.choice_id],
                source=source,
            ),
            decision=dict(
                decision,
                kind="ready",
                choice=action.choice_id,
                confirmed_at=timestamp(),
            ),
        )
    event = {
        "action": action.action,
        "request_id": action.request_id,
        "at": timestamp(),
        "source": source,
        "payload": payload,
    }
    return dict(
        updated, revision=state["revision"] + 1, events=[*state["events"], event]
    ), True


def accept_investigation_action(state, action: Action, source: str):
    return _accept_action(state, action, source, allow_continue=False)


def accept_confirmation(state, action: Action, source: str):
    return _accept_action(state, action, source, allow_continue=False)


def accept_evidence(state, action: Action):
    last_attempt = next(
        (
            e["action"]
            for e in reversed(state["events"])
            if e["action"] in {"continue_evidence", "cancel_publication"}
        ),
        None,
    )
    eligible = (
        state["task_status"] == "partial"
        and "refund_members" in state["request"]["outputs"]
        and any(
            s["kind"] == "summary" and s["status"] == "verified" for s in state["steps"]
        )
        and not state.get("publication_confirmation")
        and last_attempt != "continue_evidence"
    )
    return _accept_action(state, action, "button", allow_continue=eligible)


def accept_quality_retry(state, action: Action):
    return _accept_action(state, action, "button", allow_continue=False)


def accept_recalculation(state, action: Action):
    return _accept_action(state, action, "button", allow_continue=False)


def accept_scope_change(state, action: Action):
    return _accept_action(state, action, "button", allow_continue=False)


def accept_publication_action(state, action: Action):
    return _accept_action(state, action, "button", allow_continue=False)


def offer_publication_confirmation(state, version):
    return dict(
        state,
        task_status="partial",
        revision=state["revision"] + 1,
        publication_confirmation={
            "id": uuid4().hex,
            "prior_version": state.get("data_version"),
            "version": version,
        },
    )


def evidence_can_reuse(state, current_version):
    return current_version is not None and state.get("data_version") == current_version


def fork_run(state, parent_id):
    request = {
        key: value
        for key, value in state["request"].items()
        if key not in {"client_request_id", "selected_parent"}
    }
    return {
        "schema_version": state["schema_version"],
        "revision": 1,
        "config_version": state["config_version"],
        "request": dict(request, parent_record_id=str(parent_id)),
        "scope": dict(state["scope"]),
        "decision": {"kind": "ready"},
        "task_status": "running",
        "outputs": [],
        "steps": [],
        "events": [],
        "presentation": {"series_mode": "none", "series_semantics": "unknown"},
    }
