"""Deterministic checks on a single authorized topic view and read transaction."""

import re
from datetime import UTC, date, datetime
from time import perf_counter

from sqlalchemy import (
    BigInteger,
    Date,
    Numeric,
    String,
    and_,
    cast,
    column,
    func,
    literal,
    null,
    or_,
    select,
    table,
    union_all,
)
from sqlalchemy.dialects.postgresql import dialect

from apps.chat.task.cbi_semantic import QualityRule, TopicSemantics

_VIEW = re.compile(r"[a-z_][a-z0-9_]{0,62}\.[a-z_][a-z0-9_]{0,62}")
_SAMPLE_LIMIT = 5
_SAMPLE_LENGTH = 120


def _applicable_rules(topic: TopicSemantics, plan: dict | None) -> list[QualityRule]:
    if topic.quality is None:
        return []
    if plan is None:
        return topic.quality.rules
    filters = set(plan.get("filters", {}))
    dimensions = filters | {plan.get("group")}
    metrics = set(plan["metrics"]) | set(plan.get("overall_metrics", ()))
    if plan.get("detail"):
        fields = {
            topic.date_field,
            *topic.detail,
            *(topic.dimensions[key].field for key in filters),
        }
        return [
            rule for rule in topic.quality.rules if fields.intersection(rule.fields)
        ]
    return [
        rule
        for rule in topic.quality.rules
        if metrics.intersection(rule.metrics)
        and (not rule.dimensions or dimensions.intersection(rule.dimensions))
    ]


def _requested_impact(rule, plan):
    if plan is None:
        return rule.metrics, rule.dimensions
    metrics = dict.fromkeys((*plan["metrics"], *plan.get("overall_metrics", ())))
    dimensions = {plan.get("group"), *plan.get("filters", {})}
    return (
        list(metrics)
        if plan.get("detail")
        else [key for key in rule.metrics if key in metrics],
        [key for key in rule.dimensions if key in dimensions],
    )


def _projected_fields(topic, rules, filters, samples) -> set[str]:
    return {
        topic.date_field,
        *samples,
        *(field for rule in rules for field in rule.fields),
        *(topic.dimensions[key].field for key in filters),
    }


def quality_required_fields(
    topic: TopicSemantics, plan=None, *, include_samples=False
) -> set[str]:
    rules = _applicable_rules(topic, plan)
    if not rules:
        return set()
    samples = topic.quality.sample_keys if include_samples else ()
    return _projected_fields(topic, rules, (plan or {}).get("filters", {}), samples)


