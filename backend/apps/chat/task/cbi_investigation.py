"""Bounded investigation planning and verified, structured findings."""

from datetime import date
from decimal import Decimal, localcontext
from hashlib import sha256
from json import dumps
from time import monotonic

from sqlalchemy import (
    Date,
    Integer,
    String,
    and_,
    cast,
    column,
    func,
    literal,
    or_,
    select,
    table,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by, dialect

METHOD_VERSION = "additive-change-v1"
SOURCE_ROW_LIMIT = 50_000
STEP_LIMIT = 5
CANDIDATE_LIMIT = 2
TIME_LIMIT_SECONDS = 55


def candidates(result, *, limit=CANDIDATE_LIMIT):
    if not result.get("reconciled"):
        return []
    rows = [
        item
        for item in result["rows"]
        if item["delta"] is not None and Decimal(str(item["delta"])) != 0
    ]
    return sorted(
        rows, key=lambda item: (-abs(Decimal(str(item["delta"]))), str(item["key"]))
    )[:limit]


def source_scope(plan):
    return {
        key: plan[key]
        for key in ("start", "end", "compare_start", "compare_end", "filters")
    }


def same_periods(parent, child):
    return all(
        parent[key] == child[key]
        for key in ("start", "end", "compare_start", "compare_end")
    )


def narrows_scope(parent, child):
    def members(value):
        return set(value) if isinstance(value, list) else {value}

    return same_periods(parent, child) and all(
        key in child["filters"] and members(child["filters"][key]) <= members(value)
        for key, value in parent["filters"].items()
    )


def comparison_total(current, baseline):
    with localcontext() as context:
        context.prec = 120
        a, b = Decimal(str(current)), Decimal(str(baseline))
        return {"current": str(a), "baseline": str(b), "delta": str(a - b)}


def same_source(expected, actual):
    return bool(expected and actual and expected == actual and actual.get("digest"))


def source_query(topic, view, scope):
    """Hash the complete authorized projection in a bounded, repeatable read."""
    schema, name = view.split(".")
    numeric = {
        key
        for dim in topic.dimensions.values()
        if dim.numeric
        for key in (dim.field, dim.identity)
        if key
    }
    relation = table(
        name,
        *[
            column(
                key,
                Date
                if key == topic.date_field
                else Integer
                if key in numeric
                else String,
            )
            for key in sorted(topic.fields)
        ],
        schema=schema,
    )
    business_day = relation.c[topic.date_field]
    predicates = [
        or_(
            and_(
                business_day >= date.fromisoformat(scope["start"]),
                business_day < date.fromisoformat(scope["end"]),
            ),
            and_(
                business_day >= date.fromisoformat(scope["compare_start"]),
                business_day < date.fromisoformat(scope["compare_end"]),
            ),
        )
    ]
    for key, value in sorted(scope["filters"].items()):
        field = relation.c[topic.dimensions[key].field]
        predicates.append(
            field.in_(value) if isinstance(value, list) else field == value
        )
    limited = (
        select(*relation.c)
        .where(*predicates)
        .limit(SOURCE_ROW_LIMIT + 1)
        .subquery("source_rows")
    )
    hashed = (
        select(
            func.encode(
                func.sha256(
                    func.convert_to(
                        cast(func.row_to_json(limited.table_valued()), String), "UTF8"
                    )
                ),
                "hex",
            ).label("fingerprint")
        )
        .select_from(limited)
        .subquery()
    )
    combined = func.coalesce(
        func.string_agg(
            hashed.c.fingerprint,
            aggregate_order_by(literal(""), hashed.c.fingerprint),
        ),
        "",
    )
    statement = select(
        func.count().label("rows"),
        func.encode(func.sha256(func.convert_to(combined, "UTF8")), "hex").label(
            "digest"
        ),
    ).select_from(hashed)
    sql = str(
        statement.compile(dialect=dialect(), compile_kwargs={"literal_binds": True})
    )
    return statement, sql, topic.fields


def child_plan(plan, group, filter_key, value, *, top=25):
    return dict(
        plan,
        group=group,
        filters={**plan["filters"], filter_key: value},
        top=top,
        detail=False,
        overall_metrics=[],
    )


def fact(row, plan, metric, unit, reference):
    values = {
        "key": row["key"],
        "label": row["label"],
        "metric": metric,
        "dimension": plan["group"],
        "filters": sorted([key, value] for key, value in plan["filters"].items()),
        "current_period": [plan["start"], plan["end"]],
        "baseline_period": [plan["compare_start"], plan["compare_end"]],
        "unit": unit,
        "current": row["current"],
        "baseline": row["baseline"],
        "delta": row["delta"],
        "direction": "increase" if Decimal(str(row["delta"])) > 0 else "decrease",
        "reference": reference,
        "formula": "current - baseline",
    }
    return {
        "fact_id": sha256(dumps(values, sort_keys=True).encode()).hexdigest()[:20],
        **values,
    }


def verify_claim(fact_record, proposed):
    """Only accept claims bound to the same verified fields and evidence."""
    if proposed.keys() != fact_record.keys():
        return False
    for key, value in fact_record.items():
        candidate = proposed[key]
        if key in {"current", "baseline", "delta"}:
            try:
                if Decimal(str(candidate)) != Decimal(str(value)):
                    return False
            except ArithmeticError:
                return False
        elif value != candidate:
            return False
    return True


def new_record(plan, *, members=False, refund=False, execution_scope="investigation"):
    single_partition = execution_scope == "single_partition"
    return {
        "status": "running",
        "reason_status": "unconfirmed",
        "plan": {
            "method_version": METHOD_VERSION,
            "first_dimension": plan["group"],
            "execution_scope": execution_scope,
            "needs_members": members,
            "needs_refund_evidence": refund,
            "logical_step_limit": 1 if single_partition else STEP_LIMIT,
            "candidate_limit": 0 if single_partition else CANDIDATE_LIMIT,
            "layer_limit": 1 if single_partition else 2,
            "time_limit_seconds": TIME_LIMIT_SECONDS,
        },
        "steps": [],
        "findings": [],
        "stop_reason": None,
    }


def scheduled_steps(topic, plan, result, investigation):
    """Materialize a fixed bounded continuation from the complete first partition."""
    policy = topic.decomposition
    root = plan["group"]
    dimension = topic.dimensions[root]
    filter_key = root
    if dimension.identity:
        filter_key = next(
            (
                key
                for key, candidate in topic.dimensions.items()
                if candidate.field == dimension.identity
            ),
            None,
        )
    partitions = policy.dimensions[root]
    selected = [row for row in candidates(result) if row["key"] is not None]
    steps = []
    if filter_key and partitions:
        for row in selected:
            scoped = child_plan(plan, partitions[0], filter_key, row["key"])
            steps.append(
                {
                    "id": "drill-" + str(len(steps) + 1),
                    "kind": "drill",
                    "status": "pending",
                    "dimension": partitions[0],
                    "label": row["label"],
                    "parent_total": row,
                    "parent_key": row["key"],
                    "query_plan": scoped,
                }
            )
    if investigation["plan"]["needs_members"]:
        scope = plan
        if filter_key and selected:
            scope = child_plan(plan, root, filter_key, selected[0]["key"])
        # Both periods use a separate existing detail query. This shares one logical step.
        steps.append(
            {
                "id": "members",
                "kind": "members",
                "status": "pending",
                "dimension": root,
                "label": selected[0]["label"] if selected and filter_key else None,
                "query_plan": scope,
            }
        )
    return steps


def finish(state, status, reason):
    investigation = state["investigation"]
    investigation.update(status=status, stop_reason=reason)
    state["task_status"] = status


def time_remaining(start):
    return TIME_LIMIT_SECONDS - (monotonic() - start)
