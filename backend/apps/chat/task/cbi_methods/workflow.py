from copy import deepcopy
from uuid import uuid4

import orjson

from apps.chat.task.cbi_analysis import _record
from apps.chat.task.cbi_contract import timestamp
from apps.chat.task.cbi_multi_store import MultiTask

from .contracts import Change, build_plan, exact_delta
from .event_view import event_rows
from .provenance import compose_definition
from .registry import resolve_binding
from .execution import calculate, fetch


def run(session, user, chat, state, topic, datasource, definition, *, publish=True, diagnostic=False):
    plan = definition["plan"]
    started = timestamp()
    binding = resolve_binding(session, user, datasource, plan["binding"], plan["binding_version"], history=True)
    if binding.adapter == "event_view":
        events, source = event_rows(datasource, topic, binding)
        facts = {"events": events}
    else:
        facts, source = fetch(datasource, topic, plan["binding"])
    result, fingerprint = calculate(plan, facts, diagnostic=diagnostic, binding=binding)
    item = {"id": uuid4().hex, "actor": str(user.id), "definition": deepcopy(definition),
            "result": result, "started": started, "finished": timestamp(),
            "display": {"epoch": binding.epoch, "timezone": binding.timezone},
            "evidence": {"source": source, "engine_sha256": fingerprint,
                         "engine": "B03-fault-fixture" if diagnostic else "baseline",
                         "calculation": "registered_funnel" if plan["unit"] == "attempt" else "registered_repeat"}}
    parent = state["runs"].get(definition["parent_run"])
    if parent and parent["definition"]["plan"]["binding"] == plan["binding"]:
        reference = parent["result"]
        changed = [key for key, value in plan.items() if parent["definition"]["plan"][key] != value]
        item["comparison"] = {"base_run": parent["id"], "changed": changed}
        if result["rate_pct"] is not None and reference["rate_pct"] is not None:
            item["comparison"]["delta_pp"] = exact_delta(result, reference)
        if plan["unit"] == "attempt":
            item["comparison"]["new_completions"] = sorted(set(result["members"][2]) - set(reference["members"][2]))
    if publish:
        item["record_id"] = save_record(session, user, chat, state, item)
        state["work"] = item["id"]
    state["runs"][item["id"]] = item
    return item


def save_record(session, user, chat, state, run):
    result, plan = run["result"], run["definition"]["plan"]
    marker = {"run_id": run["id"], "task_id": str(chat.id), "window": f'{plan["window_hours"]}h',
              "revision": run["definition"]["id"], "method": plan["method"], "numerator": result["numerator"],
              "denominator": result["denominator"], "rate_pct": result["rate_pct"], "status": result["status"]}
    row = _record(session, user, chat, state["question"], state={"multi": marker})
    marker["record_id"] = str(row.id)
    row.analysis_state = {"multi": dict(marker)}
    fields = {"step": "步骤", "count": "确认人数"} if "counts" in result else {
        "numerator": "成熟样本复购人数", "denominator": "成熟样本人数", "rate_pct": "复购率（%）",
    }
    if "counts" in result:
        fields["count"] = "确认尝试数"
        values = [{"step": step, "count": value} for step, value in zip(
            ("浏览","加购","支付"), result["counts"], strict=True,
        )]
    else:
        values = [{key: result[key] for key in fields}]
    chart = {"type": "table", "title": "方法运行", "columns": [{"name": label, "value": key} for key, label in fields.items()]}
    row.chart = orjson.dumps(chart).decode()
    row.data = orjson.dumps({"datasource": chat.datasource, "fields": list(fields), "data": values}).decode()
    row.finish = True
    session.add(row)
    session.flush()
    return str(row.id)


def saved_funnel_result(session, record):
    """Read the presentation fields of an already authorized saved result."""
    marker = (record.analysis_state or {}).get("multi") or {}
    if marker.get("method") != "ordered_attempt_funnel":
        return None
    task = session.get(MultiTask, record.chat_id)
    runs = task.state["runs"] if task else {}
    run = runs.get(marker.get("run_id"))
    if not run:
        return None
    plan, result = run["definition"]["plan"], run["result"]
    plan_fields = ("window_hours", "entry_start", "entry_end", "observation_asof", "unit")
    result_fields = ("counts", "each_of_entry_pct", "immature")
    summary_fields = ("numerator", "denominator", "rate_pct", "status")
    if not (set(plan_fields) <= plan.keys() and set(result_fields) <= result.keys()
            and set(summary_fields) <= marker.keys()):
        return None
    view = {
        **{key: plan[key] for key in plan_fields},
        **{key: result[key] for key in result_fields},
        **{key: marker[key] for key in summary_fields},
        "run_id": run.get("id", marker["run_id"]),
        "store": plan.get("store"),
        "timezone": plan.get("timezone"),
        "binding": plan.get("binding"),
        "binding_version": plan.get("binding_version"),
    }
    comparison = run.get("comparison") or {}
    base_run = runs.get(comparison.get("base_run"))
    if base_run and comparison.get("delta_pp") is not None:
        view["comparison"] = {
            "run_id": comparison["base_run"],
            "window_hours": base_run["definition"]["plan"]["window_hours"],
            "delta_pp": comparison["delta_pp"],
        }
    return view


def correct(session, user, chat, state, topic, datasource, base, reason):
    if not reason or base["evidence"]["engine"] != "B03-fault-fixture":
        raise ValueError("method_correction_requires_registered_case")
    plan = base["definition"]["plan"]
    binding = resolve_binding(session, user, datasource, plan["binding"], plan["binding_version"], history=True)
    definition = compose_definition(plan, binding, Change(), base["id"], "controls", base["id"])
    candidate = run(session, user, chat, state, topic, datasource, definition, publish=False)
    checks = {}
    for label, change in {
        "full": Change(cohort_channel=None, repeat_channel=None), "both_ad": Change(repeat_channel="ad"),
    }.items():
        guard = build_plan(plan["binding"], change, plan, base["id"])
        receipt = run(session, user, chat, state, topic, datasource, compose_definition(guard, binding, change, base["id"], "controls", base["id"]), publish=False)
        checks[label] = {"run_id": receipt["id"], "numerator": receipt["result"]["numerator"],
                         "denominator": receipt["result"]["denominator"]}
    if (
        candidate["result"]["status"] != "verified"
        or checks["full"]["denominator"] < checks["both_ad"]["denominator"]
        or checks["both_ad"]["numerator"] > candidate["result"]["numerator"]
    ):
        raise ValueError("method_comparison_failed")
    candidate["correction"] = {
        "base_run": base["id"], "reason": reason, "guards": checks,
        "change": "首购渠道只筛选已确定的历史首购，再次订单使用独立筛选。",
    }
    return candidate
