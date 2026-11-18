import asyncio
import json
import os
import re
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from threading import Event
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

import orjson
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlbot_xpack.permissions.models.ds_permission import DsPermission
from sqlbot_xpack.permissions.models.ds_rules import DsRules
from sqlmodel import Session

from apps.ai_model.model_factory import LLMFactory, get_default_config
from apps.chat.curd.chat import end_log, start_log, trigger_log_error
from apps.chat.models.chat_model import Chat, ChatRecord, OperationEnum
from apps.chat.task import cbi_investigation, cbi_quality_runtime, cbi_semantic
from apps.chat.task.cbi_contract import (
    Action,
    ActionRejected,
    Intent,
    InvestigationActions,
    accept_confirmation,
    accept_evidence,
    accept_investigation_action,
    accept_publication_action,
    accept_quality_retry,
    accept_recalculation,
    accept_scope_change,
    evidence_can_reuse,
    explicitly_chosen,
    fork_run,
    historical_metric_catalog,
    is_managed_topic,
    load_effective_topic,
    load_topic,
    offer_publication_confirmation,
    resolve_request,
    source_matches,
    validate_metric_request,
)
from apps.chat.task.cbi_outputs import (
    comparison,
    completion,
    number,
    outputs,
    presentation,
)
from apps.datasource.crud.datasource import get_table_obj_by_ds
from apps.datasource.models.datasource import CoreDatasource, CoreTable, DatasourceConf
from apps.datasource.utils.utils import aes_decrypt
from apps.db.db import exec_sql, get_session
from apps.system.models.system_model import UserWsModel
from common.core.db import engine
from common.utils.utils import SQLBotLogUtil

SummaryFields = {"store_id", "business_date", "net_sales"}
EventsFields = {"store_id", "business_date", "signed_cents"}
MemberFields = {
    "store_id",
    "business_date",
    "refund_id",
    "line_id",
    "paid_at_utc",
    "refund_at_utc",
    "amount_cents",
    "in_reference",
    "in_cohort",
}
Labels = {
    "current": "本期净销售额",
    "baseline": "基期净销售额",
    "delta": "变化额",
    "rate": "变化率",
    "target": "当天目标金额",
    "attainment": "目标完成率",
}
MemberLimit = 500
CategoryLimit = 8
ContextStatuses = ("completed", "partial", "awaiting_confirmation")


def _accessible_topic(session, user, chat, *, version=None):
    if not chat or chat.create_by != user.id or chat.oid != user.oid:
        raise HTTPException(404, "analysis_not_found")
    if not is_managed_topic(session, chat.datasource) and not load_topic(chat.datasource):
        return None, None
    if not user.isAdmin and not session.scalar(
        select(UserWsModel.uid).where(
            UserWsModel.uid == user.id, UserWsModel.oid == chat.oid
        )
    ):
        raise HTTPException(404, "analysis_not_found")
    topic = load_effective_topic(session, chat.datasource, workspace=chat.oid, version=version)
    if not topic:
        return None, None
    datasource = session.get(CoreDatasource, chat.datasource)
    if not datasource or datasource.oid != user.oid or datasource.type != "pg":
        raise HTTPException(409, "analysis_source_changed")
    connection = DatasourceConf.model_validate_json(
        aes_decrypt(datasource.configuration)
    )
    if not source_matches(topic, connection):
        raise HTTPException(409, "analysis_source_changed")
    if topic.asset_id:
        asset = session.get(CoreTable, int(topic.asset_id))
        if (not asset or asset.ds_id != datasource.id or not asset.checked
                or topic.by_id(topic.default_definition).view != f"{connection.dbSchema}.{asset.table_name}"):
            raise HTTPException(409, "analysis_asset_not_available")
    return topic, datasource


def _require_assets(session, user, datasource, required):
    permitted = {
        f"{asset.schema}.{asset.table.table_name}": {
            column.field_name for column in (asset.fields or [])
        }
        for asset in get_table_obj_by_ds(session, user, datasource)
    }
    if any(
        not fields.issubset(permitted.get(view, set()))
        for view, fields in required.items()
    ):
        raise HTTPException(409, "analysis_asset_not_available")
    # The registered SQL templates do not translate arbitrary row policies.
    # Deny assignment itself, including rules that cannot be resolved.
    if not user.isAdmin:
        rows = session.execute(
            select(DsPermission.id)
            .join(CoreTable, CoreTable.id == DsPermission.table_id)
            .join(
                DsRules,
                or_(
                    DsRules.permission_list.op("@>")(
                        func.jsonb_build_array(DsPermission.id)
                    ),
                    DsRules.permission_list.op("@>")(
                        func.jsonb_build_array(cast(DsPermission.id, String))
                    ),
                ),
            )
            .where(
                CoreTable.ds_id == datasource.id,
                CoreTable.table_name.in_([view.split(".")[1] for view in required]),
                DsPermission.type == "row",
                or_(
                    DsRules.user_list.op("@>")(cast([str(user.id)], JSONB)),
                    DsRules.user_list.op("@>")(cast([user.id], JSONB)),
                ),
            )
        ).first()
        if rows:
            raise HTTPException(403, "analysis_row_policy_unsupported")
    return permitted


def _record(session, user, chat, question, *, state=None, model_id=None):
    row = ChatRecord(
        chat_id=chat.id,
        datasource=chat.datasource,
        create_by=user.id,
        engine_type=chat.engine_type,
        question=question,
        create_time=datetime.now(),
        analysis_state=state,
        ai_modal_id=model_id,
    )
    session.add(row)
    session.flush()
    return row


def _save(session, row, state, *, finished=True):
    row.analysis_state = state
    row.finish = finished
    row.finish_time = datetime.now() if finished else None
    session.add(row)
    session.commit()


def _owned_parent(session, user, chat, parent_id):
    query = select(ChatRecord).where(
        ChatRecord.chat_id == chat.id,
        ChatRecord.create_by == user.id,
        ChatRecord.datasource == chat.datasource,
        ChatRecord.analysis_state.is_not(None),
        ChatRecord.analysis_state["internal"].as_boolean().is_not(True),
    )
    if parent_id is not None:
        row = session.scalars(query.where(ChatRecord.id == parent_id)).first()
        if not row:
            raise HTTPException(404, "analysis_parent_not_found")
    else:
        row = session.scalars(query.where(
            ChatRecord.analysis_state["task_status"].as_string().in_(ContextStatuses)
        ).order_by(ChatRecord.id.desc()).limit(1)).first()
    if row:
        check_saved_access(session, user, row)
    return row