def _date(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _periods(topic, plan, periods):
    if plan is not None and periods is not None:
        raise ValueError("Quality scope must come from a single request")
    if plan is not None:
        periods = {"current": (plan["start"], plan["end"])}
        if plan.get("compare_start") or plan.get("compare_end"):
            periods["baseline"] = (plan["compare_start"], plan["compare_end"])
    if periods is None:
        periods = {"current": (topic.start, topic.end)}
    if "current" not in periods or periods.keys() - {"current", "baseline"}:
        raise ValueError("Invalid quality scope")
    scopes = {}
    for name, limits in periods.items():
        start, end = map(_date, limits)
        if not start or not end or not 0 < (end - start).days <= 731:
            raise ValueError("Invalid quality period")
        scopes[name] = (start, end)
    if "baseline" in scopes:
        start, end = scopes["current"]
        baseline_start, baseline_end = scopes["baseline"]
        if start < baseline_end and baseline_start < end:
            raise ValueError("Quality periods overlap")
    return scopes


def _whole_scope(rule: QualityRule, date_field: str) -> bool:
    return rule.type == "freshness" or (
        rule.type == "not_null" and rule.fields == [date_field]
    )


def _missing_or_outside(rule, field):
    if rule.type == "not_null":
        return field.is_(None)
    bounds = [cast(field, String).in_(("NaN", "Infinity", "-Infinity"))]
    if rule.minimum is not None:
        bounds.append(field < rule.minimum)
    if rule.maximum is not None:
        bounds.append(field > rule.maximum)
    return or_(*bounds)


def _scope_query(topic, view, rules, filters, start, end, samples):
    schema, name = view.split(".")
    fields = _projected_fields(topic, rules, filters, samples)
    numeric = {dim.field for dim in topic.dimensions.values() if dim.numeric}
    numeric.update(rule.fields[0] for rule in rules if rule.type == "range")
    source = table(
        name,
        *(
            column(
                key,
                Date
                if key == topic.date_field
                else Numeric
                if key in numeric
                else String,
            )
            for key in sorted(fields)
        ),
        schema=schema,
    )
    date_column = source.c[topic.date_field]
    predicates = []
    if start is not None:
        predicates.extend((date_column >= start, date_column < end))
    for key, value in sorted(filters.items()):
        field = source.c[topic.dimensions[key].field]
        predicates.append(
            field.in_(value) if isinstance(value, list) else field == value
        )
    scoped = select(*source.c).where(*predicates).cte()
    aggregates = [
        func.count().label("population"),
        func.max(scoped.c[topic.date_field]).label("observed"),
        func.count(scoped.c[topic.date_field]).label("dated"),
    ]
    duplicates = {}
    sample_sources = []
    for index, rule in enumerate(rules):
        field = scoped.c[rule.fields[0]]
        if rule.type == "freshness":
            continue
        if rule.type == "unique":
            keys = [scoped.c[key] for key in rule.fields]
            grouped = (
                select(*keys, func.count().label("_quality_group_size"))
                .where(*(key.is_not(None) for key in keys))
                .group_by(*keys)
                .cte()
            )
            size = grouped.c._quality_group_size
            duplicates[index] = (
                select(
                    func.count().label("checked"),
                    func.count().filter(size > 1).label("issues"),
                    func.coalesce(func.sum(size).filter(size > 1), 0).label("affected"),
                )
                .select_from(grouped)
                .subquery()
            )
            if samples:
                joined = scoped.join(
                    grouped, and_(*(key == grouped.c[key.name] for key in keys))
                )
                sample_sources.append((index, joined, size > 1))
        else:
            predicate = _missing_or_outside(rule, field)
            aggregates.extend(
                (
                    (func.count(field) if rule.type == "range" else func.count()).label(
                        f"checked_{index}"
                    ),
                    func.count().filter(predicate).label(f"issues_{index}"),
                )
            )
            if samples:
                sample_sources.append((index, scoped, predicate))

    aggregate = select(*aggregates).select_from(scoped).cte()
    sample_columns = [f"key_{index}" for index in range(len(samples))]

    def summary_row(index, checked, issues=None, affected=None, observed=None):
        return select(
            literal(index).label("rule_index"),
            checked.label("checked"),
            (cast(null(), BigInteger) if issues is None else issues).label("issues"),
            (cast(null(), BigInteger) if affected is None else affected).label(
                "affected"
            ),
            (cast(null(), Date) if observed is None else observed).label("observed"),
            literal(False).label("sample"),
            *(cast(null(), String).label(key) for key in sample_columns),
        )

    statements = [
        summary_row(-1, aggregate.c.population, observed=aggregate.c.observed)
    ]
    for index, rule in enumerate(rules):
        if rule.type == "unique":
            counts = duplicates[index].c
            statements.append(
                summary_row(index, counts.checked, counts.issues, counts.affected)
            )
        elif rule.type == "freshness":
            statements.append(
                summary_row(index, aggregate.c.dated, observed=aggregate.c.observed)
            )
        else:
            issues = aggregate.c[f"issues_{index}"]
            statements.append(
                summary_row(index, aggregate.c[f"checked_{index}"], issues, issues)
            )

    for index, relation, predicate in sample_sources:
        values = [
            func.substr(cast(scoped.c[field], String), 1, _SAMPLE_LENGTH + 1).label(key)
            for field, key in zip(samples, sample_columns, strict=True)
        ]
        offenders = (
            select(*values)
            .select_from(relation)
            .where(predicate)
            .distinct()
            .order_by(*values)
            .limit(_SAMPLE_LIMIT + 1)
            .subquery()
        )
        statements.append(
            select(
                literal(index),
                null(),
                null(),
                null(),
                cast(null(), Date),
                literal(True),
                *offenders.c,
            )
        )
    result = union_all(*statements).subquery()
    return select(*result.c).order_by(
        result.c.rule_index, result.c.sample, *(result.c[key] for key in sample_columns)
    ), fields


def _report_status(results):
    statuses = {item["status"] for item in results}
    return next(
        (
            status
            for status in ("failed", "incomplete", "warning", "no_samples", "passed")
            if status in statuses
        ),
        "not_applicable",
    )


def _rule_result(rule, row, scoped_samples, checked_on, scope, query_ref, plan):
    checked = row["checked"]
    issues = row["issues"]
    if rule.type == "freshness" and checked:
        issues = int((checked_on - _date(row["observed"])).days > rule.max_age_days)
    status = "no_samples"
    if checked:
        status = (
            "passed"
            if not issues
            else "warning"
            if rule.severity == "warning"
            else "failed"
        )
    metrics, dimensions = _requested_impact(rule, plan)
    result = {
        "rule_id": rule.id,
        "type": rule.type,
        "fields": rule.fields,
        "severity": rule.severity,
        "metrics": metrics,
        "dimensions": dimensions,
        "description": rule.description,
        "suggestion": rule.suggestion,
        "scope": scope,
        "status": status,
        "checked_count": checked,
        "issue_count": issues if checked else None,
        "count_unit": {"unique": "key_groups", "freshness": "freshness"}.get(
            rule.type, "rows"
        ),
        "affected_rows": row["affected"] if checked else None,
        "query_ref": query_ref,
    }
    if scoped_samples:
        result["samples_truncated"] = len(scoped_samples) > _SAMPLE_LIMIT or any(
            value is not None and len(value) > _SAMPLE_LENGTH
            for sample in scoped_samples
            for value in sample.values()
        )
        result["sample_keys"] = [
            {
                key: value[:_SAMPLE_LENGTH] if value is not None else None
                for key, value in sample.items()
            }
            for sample in scoped_samples[:_SAMPLE_LIMIT]
        ]
    return result


def run_quality(
    read_session,
    topic: TopicSemantics,
    view: str,
    *,
    allowed_fields,
    plan=None,
    include_samples=False,
    checked_on=None,
    before_query=None,
    periods=None,
    execute=None,
) -> dict:
    """Run in the caller's transaction. The caller authorizes the view and column set.

    SQL or deadline errors propagate. Never turn an incomplete read into a zero count.
    """
    checked_on = (
        _date(checked_on) if checked_on is not None else datetime.now(UTC).date()
    )
    report = {
        "status": "not_applicable",
        "source_mode": topic.quality.source_mode if topic.quality else None,
        "checked_on": checked_on.isoformat(),
        "coverage": [],
        "results": [],
        "queries": [],
    }
    rules = _applicable_rules(topic, plan)
    if not rules:
        return report
    if not _VIEW.fullmatch(view) or not quality_required_fields(
        topic, plan, include_samples=include_samples
    ) <= set(allowed_fields):
        raise ValueError("Quality scope includes an unavailable asset")

    filters = (plan or {}).get("filters", {})
    periods = _periods(topic, plan, periods)
    sample_keys = topic.quality.sample_keys if include_samples else ()
    period_rules = [rule for rule in rules if not _whole_scope(rule, topic.date_field)]
    readable_rules = [rule for rule in rules if _whole_scope(rule, topic.date_field)]
    scopes = [
        (name, start, end, period_rules)
        for name, (start, end) in periods.items()
        if period_rules
    ]
    if readable_rules:
        scopes.append(("readable", None, None, readable_rules))
    for name, start, end, selected in scopes:
        statement, fields = _scope_query(
            topic, view, selected, filters, start, end, sample_keys
        )
        sql = str(
            statement.compile(
                dialect=dialect(paramstyle="named"),
                compile_kwargs={"literal_binds": True},
            )
        )
        if before_query is not None:
            before_query()
        tick = perf_counter()
        if execute is None:
            with read_session.execute(statement) as result:
                rows = result.mappings().all()
        else:
            rows = execute(statement, sql=sql, fields=fields)
        duration = round((perf_counter() - tick) * 1000, 3)
        query_ref = f"quality_{name}"
        report["queries"].append(
            {
                "query_ref": query_ref,
                "sql": sql,
                "fields": sorted(fields),
                "row_count": len(rows),
                "duration_ms": duration,
            }
        )
        counts = {row["rule_index"]: row for row in rows if not row["sample"]}
        if len(counts) != len(selected) + 1 or len(counts) + sum(
            bool(row["sample"]) for row in rows
        ) != len(rows):
            raise ValueError("Incomplete quality query result")
        population = counts[-1]
        observed = population["observed"]
        report["coverage"].append(
            {
                "scope": name,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "filters": filters,
                "checked_count": population["checked"],
                "latest_observed_date": _date(observed).isoformat()
                if observed is not None
                else None,
                "completeness_confirmed": False,
            }
        )
        for index, rule in enumerate(selected):
            samples = [
                {key: row[f"key_{i}"] for i, key in enumerate(sample_keys)}
                for row in rows
                if row["sample"] and row["rule_index"] == index
            ]
            report["results"].append(
                _rule_result(
                    rule, counts[index], samples, checked_on, name, query_ref, plan
                )
            )
    report["status"] = _report_status(report["results"])
    return report