async def _interpret(question, topic, prior, model, received_at, *, history=None, capture=None,
                     evaluation=None, allowed_fields=None):
    if topic.semantic:
        previous = (prior or {}).get("semantic_plan")
        breakdown = (prior or {}).get("presentation", {}).get("decomposition")
        if previous and breakdown and breakdown["reconciled"] and topic.semantic.decomposition:
            group = breakdown["dimension"]
            dimension = topic.semantic.dimensions[group]
            filter_key = group
            if dimension.identity:
                filter_key = next(key for key, candidate in topic.semantic.dimensions.items()
                                  if candidate.field == dimension.identity and not candidate.identity)
            previous = dict(previous, known_group_members={
                "filter_key": filter_key,
                "children": topic.semantic.decomposition.dimensions[group],
                "values": [{"key": item["key"], "label": item["label"]}
                           for item in breakdown["rows"][:25] if item["key"] is not None],
            })
        system = cbi_semantic.instructions(topic.semantic, previous, history, allowed_fields=allowed_fields)
        if capture is not None:
            capture["messages"] = [
                {"role":"system","content":system},
                {"role":"user","content":question},
            ]
        llm = evaluation.llm if evaluation else LLMFactory.create_llm(model).llm
        if evaluation:
            evaluation.before_model()
        message = await llm.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=question),
        ])
        response_text = str(message.content)
        if capture is not None and isinstance(message.content, str):
            capture["response_text"] = re.sub(r"<think>.*?(?:</think>|$)", "", message.content, flags=re.DOTALL | re.IGNORECASE)
        fence = "```"
        raw = response_text.strip().removeprefix(fence + "json").removeprefix(fence)
        raw = raw.removesuffix(fence).strip()
        return cbi_semantic.SemanticIntent.model_validate_json(raw), message.usage_metadata
    definitions = [
        {"id": d.id, "label": d.label, "aliases": d.aliases} for d in topic.definitions
    ]
    instructions = """Parse one question as JSON without SQL or analysis. Supported work: single-store net sales for single
business dates; prior date comparisons; daily target and attainment; comparing refund membership across net-sales
definitions; daily net-sales series for explicitly specified dates; net sales by category when registered. Assign operation new, revise (only for an explicit follow-up), or unsupported.
Keys: operation, store, current (ISO date), current_day_offset, baseline (ISO date), comparison_rule, requested,
shape (summary, trend, category), range_start (ISO date). For a daily series, set shape=trend, range_start and current
as the inclusive start and end. Category requests set shape=category, current and optional range_start. Series contain
only net sales; mark unsupported when another measure or an unsupported grouping is essential. Omit shape for a
follow-up that keeps prior shape; use summary for explicit single value or two-period comparison.
Use current_day_offset 0, -1 or -2 for explicit today, yesterday or day before yesterday. Omit current when using it.
If the user asks to compare with the day before the current date, set comparison_rule to previous_day and omit baseline.
If the user gives an absolute comparison period, set comparison_rule to fixed and include baseline. A date-only follow-up
retains the prior comparison rule. Omit baseline and comparison_rule unless explicitly changed. No invented store or date.
Do not copy prior fields into a new request. For revise omit unchanged fields; if a year is implicit and the other
explicit date or a valid prior period identifies its year, use that year. Store is the source identifier without 店/店铺.
For a follow-up that only changes scope, such as asking about another date with no different measure,
set operation to revise and requested to [] to retain prior.requested. Do not reduce it to the primary metric.
When the user explicitly changes which outputs to see, requested is the complete new list.
Allowed outputs: current, baseline, delta, rate, target, attainment, refund_members.
A period comparison asks for both amounts. '相比变化多少' means delta and rate.
'哪笔退款/差异来自哪些退款' also requests current, baseline, delta, rate, refund_members.
Only include target if user asks target amount, not when they only ask for attainment. Do not choose a definition.
Return one JSON object only, using null for unknown values."""
    previous = (
        dict({
            k: v
            for k, v in prior["scope"].items()
            if k in {"store", "current", "baseline", "comparison_rule", "definition"}
        }, requested=prior["request"]["outputs"], shape=prior["request"].get("shape"),
             range_start=prior["scope"].get("range_start"))
        if prior
        else None
    )
    message = await LLMFactory.create_llm(model).llm.ainvoke(
        [
            SystemMessage(content=instructions),
            HumanMessage(
                content=json.dumps(
                    {
                        "question": question,
                        "business_today": received_at.astimezone(ZoneInfo(topic.timezone)).date().isoformat(),
                        "definitions": definitions,
                        "prior": previous,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    raw = str(message.content).strip()
    if raw.startswith("```"):
        raw = (
            raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        )
    intent = Intent.model_validate_json(raw)
    intent.definition = explicitly_chosen(question, topic)
    return intent, message.usage_metadata


def _literal_store(store):
    # The contract rejects punctuation and control characters before this call.
    return "'" + store.replace("'", "''") + "'"


def _periods(scope):
    dates = [date.fromisoformat(scope["current"]).isoformat()]
    if scope["baseline"] and scope["baseline"] != scope["current"]:
        dates.append(date.fromisoformat(scope["baseline"]).isoformat())
    return ",".join(f"DATE '{value}'" for value in dates)


def _view(view):
    schema, table = view.split(".")
    return f'"{schema}"."{table}"'


def _query_parts(scope):
    return f"store_id = {_literal_store(scope['store'])} AND business_date IN ({_periods(scope)})"


def _summary_sql(topic, definition, scope, target_requested):
    view = _view(definition.view)
    filters = _query_parts(scope)
    if topic.layout == "store_day":
        target = ", target_amount AS target" if target_requested else ""
        return f"SELECT store_id, business_date, net_sales AS amount{target} FROM {view} WHERE {filters} ORDER BY business_date"
    return (
        f"SELECT store_id, business_date, SUM(signed_cents)::numeric / 100 AS amount "
        f"FROM {view} WHERE {filters} GROUP BY store_id, business_date ORDER BY business_date"
    )


def _series_query(topic, definition, scope, shape):
    fields = SummaryFields if topic.layout == "store_day" else EventsFields
    end = date.fromisoformat(scope["current"])
    start = date.fromisoformat(scope["range_start"]) if scope.get("range_start") else end
    where = (
        f"store_id = {_literal_store(scope['store'])} "
        f"AND business_date BETWEEN DATE '{start}' AND DATE '{end}'"
    )
    view = _view(definition.view)
    if shape == "category":
        sql = (
            "SELECT NULLIF(BTRIM(category), '') AS category, SUM(signed_cents)::numeric / 100 AS amount "
            f"FROM {view} WHERE {where} GROUP BY 1 ORDER BY 2 DESC, 1 NULLS LAST LIMIT {CategoryLimit + 1}"
        )
        return sql, fields | {"category"}
    expression = "net_sales" if topic.layout == "store_day" else "SUM(signed_cents)::numeric / 100"
    grouping = "" if topic.layout == "store_day" else " GROUP BY business_date"
    return (
        f"SELECT business_date, {expression} AS amount FROM {view} WHERE {where}{grouping} ORDER BY business_date",
        fields,
    )


def _save_series(row, state, data, definition, shape):
    query_data = data
    truncated = shape == "category" and len(data) > CategoryLimit
    if shape == "trend":
        start = date.fromisoformat(state["scope"]["range_start"])
        end = date.fromisoformat(state["scope"]["current"])
        daily = {item["business_date"]: item["amount"] for item in data}
        if len(daily) != len(data):
            raise ValueError("Expected one result per business date")
        dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        data = [{"business_date": day, "amount": daily.get(day)} for day in dates]
        dimension, label, chart_type = "business_date", "业务日", "line"
    else:
        data = data[:CategoryLimit]
        dimension, label, chart_type = "category", "品类", "bar"

    state["presentation"] = dict(
        presentation("unknown", ["CNY"]),
        shape=shape,
        unit="CNY",
        truncated=truncated,
        limit=CategoryLimit if shape == "category" else None,
        rows=[{"label": item[dimension], "value": item["amount"]} for item in data],
    )
    state["steps"].append(
        {"kind": shape, "status": "verified", "record_id": str(row.id),
         "definition": definition.id, "view": definition.view}
    )
    state["task_status"] = "completed"
    result = json.loads(row.data)
    result.update(data=data, query_data=query_data)
    row.data = orjson.dumps(result).decode()
    row.chart = orjson.dumps({
        "type": chart_type,
        "title": "每日净销售额" if shape == "trend" else "按品类查看净销售额",
        "axis": {"x": {"name": label, "value": dimension},
                 "y": {"name": "净销售额（元）", "value": "amount"}},
        "columns": [{"name": label, "value": dimension},
                    {"name": "净销售额（元）", "value": "amount"}],
    }).decode()


def _execute(session, row, datasource, sql, read_session, *, statement=None):
    row.sql = sql
    session.add(row)
    log = start_log(
        session,
        operate=OperationEnum.EXECUTE_SQL,
        record_id=row.id,
        local_operation=True,
    )
    try:
        result = exec_sql(datasource, sql, read_session=read_session, statement=statement)
    except Exception:
        trigger_log_error(session, log)
        end_log(session, log, {"error": "query_failed"})
        raise
    result["datasource"] = datasource.id
    row.data = orjson.dumps(result).decode()
    session.add(row)
    end_log(
        session, log, {"fields": result["fields"], "row_count": len(result["data"])}
    )
    return result["data"]


def _build_chart(row, state, topic, summary_rows, receipts):
    summary = {item["id"]: item for item in state["outputs"] if item["id"] in Labels}
    result = json.loads(row.data)
    if len({receipt["definition"] for receipt in receipts}) > 1:
        chart = {
            "type": "column",
            "title": "净销售额口径对比",
            "axis": {
                "x": {"name": "业务日", "value": "business_date"},
                "y": {"name": "净销售额（元）", "value": "amount"},
                "series": {"name": "口径", "value": "definition"},
            },
            "columns": [
                {"name": "业务日", "value": "business_date"},
                {"name": "口径", "value": "definition"},
                {"name": "净销售额（元）", "value": "amount"},
            ],
        }
        for source, receipt in zip(summary_rows, receipts, strict=True):
            source["definition"] = topic.by_id(receipt["definition"]).label
        result["data"] = summary_rows
        result["fields"] = list(summary_rows[0])
        state["presentation"] = presentation("alternative_definitions", ["CNY"])
    else:
        chart = {
            "type": "table",
            "title": "经营结果",
            "columns": [{"name": Labels[key], "value": key} for key in summary],
        }
        result["data"] = [{key: item.get("value") for key, item in summary.items()}]
        result["fields"] = list(summary)
        state["presentation"] = presentation(
            "unknown", [item["unit"] for item in summary.values()]
        )
    chart.update(state["presentation"])
    row.chart = orjson.dumps(chart).decode()
    # Preserve the query's own receipt as a separate object; the ordinary
    # display endpoint reads the shape declared by the saved chart.
    result["query_data"] = json.loads(row.data)["data"]
    row.data = orjson.dumps(result).decode()


def _members(
    session, user, row, datasource, topic, state, chat, receipts, read_session
):
    scope = state["scope"]
    step = {"kind": "refund_members", "status": "missing"}
    state["steps"].append(step)
    if not topic.evidence_view or scope["definition"] == topic.compare_definition:
        return {"status": "missing", "reason": "evidence_unavailable"}
    try:
        _require_assets(session, user, datasource, {topic.evidence_view: MemberFields})
        sql = (
            f"SELECT store_id, business_date, refund_id, line_id, paid_at_utc, refund_at_utc, "
            f"amount_cents, in_reference, in_cohort FROM {_view(topic.evidence_view)} "
            f"WHERE {_query_parts(scope)} ORDER BY business_date, refund_id LIMIT {MemberLimit + 1}"
        )
        child = _record(
            session,
            user,
            chat,
            "refund_members",
            state={"internal": True, "parent_record_id": str(row.id)},
        )
        step["record_id"] = str(child.id)
        items = _execute(session, child, datasource, sql, read_session)
        child.finish = True
        child.finish_time = datetime.now()
        session.add(child)
    except Exception as error:
        SQLBotLogUtil.warning(
            f"CBI refund evidence failed for {row.id}: {type(error).__name__}"
        )
        step["status"] = "failed"
        return {"status": "failed", "reason": "member_query_failed"}
    bound = {(item["definition"], item["date"]): item["amount"] for item in receipts}
    dates = {scope["current"], scope["baseline"]}
    differences = {
        day: comparison(
            bound.get((scope["definition"], day)),
            bound.get((topic.compare_definition, day)),
        )[0]
        for day in sorted(dates)
    }
    included = {(x["business_date"], x["refund_id"]) for x in items}
    if (
        len(items) > MemberLimit
        or len(included) != len(items)
        or any(not x["paid_at_utc"] for x in items)
    ):
        return {"status": "missing", "reason": "member_coverage"}
    totals = {
        day: sum(
            x["amount_cents"] * (int(x["in_reference"]) - int(x["in_cohort"]))
            for x in items
            if x["business_date"] == day
        )
        for day in dates
    }
    if any(
        amount is None or amount * 100 != totals[day]
        for day, amount in differences.items()
    ):
        return {"status": "missing", "reason": "member_reconciliation"}
    step["status"] = "verified"
    step["member_count"] = len(items)
    reference_totals = {
        day: bound.get((topic.compare_definition, day)) for day in sorted(dates)
    }
    return {
        "status": "verified",
        "references": [{"record_id": step["record_id"], "field": "refund_id"}],
        "members": items,
        "differences": {day: number(amount) for day, amount in differences.items()},
        "reference_totals": reference_totals,
        "reference_label": topic.by_id(topic.compare_definition).label,
    }


def _new_execution(
    session, user, chat, parent, state, request_id, *, keep_partial=False, child_state=None
):
    child_state = child_state or fork_run(state, parent.id)
    child = _record(
        session,
        user,
        chat,
        child_state["request"]["text"],
        state=child_state,
        model_id=parent.ai_modal_id,
    )
    if keep_partial:
        state["task_status"] = "partial"
    state["events"] = [
        dict(event, result_record_id=str(child.id))
        if event["request_id"] == request_id
        else event
        for event in state["events"]
    ]
    if child_state.get("investigation") and child_state["investigation"].get("steps"):
        child.sql, child.data, child.chart = parent.sql, parent.data, parent.chart
    _save(session, parent, state)
    return child



def _configure_investigation(topic, plan, state, prior, parent_id, intent):
    policy = topic.semantic.decomposition
    eligible = prior and prior.get("source_proof") and prior.get("task_status") == "completed"
    original = (prior or {}).get("semantic_plan", {})
    resolved = plan["metrics"]
    if not eligible or not parent_id or not original.get("compare_start") or not plan.get("compare_start"):
        raise cbi_semantic.PlanRejected("investigation_parent_required")
    if not policy or len(resolved) != 1 or resolved[0] not in policy.metrics:
        raise cbi_semantic.PlanRejected("investigation_method_unavailable")
    metric = resolved[0]
    if metric not in {item["key"] for item in _eligible_investigation(prior, topic)}:
        raise cbi_semantic.PlanRejected("investigation_metric_required")
    original_scope = cbi_investigation.source_scope(original)
    next_scope = cbi_investigation.source_scope(plan)
    if not cbi_investigation.narrows_scope(original_scope, next_scope):
        raise cbi_semantic.PlanRejected("semantic_scope")
    state.update(
        investigation=cbi_investigation.new_record(
            plan, members=intent.investigate and intent.investigation_members,
            refund=intent.requires_refund_evidence,
            execution_scope="investigation" if intent.investigate else "single_partition",
        ),
        source_expectation=prior["source_proof"],
    )
    state["investigation"]["comparison_record_id"] = str(parent_id)
    if cbi_investigation.narrows_scope(next_scope, original_scope):
        breakdown = prior["presentation"].get("decomposition")
        exact = breakdown["total"] if breakdown else prior.get("comparison_totals", {}).get(metric)
        if exact:
            state["investigation"]["parent_total"] = exact
        else:
            # Earlier comparison receipts stored summary values at JSON-number precision.
            values = {entry["id"]: entry["value"] for entry in prior["outputs"]}
            state["investigation"].update(
                parent_total=cbi_investigation.comparison_total(
                    values[metric + "_current"], values[metric + "_baseline"]
                ),
                recorded_precision=True,
            )
    state["request"]["parent_record_id"] = str(parent_id)


def _normalize_investigation_intent(intent, prior, question, *, selected):
    old = (prior or {}).get("semantic_plan") or {}
    if not selected or intent.operation != "revise" or not old.get("compare_start"):
        return intent
    changes = {}
    compare = (intent.compare_start, intent.compare_end)
    if intent.comparison_action == "keep" and all(compare) and [
        item.isoformat() for item in compare
    ] == [old["compare_start"], old["compare_end"]]:
        changes.update(compare_start=None, compare_end=None)
    if "抵消" in question and not intent.detail and intent.group in {None, "none"}:
        changes["investigate"] = True
    return intent.model_copy(update=changes) if changes else intent


def _refund_facts_unavailable(state, prior, parent_id):
    """Only read known values from the explicit comparison and declare the evidence gap."""
    prior_plan = (prior or {}).get("semantic_plan")
    if prior_plan and prior_plan.get("compare_start"):
        state.update(scope=deepcopy(prior["scope"]), outputs=deepcopy(prior["outputs"]),
                     semantic_plan=deepcopy(prior_plan), steps=deepcopy(prior["steps"]),
                     presentation=deepcopy(prior["presentation"]))
        state["request"]["parent_record_id"] = str(parent_id)
    plan = prior_plan or {"group": "none"}
    state["investigation"] = cbi_investigation.new_record(plan, refund=True)
    cbi_investigation.finish(state, "partial" if state["outputs"] else "blocked", "refund_facts_unavailable")
    state["decision"] = {"kind": "unsupported", "reason": "refund_facts_unavailable"}
    return state


def _semantic_state(topic, state, intent, prior, parent_id, *, selected=False):
    period_keys = {"start", "end", "compare_start", "compare_end"}
    model_windows = intent.model_dump(mode="json", include=period_keys)
    period_basis = "model"
    question_text = state["request"].get("text", "")
    try:
        original = (prior or {}).get("semantic_plan")
        fixed_members = cbi_semantic.requested_prior_members(
            question_text, topic.semantic, prior
        ) if selected and original else None
        if selected and original and cbi_semantic.MONTH_ONLY_REVISION.fullmatch(question_text):
            if not intent.start or not intent.end:
                raise cbi_semantic.PlanRejected("semantic_scope")
            intent = cbi_semantic.SemanticIntent(operation="revise", start=intent.start, end=intent.end)
        elif fixed_members is not None:
            if intent.operation == "unsupported":
                raise cbi_semantic.PlanRejected("semantic_scope")
            if intent.group not in {None, original["group"]}:
                raise cbi_semantic.PlanRejected("semantic_scope")
            intent = intent.model_copy(update={
                "operation": "revise", "group": original["group"],
                "filters": {**(intent.filters or {}), **fixed_members},
            })
        if intent.operation == "new" or intent.comparison_direction_uncertain:
            intent, period_basis = cbi_semantic.resolve_comparison_direction(
                question_text, intent, {m.label for m in topic.semantic.metrics.values()},
            )
        if selected and intent.operation == "revise" and cbi_semantic.requests_all_groups(
            question_text, topic.semantic, intent.group or (original or {}).get("group")
        ):
            intent = intent.model_copy(update={"top": None})
        if intent.investigate and intent.operation != "unsupported":
            policy = topic.semantic.decomposition
            if not policy:
                raise cbi_semantic.PlanRejected("investigation_method_unavailable")
            old = (prior or {}).get("semantic_plan", {})
            available = [item for item in old.get("metrics", []) if item in policy.metrics]
            if not intent.metrics and len(available) > 1:
                raise cbi_semantic.PlanRejected("investigation_metric_required")
            if intent.operation != "revise" or not selected or not old.get("compare_start"):
                raise cbi_semantic.PlanRejected("investigation_parent_required")
            group = intent.group if intent.group not in {None, "none"} else (
                old.get("group") if old.get("group") in policy.dimensions else next(iter(policy.dimensions), None)
            )
            if group not in policy.dimensions:
                raise cbi_semantic.PlanRejected("investigation_method_unavailable")
            intent = intent.model_copy(update={
                "group": group,
                "metrics": intent.metrics or available,
                "top": max(intent.top or 25, cbi_investigation.CANDIDATE_LIMIT),
                "detail": False,
                "overall_metrics": None,
                "totals": False,
            })
        plan = cbi_semantic.resolve(topic.semantic, intent, original)
        selected_comparison = (
            selected and intent.operation == "revise" and original
            and plan["compare_start"] and plan["group"] != "none"
            and set(plan["metrics"]) <= set(original["metrics"])
            and cbi_investigation.same_periods(original, plan)
        )
        if intent.investigate or selected_comparison:
            _configure_investigation(topic, plan, state, prior, parent_id, intent)
    except cbi_semantic.PlanRejected as rejected:
        decision = {"kind": "unsupported", "reason": rejected.reason}
        if rejected.reason == "semantic_unsupported" and _sample_payment_fact_missing(topic, state["request"]["text"], intent):
            decision["missing_fact"] = "payment"
        state["decision"] = decision
        if rejected.reason == "semantic_scope" and (
            model_windows["compare_start"] or intent.comparison_direction_uncertain
        ):
            state["comparison_resolution"] = {
                "basis": "confirmation_required", "model_windows": model_windows, "adopted_windows": None,
            }
        return state
    definition = topic.by_id(topic.default_definition)
    state.update(
        semantic_plan=plan, task_status="running", decision={"kind":"ready"},
        scope={
            "current": str(date.fromisoformat(plan["end"]) - timedelta(days=1)),
            "range_start": plan["start"], "range_end_exclusive": plan["end"],
            "baseline": plan["compare_start"], "baseline_end_exclusive": plan["compare_end"],
            "definition":definition.id,"definition_label":definition.label,
            "timezone":topic.timezone,
            "filter_labels":[{"label":topic.semantic.dimensions[k].label,"value":", ".join(map(str,v)) if isinstance(v,list) else v}
                             for k,v in plan["filters"].items()],
        },
    )
    if intent.operation == "revise" and prior:
        state["request"]["parent_record_id"] = str(parent_id)
    if plan["compare_start"]:
        adopted_windows = {key: plan[key] for key in period_keys}
        state["comparison_resolution"] = {
            "basis": "selected_context" if intent.operation == "revise" else period_basis,
            "model_windows": model_windows,
            "adopted_windows": adopted_windows,
            "normalized": intent.operation == "new" and model_windows != adopted_windows,
        }
    return state


def _sample_payment_fact_missing(topic, question, intent):
    # This certified sample view contains billed orders, with no payment records.
    if topic.by_id(topic.default_definition).view != "demo_bi.order_analysis":
        return False
    pattern = r"支付\s*(?:转化|转换|轉化|轉換)率|\bpayment\s+conversion\s+rate\b"
    refs = [ref for ref in intent.metric_references if re.search(pattern, ref.quote, re.IGNORECASE)]
    if refs:
        return any(ref.role == "requested" and ref.quote in question for ref in refs)
    return bool(re.search(pattern, question, re.IGNORECASE))


class QualityBlocked(Exception):
    pass


def _run_quality_check(session, user, row, state, topic, datasource, read_session,
                       plan, definition, allowed_fields, before_query):
    from apps.chat.task import cbi_quality

    checked_at = datetime.now(UTC)
    started = monotonic()
    receipts = []

    def dispatch():
        if before_query is not None:
            if not before_query():
                raise TimeoutError("quality execution deferred")
            return
        remaining = cbi_investigation.time_remaining(started)
        if remaining < 1:
            raise TimeoutError("quality budget exceeded")
        read_session.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                             {"timeout": str(min(20000, int(remaining * 1000)))})

    def execute(statement, *, sql, fields):
        receipt = _semantic_internal(session, user, row, "数据检查")
        receipts.append(receipt)
        step = {"kind": "quality", "status": "incomplete", "record_id": str(receipt.id),
                "definition": definition.id, "view": definition.view, "fields": sorted(fields),
                "purpose": "quality_check"}
        state["steps"].append(step)
        with read_session.begin_nested():
            data = _execute(session, receipt, datasource, sql, read_session, statement=statement)
        step["status"] = "verified"
        return data

    try:
        report = cbi_quality.run_quality(
            read_session, topic.semantic, definition.view, allowed_fields=allowed_fields,
            plan=plan, before_query=dispatch, execute=execute,
            checked_on=(checked_at if topic.timezone == "source_calendar" else checked_at.astimezone(ZoneInfo(topic.timezone))).date(),
        )
    except (SQLAlchemyError, TimeoutError, ValueError) as error:
        SQLBotLogUtil.warning(f"CBI quality check incomplete for {row.id}: {type(error).__name__}")
        report = {"status": "incomplete", "results": []}
    disposition = cbi_quality_runtime.decide(topic.semantic, plan, report)
    try:
        if disposition.plan:
            dispatch()
    except (SQLAlchemyError, TimeoutError) as error:
        SQLBotLogUtil.warning(f"CBI quality protected query deferred for {row.id}: {type(error).__name__}")
        report = {**report, "status": "incomplete", "results": []}
        disposition = cbi_quality_runtime.decide(topic.semantic, plan, report)
    if not receipts and report["status"] != "not_applicable":
        receipts.append(_semantic_internal(session, user, row, "数据检查"))
    if receipts:
        receipts[0].analysis_state = {
            "internal": True,
            "quality_report": {**report, "config_version": topic.version,
                               "source_version": topic.source_version, "checked_at": checked_at.isoformat(),
                               "read_basis": "postgres_repeatable_read",
                               "query_records": [str(item.id) for item in receipts]},
        }
        session.add(receipts[0])
    public = cbi_quality_runtime.public_evidence(
        report, disposition, [str(receipts[0].id)] if receipts else [], checked_at.isoformat()
    )
    cbi_quality_runtime.record_evidence(state, plan, public)
    return disposition


def _semantic_materialize(session, user, row, state, topic, datasource, read_session, plan,
                          *, before_query=None):
    definition = topic.by_id(state["scope"]["definition"])
    statement, sql, fields = cbi_semantic.build_query(topic.semantic, definition.view, plan)
    unavailable = []
    if topic.semantic.quality:
        from apps.chat.task import cbi_quality

        quality_fields = cbi_quality.quality_required_fields(topic.semantic, plan)
        permitted = _require_assets(session, user, datasource, {definition.view: fields | quality_fields})
        disposition = _run_quality_check(session, user, row, state, topic, datasource, read_session,
                                         plan, definition, permitted[definition.view], before_query)
        unavailable = disposition.unavailable
        if disposition.plan is None:
            raise QualityBlocked
        if disposition.plan != plan:
            plan = disposition.plan
            statement, sql, fields = cbi_semantic.build_query(topic.semantic, definition.view, plan)
    else:
        _require_assets(session, user, datasource, {definition.view: fields})
    raw = _execute(session, row, datasource, sql, read_session, statement=statement)
    view, outputs = cbi_semantic.present(topic.semantic, plan, raw)
    if decomposed := view.get("decomposition"):
        decomposed["children"] = [
            {"key": key, "label": topic.semantic.dimensions[key].label}
            for key in topic.semantic.decomposition.dimensions[plan["group"]]
        ]
    step = {"kind": "semantic", "status": "verified", "record_id": str(row.id),
            "view": definition.view, "definition": definition.id, "fields": sorted(fields)}
    state["steps"].append(step)
    return view, [*outputs, *cbi_quality_runtime.missing_outputs(unavailable)], raw


def _write_semantic_result(row, state, view, outputs, raw):
    column_specs = view["table"]["columns"]
    receipt = json.loads(row.data)
    receipt.update(data=view["table"]["rows"], query_data=raw, fields=[c["key"] for c in column_specs])
    row.data = orjson.dumps(receipt).decode()
    config = {
        "type": {"trend":"line","category":"bar"}.get(view.get("shape"),"table"),
        "title": view["title"],
        "columns": [{"name":c["label"],"value":c["key"]} for c in column_specs],
    }
    if view.get("shape"):
        key = next(c["key"] for c in column_specs if c["numeric"])
        config["axis"] = {"x":config["columns"][0],
                          "y":next(c for c in config["columns"] if c["value"]==key)}
    row.chart = orjson.dumps(config).decode()
    state.update(presentation=view, outputs=outputs)


def _semantic_internal(session, user, row, title):
    chat = session.get(Chat, row.chat_id)
    return _record(
        session, user, chat, title, state={"internal": True}, model_id=row.ai_modal_id
    )


def _semantic_proof(session, user, row, state, topic, datasource, read_session, scope):
    definition = topic.by_id(state["scope"]["definition"])
    statement, sql, fields = cbi_investigation.source_query(topic.semantic, definition.view, scope)
    _require_assets(session, user, datasource, {definition.view: fields})
    evidence = _semantic_internal(session, user, row, "比较来源核验")
    raw = _execute(session, evidence, datasource, sql, read_session, statement=statement)
    state["steps"].append({
        "kind": "semantic", "status": "verified", "record_id": str(evidence.id),
        "definition": definition.id, "view": definition.view, "fields": sorted(fields),
        "purpose": "source_proof",
    })
    if len(raw) != 1 or raw[0]["rows"] > cbi_investigation.SOURCE_ROW_LIMIT:
        return None
    return {
        "view": definition.view, "scope": scope, "rows": raw[0]["rows"],
        "digest": raw[0]["digest"], "source_version": topic.source_version,
    }


def _eligible_investigation(state, topic):
    policy = topic.semantic.decomposition
    plan = state["semantic_plan"]
    if not policy or not plan["compare_start"] or plan["detail"]:
        return []
    breakdown = state["presentation"].get("decomposition")
    if breakdown and breakdown["reconciled"]:
        return [{"key": breakdown["metric"], "label": breakdown["label"]}]
    verified = {item["id"] for item in state["outputs"]
                if item["status"] == "verified" and item.get("value") is not None}
    return [{"key": key, "label": topic.semantic.metrics[key].label}
            for key in plan["metrics"] if key in policy.metrics
            and {key + "_current", key + "_baseline"} <= verified]


def _run_semantic(session, user, row, state, topic, datasource, read_session):
    plan = state["semantic_plan"]
    view, outputs, raw = _semantic_materialize(
        session, user, row, state, topic, datasource, read_session, plan
    )
    _write_semantic_result(row, state, view, outputs, raw)
    complete = (view.get("decomposition", {}).get("reconciled", True)
                and not state.get("quality", {}).get("unavailable_outputs"))
    state.update(task_status="completed" if complete else "partial",
                 data_version=topic.source_version if topic.asset_id else topic.source_version or topic.version)
    available = _eligible_investigation(state, topic) if complete else []
    if available:
        if plan["group"] == "none":
            by_period = {row["period"]: row for row in raw}
            state["comparison_totals"] = {}
            for item in available:
                key = "_comparison_exact_" + item["key"]
                current = by_period["current"].get(key)
                baseline = by_period["baseline"].get(key)
                if current is not None and baseline is not None:
                    state["comparison_totals"][item["key"]] = cbi_investigation.comparison_total(current, baseline)
        scope = cbi_investigation.source_scope(plan)
        try:
            proof = _semantic_proof(session, user, row, state, topic, datasource, read_session, scope)
        except Exception as error:
            SQLBotLogUtil.warning(f"CBI source verification failed for {row.id}: {type(error).__name__}")
            proof = None
        if proof:
            state.update(source_proof=proof, available_investigation=available)


def _decomposition_matches(result, expected, *, recorded_precision=False):
    if not result["reconciled"]:
        return False
    with localcontext() as context:
        context.prec = 120
        tolerance = Decimal(result.get("coverage", {}).get("tolerance", "0"))
        # Historical rounded numbers can bind their two observed periods, from
        # which the signed change follows. New receipts bind all three exactly.
        parts = ("current", "baseline") if recorded_precision else ("current", "baseline", "delta")
        for key in parts:
            actual = result["total"].get(key)
            if actual is None or expected.get(key) is None:
                return False
            if recorded_precision:
                actual = float(actual)
            if abs(Decimal(str(actual)) - Decimal(str(expected[key]))) > tolerance:
                return False
        return True


def _record_investigation_step(session, user, row, state, topic, datasource, read_session, step, dispatch):
    plan = step["query_plan"]
    quality_budget = {"before_query": dispatch} if topic.semantic.quality else {}
    if step["kind"] == "members":
        tables = step.setdefault("tables", [])
        references = step.setdefault("records", [])
        for period in ("current", "baseline"):
            if any(table["period"] == period for table in tables):
                continue
            if not dispatch():
                return None
            start = plan["start"] if period == "current" else plan["compare_start"]
            end = plan["end"] if period == "current" else plan["compare_end"]
            scoped = dict(plan, start=start, end=end, compare_start=None, compare_end=None,
                          group="none", detail=True, top=50)
            item = _semantic_internal(session, user, row, "调查成员")
            view, _, _ = _semantic_materialize(session, user, item, state, topic, datasource, read_session, scoped, **quality_budget)
            tables.append({"period": period, **view["table"], "truncated": view["truncated"]})
            references.append(str(item.id))
            _save(session, row, deepcopy(state), finished=False)
        step.update(status="verified", record_id=references[0], records=references, tables=tables, table=tables[0])
        return True
    if not dispatch():
        return None
    item = _semantic_internal(session, user, row, "调查下钻")
    view, _, _ = _semantic_materialize(session, user, item, state, topic, datasource, read_session, plan, **quality_budget)
    result = view["decomposition"]
    passed = _decomposition_matches(result, step["parent_total"])
    step.update(status="verified" if passed else "failed", record_id=str(item.id),
                decomposition=result)
    return passed


def _investigation_source(session, user, row, state, topic, datasource, read_session, dispatch):
    if not dispatch():
        return False
    expected = state["source_expectation"]
    prior_narrowed_proof = state.get("source_proof")
    scope = cbi_investigation.source_scope(state["semantic_plan"])
    checking = _semantic_proof(session, user, row, state, topic, datasource, read_session, expected["scope"])
    if not checking:
        cbi_investigation.finish(state, "partial", "source_unavailable")
        return False
    if not cbi_investigation.same_source(expected, checking):
        state["investigation"]["source_confirmation"] = {"id": uuid4().hex, "proof": checking}
        cbi_investigation.finish(state, "partial", "source_changed")
        return False
    proof = checking
    if scope != expected["scope"]:
        if not dispatch():
            return False
        proof = _semantic_proof(session, user, row, state, topic, datasource, read_session, scope)
    if not proof or (
        prior_narrowed_proof
        and prior_narrowed_proof["scope"] == scope != expected["scope"]
        and not cbi_investigation.same_source(prior_narrowed_proof, proof)
    ):
        cbi_investigation.finish(state, "partial", "source_unavailable")
        return False
    state.update(source_proof=proof, data_version=topic.source_version if topic.asset_id else topic.source_version or topic.version)
    return True


def _run_investigation(session, user, row, state, topic, datasource, read_session, stop):
    started = monotonic()
    investigation = state["investigation"]
    plan = state["semantic_plan"]
    def dispatch():
        if stop and stop.is_set():
            cbi_investigation.finish(state, "partial", "stopped")
            return False
        remaining = cbi_investigation.time_remaining(started)
        if remaining < 1:
            cbi_investigation.finish(state, "partial", "budget_exhausted")
            return False
        read_session.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                             {"timeout": str(min(20000, int(remaining * 1000)))})
        return True

    quality_budget = {}
    if topic.semantic.quality:
        from apps.chat.task import cbi_quality

        view = topic.by_id(state["scope"]["definition"]).view
        required = cbi_quality.quality_required_fields(topic.semantic, plan)
        _require_assets(session, user, datasource, {view: required | topic.semantic.fields})
        quality_budget["before_query"] = dispatch
    if not _investigation_source(session, user, row, state, topic, datasource, read_session, dispatch):
        return
    if not investigation["steps"]:
        parent_id = investigation.get("comparison_record_id") or state["request"].get("parent_record_id")
        parent = session.get(ChatRecord, int(parent_id)) if parent_id else None
        original = parent.analysis_state if parent else {}
        if cbi_investigation.same_source(original.get("source_proof"), state["source_proof"]):
            ids = {plan["metrics"][0] + "_" + part for part in ("current", "baseline", "delta", "rate")}
            state["outputs"] = [entry for entry in original["outputs"] if entry["id"] in ids]
        if not dispatch():
            return
        view, outputs, raw = _semantic_materialize(
            session, user, row, state, topic, datasource, read_session, plan, **quality_budget
        )
        result = view["decomposition"]
        expected = investigation.get("parent_total")
        passed = result["reconciled"] and (
            expected is None or _decomposition_matches(
                result, expected, recorded_precision=investigation.get("recorded_precision", False)
            )
        )
        first = {"id": "decomposition", "kind": "decomposition", "status": "verified" if passed else "failed",
                 "dimension": plan["group"], "label": result["dimension_label"], "record_id": str(row.id)}
        if not passed:
            # The first query remains available as a receipt, but can be checked again.
            investigation["first_failure"] = first
            state["steps"][-1]["status"] = "failed"
            cbi_investigation.finish(state, "partial", "decomposition_unreconciled")
            return
        _write_semantic_result(row, state, view, outputs, raw)
        investigation.pop("first_failure", None)
        investigation["steps"] = [first]
        if investigation["plan"].get("execution_scope") == "single_partition":
            state["available_investigation"] = _eligible_investigation(state, topic)
            cbi_investigation.finish(state, "completed", "plan_complete")
            return
        investigation["steps"].extend(
            cbi_investigation.scheduled_steps(topic.semantic, plan, result, investigation)
        )
        investigation["findings"] = [
            cbi_investigation.fact(item, plan, result["metric"], result["unit"], str(row.id))
            for item in cbi_investigation.candidates(result)
        ]
        _save(session, row, deepcopy(state), finished=False)
    for step in investigation["steps"]:
        if step["status"] == "verified":
            continue
        step_result = _record_investigation_step(session, user, row, state, topic, datasource, read_session, step, dispatch)
        if step_result is None:
            return
        if not step_result:
            cbi_investigation.finish(state, "partial", "decomposition_unreconciled")
            return
        _save(session, row, deepcopy(state), finished=False)
    if investigation["plan"]["needs_refund_evidence"]:
        cbi_investigation.finish(state, "partial", "refund_facts_unavailable")
    else:
        cbi_investigation.finish(
            state, "completed", "plan_complete" if investigation["findings"] else "no_nonzero_groups"
        )


def _run(record_id, user, *, evidence_only=False, publication_confirmed=False, stop=None, request_id=None):
    with Session(engine) as session:
        row = session.get(ChatRecord, record_id)
        chat = session.get(Chat, row.chat_id)
        state = deepcopy(row.analysis_state)
        try:
            topic, datasource = _accessible_topic(session, user, chat, version=state["config_version"])
        except HTTPException as error:
            if error.detail in {"cbi_theme_unavailable", "cbi_theme_version_unavailable"}:
                state["decision"] = {"kind":"unsupported","reason":"semantic_configuration_unavailable"}
            topic = datasource = None
        if not topic or topic.version != state["config_version"]:
            _save(
                session,
                row,
                dict(state, task_status="blocked", revision=state["revision"] + 1),
            )
            session.refresh(row)
            session.expunge(row)
            return row
        if topic.semantic:
            try:
                with get_session(datasource) as read_session:
                    read_session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                    read_session.execute(text("SET LOCAL statement_timeout = '20s'"))
                    if state.get("investigation"):
                        _run_investigation(session, user, row, state, topic, datasource, read_session, stop)
                    else:
                        _run_semantic(session, user, row, state, topic, datasource, read_session)
            except QualityBlocked:
                unavailable = state["quality"]["unavailable_outputs"]
                has_partition = bool(state.get("presentation", {}).get("decomposition"))
                if not has_partition:
                    state["outputs"] = cbi_quality_runtime.missing_outputs(unavailable)
                if state.get("investigation"):
                    cbi_investigation.finish(state, "partial" if has_partition else "blocked",
                                             unavailable[0]["reason"])
                else:
                    state["task_status"] = "blocked"
            except Exception as error:
                SQLBotLogUtil.warning(f"CBI semantic query failed for {row.id}: {type(error).__name__}")
                denied = isinstance(error, HTTPException) and error.detail in {
                    "analysis_asset_not_available", "analysis_row_policy_unsupported"
                }
                if denied:
                    state["decision"] = {"kind": "unsupported", "reason": "semantic_access_denied"}
                if state.get("investigation"):
                    failure = next((step for step in state["investigation"]["steps"] if step["status"] != "verified"), None)
                    if failure:
                        failure.update(status="failed", reason="query_failed")
                    cbi_investigation.finish(state, "partial" if state.get("outputs") else "blocked", "query_failed")
                elif not denied:
                    state.update(task_status="blocked", decision={"kind":"unsupported","reason":"semantic_query_failed"})
                else:
                    state["task_status"] = "blocked"
            _save(session, row, dict(state, revision=state["revision"]+1))
            session.refresh(row)
            session.expunge(row)
            return row
        with get_session(datasource) as read_session:
            read_session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            version = None
            if topic.publication_view:
                try:
                    _require_assets(
                        session, user, datasource, {topic.publication_view: {"version"}}
                    )
                    versions = exec_sql(
                        datasource,
                        f"SELECT version FROM {_view(topic.publication_view)} LIMIT 2",
                        read_session=read_session,
                    )["data"]
                    if len(versions) == 1:
                        version = versions[0]["version"]
                    else:
                        raise ValueError("Expected one publication version")
                except Exception:
                    state["task_status"] = (
                        "partial" if state.get("summary_receipts") else "blocked"
                    )
                    state["steps"].append(
                        {
                            "kind": "publication",
                            "status": "failed",
                            "reason": "publication_unavailable",
                        }
                    )
                    state["revision"] += 1
                    _save(session, row, state)
                    session.refresh(row)
                    session.expunge(row)
                    return row
            expected = (state.get("publication_confirmation") or {}).get("version")
            if (evidence_only and not evidence_can_reuse(state, version)) or (
                publication_confirmed and (version is None or version != expected)
            ):
                if version is None:
                    state.update(task_status="partial", revision=state["revision"] + 1)
                    state["decision"] = {"kind": "ready", "reason": "publication_unavailable"}
                else:
                    state = offer_publication_confirmation(state, version)
                _save(session, row, state)
                session.refresh(row)
                session.expunge(row)
                return row
            if publication_confirmed:
                state.pop("publication_confirmation", None)
                row = _new_execution(
                    session, user, chat, row, state, request_id, keep_partial=True
                )
                state = row.analysis_state
            state["data_version"] = version
            scope = state["scope"]
            need_members = "refund_members" in state["request"]["outputs"]
            receipts = state.get("summary_receipts", [])
            summary_rows = []
            target_requested = bool(
                {"target", "attainment"}.intersection(state["request"]["outputs"])
            )
            shape = state["request"].get("shape", "summary")
            try:
                if shape != "summary":
                    chosen = topic.by_id(scope["definition"])
                    sql, fields = _series_query(topic, chosen, scope, shape)
                    _require_assets(session, user, datasource, {chosen.view: fields})
                    data = _execute(session, row, datasource, sql, read_session)
                    _save_series(row, state, data, chosen, shape)
                    _save(session, row, dict(state, revision=state["revision"] + 1))
                    session.refresh(row)
                    session.expunge(row)
                    return row
                if not evidence_only:
                    chosen = topic.by_id(scope["definition"])
                    definitions = [chosen]
                    if (
                        need_members
                        and topic.compare_definition
                        and topic.compare_definition != chosen.id
                    ):
                        definitions.append(topic.by_id(topic.compare_definition))
                    for index, definition in enumerate(definitions):
                        if stop and stop.is_set():
                            break
                        fields = (
                            SummaryFields
                            if topic.layout == "store_day"
                            else EventsFields
                        )
                        if topic.layout == "store_day" and target_requested:
                            fields = fields | {"target_amount"}
                        _require_assets(
                            session, user, datasource, {definition.view: fields}
                        )
                        query_row = (
                            row
                            if index == 0
                            else _record(
                                session,
                                user,
                                chat,
                                "reference_summary",
                                state={
                                    "internal": True,
                                    "parent_record_id": str(row.id),
                                },
                            )
                        )
                        sql = _summary_sql(topic, definition, scope, target_requested)
                        data = _execute(
                            session, query_row, datasource, sql, read_session
                        )
                        if index:
                            query_row.finish = True
                            query_row.finish_time = datetime.now()
                            session.add(query_row)
                        summary_rows.extend(data)
                        receipts.extend(
                            {
                                "record_id": str(query_row.id),
                                "definition": definition.id,
                                "store": item["store_id"],
                                "date": item["business_date"],
                                "amount": item["amount"],
                                "target": item.get("target"),
                            }
                            for item in data
                        )
                        state["steps"].append(
                            {
                                "kind": "summary",
                                "status": "verified",
                                "record_id": str(query_row.id),
                                "definition": definition.id,
                                "view": definition.view,
                            }
                        )
                    state["summary_receipts"] = receipts
                evidence = (
                    _members(
                        session,
                        user,
                        row,
                        datasource,
                        topic,
                        state,
                        chat,
                        receipts,
                        read_session,
                    )
                    if need_members and not (stop and stop.is_set())
                    else None
                )
                if evidence:
                    state["evidence"] = evidence
                state["outputs"] = outputs(state, receipts, evidence)
                state["task_status"] = completion(state["outputs"])
                if row.data and not evidence_only:
                    _build_chart(row, state, topic, summary_rows, receipts)
            except Exception as exc:
                SQLBotLogUtil.warning(
                    f"CBI analysis failed for {row.id}: {type(exc).__name__}"
                )
                state["outputs"] = outputs(state, receipts) if shape == "summary" else []
                state["task_status"] = completion(state["outputs"]) if shape == "summary" else "blocked"
                state["steps"].append(
                    {"kind": shape, "status": "failed", "reason": "query_failed"}
                )
            if stop and stop.is_set() and state["task_status"] != "completed":
                state["task_status"] = "stopped"
            state["revision"] += 1
            _save(session, row, state)
            session.refresh(row)
            session.expunge(row)
            return row


def _response_events(
    row, *, replace_pending=False, brief=None, forked=False, parent=None
):
    def event(type_, **payload):
        return b"data:" + orjson.dumps({"type": type_, **payload}) + b"\n\n"

    yield event("id", id=row.id, replace_pending=replace_pending, forked=forked)
    if brief:
        yield event("brief", brief=brief)
    if row.chart:
        yield event("sql", content=row.sql)
        yield event("sql-data", id=row.id)
        yield event("chart", content=row.chart)
    yield event("analysis-state", record_id=str(row.id), state=row.analysis_state)
    if parent:
        yield event(
            "analysis-parent-state",
            record_id=str(parent.id),
            state=parent.analysis_state,
        )
    yield event("finish", id=row.id)


def _investigation_action(row, state, topic, action, source):
    updated, changed = accept_investigation_action(state, action, source)
    if not changed:
        return updated, False, None
    child = None
    if action.action == "start_investigation":
        current = state["semantic_plan"]
        if not any(item["key"] == action.choice_id for item in state.get("available_investigation", [])):
            raise ActionRejected("investigation_metric_required")
        child = resolve_request("分析变化", Intent(operation="unsupported"), topic)
        intent = cbi_semantic.SemanticIntent(
            operation="revise", investigate=True, metrics=[action.choice_id],
            group=current["group"], top=25
        )
        child = _semantic_state(topic, child, intent, state, row.id, selected=True)
        if child["task_status"] != "running":
            raise ActionRejected(child["decision"]["reason"])
    elif action.action == "resume_investigation":
        child = deepcopy(state)
        child.update(revision=1, events=[], task_status="running")
        child["investigation"].update(status="running", stop_reason=None)
        child["request"] = {
            key: value for key, value in child["request"].items()
            if key not in {"client_request_id", "selected_parent"}
        }
        child["request"]["parent_record_id"] = str(row.id)
        updated["task_status"] = state["task_status"]
        updated["investigation"] = state["investigation"]
    elif action.action == "confirm_investigation_source":
        proof = state["investigation"]["source_confirmation"]["proof"]
        child = fork_run(state, row.id)
        child.update(
            config_digest=state.get("config_digest"),
            semantic_plan=state["semantic_plan"],
            investigation=cbi_investigation.new_record(
                state["semantic_plan"],
                members=state["investigation"]["plan"]["needs_members"],
                refund=state["investigation"]["plan"]["needs_refund_evidence"],
                execution_scope=state["investigation"]["plan"].get("execution_scope", "investigation"),
            ),
            source_expectation=proof,
        )
        if origin := state["investigation"].get("comparison_record_id"):
            child["investigation"]["comparison_record_id"] = origin
    return updated, True, child



def _apply(user, record_id, action, source):
    with Session(engine) as session:
        row = session.scalars(
            select(ChatRecord).where(ChatRecord.id == record_id).with_for_update()
        ).first()
        if (
            not row
            or row.create_by != user.id
            or not row.analysis_state
            or row.analysis_state.get("internal")
        ):
            raise HTTPException(404, "analysis_not_found")
        chat = session.get(Chat, row.chat_id)
        topic, _ = _accessible_topic(session, user, chat, version=row.analysis_state["config_version"])
        if (
            not topic
            or row.datasource != chat.datasource
            or topic.version != row.analysis_state["config_version"]
        ):
            raise HTTPException(409, "analysis_source_changed")
        check_saved_access(session, user, row)
        child_state = None
        try:
            if action.action in InvestigationActions:
                if not topic.semantic:
                    raise ActionRejected("investigation_method_unavailable")
                state, changed, child_state = _investigation_action(
                    row, row.analysis_state, topic, action, source
                )
            elif action.action == "confirm_definition":
                if action.choice_id not in {
                    definition.id for definition in topic.definitions
                }:
                    raise ActionRejected("invalid_option")
                state, changed = accept_confirmation(row.analysis_state, action, source)
            elif action.action in {"confirm_publication", "cancel_publication"}:
                state, changed = accept_publication_action(row.analysis_state, action)
            elif action.action == "revise_scope":
                state, changed = accept_scope_change(row.analysis_state, action)
            elif action.action == "recalculate_latest":
                state, changed = accept_recalculation(row.analysis_state, action)
            elif action.action == "retry_quality":
                state, changed = accept_quality_retry(row.analysis_state, action)
                if changed:
                    child_state = fork_run(row.analysis_state, row.id)
                    child_state.update(config_digest=row.analysis_state.get("config_digest"),
                                       semantic_plan=deepcopy(row.analysis_state["semantic_plan"]))
            else:
                state, changed = accept_evidence(row.analysis_state, action)
        except ActionRejected as exc:
            raise HTTPException(409, str(exc)) from exc
        if changed and topic.asset_id and action.action not in {"cancel_publication", "cancel_investigation_source"}:
            load_effective_topic(session, chat.datasource, workspace=chat.oid)
        previous = next(
            (e for e in state["events"] if e["request_id"] == action.request_id), {}
        )
        if not changed and previous.get("result_record_id"):
            row = session.get(ChatRecord, int(previous["result_record_id"]))
            check_saved_access(session, user, row)
        elif changed and child_state is not None:
            row = _new_execution(session, user, chat, row, state, action.request_id, child_state=child_state)
        elif changed and action.action == "revise_scope":
            if topic.semantic:
                raise HTTPException(409, "scope_revision_unavailable")
            change = action.scope_change
            child_state = resolve_request(
                state["request"]["text"],
                Intent(operation="revise", **change.model_dump()),
                topic, state, row.id,
            )
            if child_state["task_status"] != "running":
                raise HTTPException(409, child_state["decision"]["reason"])
            row = _new_execution(
                session, user, chat, row, state, action.request_id, child_state=child_state
            )
        elif changed and action.action == "recalculate_latest":
            row = _new_execution(session, user, chat, row, state, action.request_id)
        elif changed:
            _save(session, row, state, finished=action.action in {"cancel_publication", "cancel_investigation_source"})
        session.refresh(row)
        session.expunge(row)
        return row, changed


def _reserve_question(session, user, chat, request, topic, received_at, model_id):
    if not request.client_request_id:
        return _record(session, user, chat, request.question, model_id=model_id), True
    # A conversation lock serializes reservation without a new schema.
    session.execute(select(Chat.id).where(Chat.id == chat.id).with_for_update())
    existing = session.scalars(
        select(ChatRecord).where(
            ChatRecord.chat_id == chat.id,
            ChatRecord.create_by == user.id,
            ChatRecord.analysis_state["request"]["client_request_id"].as_string()
            == request.client_request_id,
        )
    ).first()
    if existing:
        saved = existing.analysis_state["request"]
        if saved["text"] != request.question or saved["selected_parent"] != request.parent_record_id:
            raise HTTPException(409, "request_id_conflict")
        check_saved_access(session, user, existing)
        return existing, False
    state = resolve_request(
        request.question, Intent(operation="unsupported"), topic, received_at=received_at
    )
    state["task_status"] = "running"
    state["decision"] = {"kind": "ready"}
    state["request"].update(
        client_request_id=request.client_request_id, selected_parent=request.parent_record_id
    )
    return _record(session, user, chat, request.question, state=state, model_id=model_id), True


def _historical_metrics(session, datasource, topic, view, allowed_fields):
    from apps.chat.curd.cbi_theme import list_versions

    history = []
    for item in list_versions(session, datasource):
        if item.version == topic.version:
            continue
        payload = item.content
        same_binding = all(
            payload.get(field) == getattr(topic, field)
            for field in ("source_version", "expected_database", "expected_reader", "expected_port",
                          "asset_id", "connection_digest")
        )
        if same_binding and any(
            definition["view"] == view and definition["id"] == payload["default_definition"]
            for definition in payload["definitions"]
        ):
            history.append(payload["semantic"]["metrics"])
    return historical_metric_catalog(topic.semantic.metrics.keys(), history, allowed_fields)


async def question(session, user, request, assistant, http_request, *, evaluation=None):
    received_at = datetime.now(UTC)
    if assistant:
        return None
    chat = session.get(Chat, request.chat_id)
    if not chat or (not is_managed_topic(session, chat.datasource) and not load_topic(chat.datasource)):
        return None
    if evaluation:
        _accessible_topic(session, user, chat)
    topic, datasource = _accessible_topic(
        session, user, chat, version=evaluation.version if evaluation else None
    )
    if not topic:
        return None
    prior_row = _owned_parent(session, user, chat, request.parent_record_id)
    text = request.question.strip()
    if request.parent_record_id is not None and prior_row:
        failed = prior_row.analysis_state
        if (failed["decision"].get("reason") == "interpretation_failed"
                and text == failed["request"]["text"].strip()):
            retry_parent = failed["request"].get("retry_parent_id")
            prior_row = _owned_parent(session, user, chat, int(retry_parent)) if retry_parent else None
    if request.parent_record_id is not None and prior_row and is_managed_topic(session, chat.datasource):
        if evaluation and prior_row.analysis_state["config_version"] != evaluation.version:
            raise HTTPException(409, "evaluation_parent_configuration_changed")
        topic, datasource = _accessible_topic(session, user, chat, version=prior_row.analysis_state["config_version"])
    history = {}
    allowed_fields = None
    if topic.semantic:
        view = topic.by_id(topic.default_definition).view
        permitted = _require_assets(session, user, datasource, {view: topic.semantic.planning_fields})
        allowed_fields = permitted[view]
        if topic.config_digest:
            history = _historical_metrics(session, chat.datasource, topic, view, permitted[view])
    prior = (
        prior_row.analysis_state
        if prior_row and prior_row.analysis_state.get("config_version") == topic.version
        and prior_row.analysis_state.get("task_status") in ContextStatuses
        else None
    )
    choice = explicitly_chosen(text, topic)
    if prior and prior["task_status"] == "awaiting_confirmation" and choice:
        aliases = topic.by_id(choice).aliases
        normalized = (
            text.strip("。！!，, ").removeprefix("按").removeprefix("用").strip()
        )
        allowed = {
            ending
            for alias in aliases
            for ending in (alias, alias + "口径", alias + "口径计算")
        }
        if normalized in allowed:
            action = Action(
                action="confirm_definition",
                request_id=uuid4().hex,
                expected_revision=prior["revision"],
                choice_id=choice,
                decision_id=prior["decision"]["id"],
            )
            return await action_response(
                user,
                prior_row.id,
                action,
                "text",
                submitted_question=True,
                http_request=http_request,
            )
    model = evaluation.model if evaluation else await get_default_config()
    row, fresh = _reserve_question(
        session, user, chat, request, topic, received_at, model.model_id
    )
    if not fresh:
        return StreamingResponse(
            iter(tuple(_response_events(row, replace_pending=True))), media_type="text/event-stream"
        )
    log = start_log(
        session,
        ai_modal_id=model.model_id,
        ai_modal_name=model.model_name,
        operate=OperationEnum.EXTRACT_KEYWORDS,
        record_id=row.id,
    )
    capture = {} if topic.config_digest and (evaluation or os.getenv("SQLBOT_CBI_CAPTURE_SEMANTIC") == "1") else None
    interpretation_failed = False
    try:
        intent, usage = await _interpret(text, topic, prior, model, received_at, history=history,
                                         capture=capture, evaluation=evaluation, allowed_fields=allowed_fields)
        parsed = intent.model_dump(mode="json")
        if capture is not None:
            parsed["captured_exchange"] = capture
        end_log(session, log, parsed, token_usage=usage)
    except Exception as error:
        interpretation_failed = True
        SQLBotLogUtil.warning(
            f"CBI request interpretation failed: {type(error).__name__}"
        )
        trigger_log_error(session, log)
        details = {
            "error": "interpretation_failed",
            "reason": "invalid_response" if isinstance(error, ValidationError)
            else "timeout" if "timeout" in type(error).__name__.lower()
            else "model_call_failed",
        }
        if topic.semantic and isinstance(error, ValidationError):
            details["invalid_fields"] = [{"path": list(item["loc"]), "type": item["type"]}
                                         for item in error.errors(include_input=False, include_context=False)]
        if capture is not None:
            details["captured_exchange"] = capture
        if evaluation and not isinstance(error, ValidationError):
            details["provider_error"] = getattr(error, "reason", None) or type(error).__name__
        end_log(session, log, details)
        intent = cbi_semantic.SemanticIntent(operation="unsupported") if topic.semantic else Intent(operation="unsupported")
    ordinary_intent = Intent(operation="unsupported") if topic.semantic else intent
    state = resolve_request(
        request.question, ordinary_intent, topic, prior, prior_row.id if prior_row else None,
        received_at=received_at,
    )
    if interpretation_failed:
        state.update(task_status="blocked", decision={"kind": "unsupported", "reason": "interpretation_failed"})
        if prior_row:
            state["request"]["retry_parent_id"] = str(prior_row.id)
    elif topic.semantic:
        metric_decision = validate_metric_request(
            text, intent, {key: metric.label for key, metric in topic.semantic.metrics.items()},
            topic.semantic.default_metric, history, (prior or {}).get("semantic_plan"),
        )
        state["metric_decision"] = metric_decision
        state["metric_references"] = [ref.model_dump() for ref in intent.metric_references]
        fact_role_uncertain = False
        try:
            missing_fact = cbi_semantic.requested_missing_fact(
                topic.semantic.missing_facts, text, intent.fact_references,
                requires_refund_evidence=intent.requires_refund_evidence,
            )
        except cbi_semantic.PlanRejected:
            missing_fact = None
            fact_role_uncertain = True
        if topic.semantic.missing_facts:
            state["fact_references"] = [ref.model_dump() for ref in intent.fact_references]
        if missing_fact:
            state.update(
                task_status="blocked",
                decision={"kind": "unsupported", "reason": "semantic_unsupported",
                          "missing_fact": missing_fact, "missing_fact_source": "configuration"},
            )
        elif fact_role_uncertain:
            state.update(task_status="blocked",
                         decision={"kind": "unsupported", "reason": "semantic_metric_clarify"})
        elif (intent.requires_refund_evidence and not topic.evidence_view
              and "refund" not in topic.semantic.missing_facts):
            state = _refund_facts_unavailable(
                state, prior if request.parent_record_id is not None else None,
                prior_row.id if prior_row else None,
            )
        elif metric_decision["disposition"] == "allow":
            if metric_decision["adopted_metrics"] is not None:
                intent = intent.model_copy(update={
                    "metrics": metric_decision["adopted_metrics"],
                })
                adopted_overall = metric_decision["adopted_overall_metrics"]
                if adopted_overall or intent.overall_metrics is not None:
                    intent = intent.model_copy(update={"overall_metrics": adopted_overall or []})
            if "refund" in topic.semantic.missing_facts and intent.requires_refund_evidence:
                intent = intent.model_copy(update={"requires_refund_evidence": False})
            intent = _normalize_investigation_intent(
                intent, prior, text, selected=request.parent_record_id is not None and topic.semantic.decomposition is not None,
            )
            state = _semantic_state(
                topic, state, intent, prior, prior_row.id if prior_row else None,
                selected=request.parent_record_id is not None,
            )
        else:
            unavailable = metric_decision["requested_unavailable"]
            state["decision"] = {
                "kind":"unsupported",
                "reason":"semantic_metric_unavailable" if unavailable else "semantic_metric_clarify",
                "metric_labels":[history[key][0]["label"] for key in unavailable],
            }
    if request.client_request_id:
        state["request"].update(
            client_request_id=request.client_request_id, selected_parent=request.parent_record_id
        )
    brief = None
    if not chat.brief_generate:
        brief = request.question[:48]
        chat.brief = brief
        chat.brief_generate = True
        session.add(chat)
    _save(session, row, state, finished=state["task_status"] != "running")
    if state["task_status"] == "running":
        row = await _run_with_disconnect(row.id, user, http_request)
    return StreamingResponse(
        iter(tuple(_response_events(row, brief=brief))), media_type="text/event-stream"
    )


async def _run_with_disconnect(
    record_id, user, request, *, evidence_only=False, publication_confirmed=False, request_id=None
):
    stop = Event()
    task = asyncio.create_task(
        asyncio.to_thread(
            _run,
            record_id,
            user,
            evidence_only=evidence_only,
            publication_confirmed=publication_confirmed,
            stop=stop,
            request_id=request_id,
        )
    )
    try:
        while not task.done():
            if request is not None and await request.is_disconnected():
                stop.set()
                break
            await asyncio.wait({task}, timeout=0.08)
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        raise


async def action_response(
    user,
    record_id,
    action,
    source="button",
    *,
    submitted_question=False,
    http_request=None,
):
    row, changed = await asyncio.to_thread(_apply, user, record_id, action, source)
    if changed and action.action not in {"cancel_publication", "cancel_investigation_source"}:
        row = await _run_with_disconnect(
            row.id,
            user,
            http_request,
            evidence_only=action.action == "continue_evidence",
            publication_confirmed=action.action == "confirm_publication",
            request_id=action.request_id,
        )
    parent = None
    if row.id != record_id:
        with Session(engine) as session:
            parent = session.get(ChatRecord, record_id)
    return StreamingResponse(
        iter(
            tuple(
                _response_events(
                    row,
                    replace_pending=submitted_question,
                    forked=row.id != record_id,
                    parent=parent,
                )
            )
        ),
        media_type="text/event-stream",
    )


def query_receipt(session, user, parent_id, query_id):
    parent = session.get(ChatRecord, parent_id)
    if (
        not parent
        or parent.create_by != user.id
        or not parent.analysis_state
        or parent.analysis_state.get("internal")
    ):
        raise HTTPException(404, "analysis_not_found")
    chat = session.get(Chat, parent.chat_id)
    topic, _ = _accessible_topic(session, user, chat, version=parent.analysis_state["config_version"])
    check_saved_access(session, user, parent)
    refs = {
        step.get("record_id")
        for step in parent.analysis_state.get("steps", [])
        if step.get("status") == "verified" and step.get("kind") != "quality"
    }
    if not topic or str(query_id) not in refs:
        raise HTTPException(404, "analysis_query_not_found")
    row = session.get(ChatRecord, query_id)
    if (
        not row
        or row.create_by != user.id
        or row.chat_id != chat.id
        or row.datasource != chat.datasource
    ):
        raise HTTPException(404, "analysis_query_not_found")
    return {"record_id": str(row.id), "sql": row.sql}


def check_saved_access(session, user, record):
    from apps.chat.task.cbi_multi_store import topic_for

    if not record or not (
        record.analysis_state or is_managed_topic(session, record.datasource) or load_topic(record.datasource) or topic_for(record.datasource)
    ):
        return
    if (record.analysis_state or {}).get("internal") or record.create_by != user.id:
        raise HTTPException(404, "analysis_not_found")
    check_history_access(session, user, record.chat_id)


def _saved_native_assets(topic, states):
    fields = SummaryFields if topic.layout == "store_day" else EventsFields
    definitions = {definition.id: definition.view for definition in topic.definitions}
    required = {}
    for state in states:
        for step in state.get("steps", ()):
            if step.get("status") != "verified" and step.get("kind") != "quality":
                continue
            if step.get("kind") in {"semantic", "quality"}:
                view = definitions.get(step.get("definition"))
                used = set(step.get("fields", ()))
                allowed = topic.semantic.fields if topic.semantic else set()
                if step.get("kind") == "quality" and topic.semantic and topic.semantic.quality:
                    from apps.chat.task import cbi_quality

                    allowed = allowed | cbi_quality.quality_required_fields(topic.semantic)
                if not view or view != step.get("view") or not used or not used <= allowed:
                    raise HTTPException(409, "analysis_source_changed")
                required.setdefault(view, set()).update(used)
            if step.get("kind") in {"summary", "trend", "category"}:
                view = definitions.get(step.get("definition"))
                if not view or view != step.get("view"):
                    raise HTTPException(409, "analysis_source_changed")
                required.setdefault(view, set()).update(fields)
                if step["kind"] == "category":
                    if not topic.category_enabled:
                        raise HTTPException(409, "analysis_source_changed")
                    required[view].add("category")
                if topic.layout == "store_day" and {"target", "attainment"}.intersection(
                    state.get("request", {}).get("outputs", ())
                ):
                    required[view].add("target_amount")
            if step.get("kind") == "refund_members":
                if not topic.evidence_view:
                    raise HTTPException(409, "analysis_source_changed")
                required[topic.evidence_view] = MemberFields
        if state.get("data_version") is not None and topic.publication_view:
            required[topic.publication_view] = {"version"}
    return required


def check_history_access(session, user, chat_id):
    chat = session.get(Chat, chat_id)
    if not chat or chat.create_by != user.id:
        raise HTTPException(404, "analysis_not_found")
    from apps.chat.task.cbi_multi_store import source_access, topic_for
    if topic_for(chat.datasource):
        topic, datasource = source_access(session, user, chat)
        if topic.kind == "methods":
            from apps.chat.task.cbi_multi_store import MultiTask, require_method_records
            task = session.get(MultiTask, chat.id)
            if task:
                require_method_records(session, user, datasource, task.state, True)
        return
    managed = is_managed_topic(session, chat.datasource)
    if not managed and not load_topic(chat.datasource):
        return
    states = list(session.scalars(
        select(ChatRecord.analysis_state).where(
            ChatRecord.chat_id == chat_id,
            ChatRecord.analysis_state.is_not(None),
            ChatRecord.analysis_state["internal"].as_boolean().is_not(True),
        )
    ))
    if not managed:
        topic, datasource = _accessible_topic(session, user, chat)
        if topic:
            required = _saved_native_assets(topic, states)
            if required:
                _require_assets(session, user, datasource, required)
        return

    # Saved results validate against the definition they used, and current access.
    by_version = {}
    for state in states:
        if any(step.get("status") == "verified" or step.get("kind") == "quality"
               for step in state.get("steps", ())):
            by_version.setdefault(state["config_version"], []).append(state)
    if not by_version:
        _accessible_topic(session, user, chat, version=states[-1]["config_version"] if states else None)
    for version, saved_states in by_version.items():
        topic, datasource = _accessible_topic(session, user, chat, version=version)
        required = _saved_native_assets(topic, saved_states)
        if required:
            _require_assets(session, user, datasource, required)
