"""Compare frozen standards with authorized, persisted native question receipts."""

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

Document = dict[str, Any]
ABSENT = object()
QUERY_LABELS = {"business": "业务结果查询", "drill": "商品下钻", "quality": "质量检查"}
NON_BUSINESS = {"quality_check", "source_proof", "permission_check", "metadata_check"}


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _equal(actual: Any, expected: Any, tolerance: Any) -> bool:
    if tolerance is None:
        return type(actual) is type(expected) and actual == expected
    allowed = _decimal(tolerance)
    if allowed is None or allowed < 0:
        raise ValueError("invalid numeric tolerance")
    value, target = _decimal(actual), _decimal(expected)
    if target is None:
        raise ValueError("invalid expected number")
    return value is not None and abs(value - target) <= allowed


def _compare_rows(
    spec: Document, rows: Any, check: Any, dimension: str, label: str
) -> None:
    expected = spec["rows"]
    if not isinstance(rows, list):
        check("evidence", f"缺少{label}回执", expected, kind="unjudgeable")
        return
    key = spec.get("key")
    keys = [key] if isinstance(key, str) else key or []
    numeric = spec.get("numeric", ["current", "baseline", "delta", "billed"])
    if not check(dimension, f"{label}行数", len(expected), len(rows)):
        return
    if any(not isinstance(row, dict) for row in rows):
        check(dimension, f"{label}行格式", "object", rows)
        return

    def identity(row: Document) -> tuple[Any, ...]:
        return tuple(row.get(k, ABSENT) for k in keys)

    expected_keys = [identity(row) for row in expected]
    actual_keys = [identity(row) for row in rows]
    if keys:
        if len(set(expected_keys)) != len(expected_keys):
            raise ValueError("duplicate expected row identity")
        if spec.get("ordered"):
            if not check(dimension, f"{label}顺序", expected_keys, actual_keys):
                return
        if Counter(actual_keys) != Counter(expected_keys):
            check(dimension, f"{label}对象标识", expected_keys, actual_keys)
            return
        indexed = dict(zip(actual_keys, rows, strict=True))
        rows = [indexed[identity(row)] for row in expected]

    for ordinal, (target, row) in enumerate(zip(expected, rows, strict=True), 1):
        if spec.get("exact_columns"):
            check(dimension, f"{label}第 {ordinal} 行字段", sorted(target), sorted(row))
        for field, value in target.items():
            tolerance = (
                spec.get("tolerance")
                if field in numeric and value is not None
                else None
            )
            check(
                dimension,
                f"{label}第 {ordinal} 行 {field}",
                value,
                row.get(field, ABSENT),
                tolerance=tolerance,
            )


def _output_checks(spec: list[Document], state: Document, check: Any) -> None:
    entries = state.get("outputs")
    if not isinstance(entries, list):
        check(
            "evidence",
            "缺少结构化输出回执",
            [entry["id"] for entry in spec],
            kind="unjudgeable",
        )
        return
    by_id = {entry.get("id"): entry for entry in entries}
    expected_ids = sorted(entry["id"] for entry in spec)
    check(
        "presentation",
        "交付指标",
        expected_ids,
        sorted(str(entry.get("id")) for entry in entries),
    )
    for expected in spec:
        name = expected["id"]
        actual = by_id.get(name, {})
        dimension = (
            "quality" if expected.get("reason") == "quality_blocked" else "presentation"
        )
        for field in ("status", "unit", "reason"):
            if field in expected:
                check(
                    dimension,
                    f"{name} 的 {field}",
                    expected[field],
                    actual.get(field, ABSENT),
                )
        if "value" in expected:
            target = expected["value"]
            check(
                "quantity" if target is not None else dimension,
                f"{name} 数值",
                target,
                actual.get("value", ABSENT),
                tolerance=expected.get("tolerance"),
            )
            number = _decimal(target) if name.endswith("_delta") else None
            if number is not None:
                direction = (
                    "increase"
                    if number > 0
                    else "decrease"
                    if number < 0
                    else "unchanged"
                )
                for stated in (actual.get("direction"), state.get("direction")):
                    if stated is not None:
                        check("direction", f"{name} 变化方向", direction, stated)


def _query_checks(
    spec: Document, turn: Document, state: Document, check: Any
) -> dict[str, Document]:
    steps, queries = state.get("steps"), turn.get("queries")
    if not isinstance(steps, list) or not isinstance(queries, list):
        check("evidence", "缺少已授权 SQL 回执或原生执行步骤", kind="unjudgeable")
        return {}
    by_id: dict[str, Document] = {}
    for query in queries:
        reference = str(query.get("record_id"))
        if reference == "None" or reference in by_id:
            check(
                "evidence",
                "SQL 回执标识缺失或重复",
                actual=reference,
                kind="unjudgeable",
            )
        else:
            by_id[reference] = query
    drill_ids = {
        str(step.get("record_id"))
        for step in (state.get("investigation") or {}).get("steps", [])
        if step.get("kind") == "drill"
    }
    execution: Counter[str] = Counter()
    linked = set()
    missing: Counter[str] = Counter()
    for step in steps:
        reference = str(step.get("record_id"))
        query = by_id.get(reference)
        linked.add(reference)
        purpose = step.get("purpose")
        if purpose == "quality_check" or step.get("kind") == "quality":
            kinds = ("quality",)
        elif purpose in NON_BUSINESS:
            kinds = ()
        else:
            kinds = (
                ("business", "drill")
                if reference in drill_ids or purpose == "drill"
                else ("business",)
            )
        if query and query.get("sql"):
            execution.update(kinds)
        elif step.get("status") == "verified":
            missing.update(kinds)
            check("evidence", f"缺少查询 {reference} 的 SQL 回执", kind="unjudgeable")
    for reference, query in by_id.items():
        # Failed native business execution can store its SQL before recording a step.
        if reference not in linked and query.get("sql"):
            execution["business"] += 1
    if turn.get("queries_complete", True):
        for name, expected in spec.items():
            if name not in QUERY_LABELS or type(expected) is not int or expected < 0:
                raise ValueError("invalid query count")
            actual = execution[name]
            if actual > expected or actual + missing[name] < expected:
                check("scope", f"{QUERY_LABELS[name]}次数", expected, actual)
    else:
        check("evidence", "未能取得完整 SQL 回执", kind="unjudgeable")
    return by_id


def _quality_checks(
    spec: Document,
    turn: Document,
    state: Document,
    queries: dict[str, Document],
    check: Any,
) -> None:
    quality = state.get("quality") or {}
    check("quality", "质量处置", spec["status"], quality.get("status"))
    if "unavailable" in spec:
        unavailable = spec["unavailable"]
        check(
            "quality",
            "受阻指标",
            sorted(unavailable),
            sorted(
                str(entry.get("key"))
                for entry in quality.get("unavailable_outputs", [])
            ),
        )
        table = (state.get("presentation") or {}).get("table") or {}
        row_sources = (table.get("rows") or [], turn.get("raw_rows") or [])
        for metric in unavailable:
            leaked = any(
                value is not None
                and (
                    field == metric
                    or field.endswith("_" + metric)
                    or field.startswith(metric + "_")
                )
                for rows in row_sources
                for row in rows
                for field, value in row.items()
            )
            check("quality", f"{metric} 无结果泄露", False, leaked)
    native = {
        (entry.get("rule_id"), entry.get("scope")): entry
        for entry in quality.get("checks", [])
    }
    full = {
        (result.get("rule_id"), result.get("scope")): result
        for query in queries.values()
        for result in ((query.get("state") or {}).get("quality_report") or {}).get(
            "results", []
        )
    }
    for expected in spec.get("checks", []):
        identity = (expected["rule_id"], expected["scope"])
        description = f"{identity[0]}（{identity[1]}）"
        check(
            "quality",
            f"{description}检查结论",
            expected["status"],
            native.get(identity, {}).get("status"),
        )
        for field in ("issue_count", "affected_rows"):
            if field not in expected:
                continue
            if identity not in full or field not in full[identity]:
                check(
                    "evidence",
                    f"缺少 {description} 的 {field} 原始统计",
                    kind="unjudgeable",
                )
            else:
                check(
                    "quality",
                    f"{description} 的 {field}",
                    expected[field],
                    full[identity][field],
                    tolerance="0",
                )


def _decomposition_checks(
    spec: Document, actual: Document, check: Any, label: str
) -> None:
    for field in ("dimension", "metric", "unit"):
        if field in spec:
            check("presentation", f"{label}的 {field}", spec[field], actual.get(field))
    check("presentation", f"{label}可对账", True, actual.get("reconciled"))
    _compare_rows(
        dict(spec, key="key"), actual.get("rows"), check, "presentation", label
    )
    if "omitted" in spec:
        check(
            "presentation",
            f"{label}的未展开行数",
            spec["omitted"],
            actual.get("omitted"),
        )
    if "other" in spec:
        if spec["other"] is None:
            check("presentation", f"{label}的未展开汇总", None, actual.get("other"))
        else:
            remaining = actual.get("other") or {}
            for field, expected in spec["other"].items():
                check(
                    "quantity",
                    f"{label}的未展开汇总 {field}",
                    expected,
                    remaining.get(field),
                    tolerance=spec.get("tolerance"),
                )


def _investigation_checks(
    spec: Document, state: Document, queries: dict[str, Document], check: Any
) -> None:
    actual = state.get("investigation")
    if spec.get("absent"):
        check("scope", "未安排额外调查", None, actual)
        return
    record = actual or {}
    check("claim", "经营原因认定", spec["reason_status"], record.get("reason_status"))
    if "plan" in spec:
        plan = record.get("plan") or {}
        for key, value in spec["plan"].items():
            check("scope", f"调查计划 {key}", value, plan.get(key))
    drills = [step for step in record.get("steps", []) if step.get("kind") == "drill"]
    if "candidates" not in spec:
        return
    candidates = spec["candidates"]
    check(
        "scope",
        "下钻候选及先后顺序",
        [item["key"] for item in candidates],
        [step.get("parent_key") for step in drills],
    )
    for expected, step in zip(candidates, drills, strict=False):
        label = f"{expected['key']} 下钻"
        check("state", f"{label}执行状态", "verified", step.get("status"))
        for field, value in expected["plan"].items():
            check(
                "conditions",
                f"{label}条件 {field}",
                value,
                (step.get("query_plan") or {}).get(field),
            )
        query = queries.get(str(step.get("record_id")), {})
        if not query.get("sql"):
            check("evidence", f"缺少{label}的 SQL 回执", kind="unjudgeable")
        if "raw" in expected:
            _compare_rows(
                expected["raw"],
                query.get("rows"),
                check,
                "quantity",
                f"{label}原始数据",
            )
        _decomposition_checks(
            expected["decomposition"], step.get("decomposition") or {}, check, label
        )


def _score_turn(
    spec: Document, turn: Document, turns: list[Document], index: int, check: Any
) -> None:
    state = turn.get("state")
    if (
        not isinstance(state, dict)
        or not turn.get("record_id")
        or not state.get("task_status")
    ):
        check("evidence", "缺少原生运行记录或最终分析状态", kind="unjudgeable")
        return
    models = turn.get("model") or []
    if models and all(entry.get("error") for entry in models):
        check(
            "evidence",
            "模型请求失败，无法核验问题是否按业务条件处理",
            kind="unjudgeable",
        )
        return
    check("state", "业务任务状态", spec["task_status"], state["task_status"])
    if basis := spec.get("data_version"):
        check(
            "evidence",
            "数据版本",
            basis,
            state.get("data_version") or turn.get("data_version"),
        )
    if view := spec.get("view"):
        used_views = sorted(
            {step["view"] for step in state.get("steps", []) if step.get("view")}
        )
        check("conditions", "使用的数据视图", [view], used_views)
    if "parent" in spec:
        parent = spec["parent"]
        if type(parent) is not int or parent < 0 or parent >= index:
            raise ValueError("invalid expected parent")
        request = state.get("request") or {}
        check(
            "conditions",
            "继承的父记录",
            str(turns[parent].get("record_id")),
            str(request.get("parent_record_id") or request.get("selected_parent")),
        )
    elif (state.get("request") or {}).get("parent_record_id"):
        check(
            "conditions",
            "独立题目应从新会话发问",
            None,
            state["request"]["parent_record_id"],
        )
    if "plan" in spec:
        actual = state.get("semantic_plan") or {}
        for field, expected in spec["plan"].items():
            value = actual.get(field, ABSENT)
            if field == "metrics" and isinstance(value, list):
                value, expected = sorted(value), sorted(expected)
            if field == "filters" and isinstance(value, dict):
                value = {
                    key: item[0] if isinstance(item, list) and len(item) == 1 else item
                    for key, item in value.items()
                }
            check("conditions", f"采用条件 {field}", expected, value)
    if "decision_reason" in spec:
        check(
            "state",
            "限制原因",
            spec["decision_reason"],
            (state.get("decision") or {}).get("reason"),
        )
    if "outputs" in spec:
        _output_checks(spec["outputs"], state, check)
    presentation = state.get("presentation") or {}
    if "table" in spec:
        _compare_rows(
            spec["table"],
            (presentation.get("table") or {}).get("rows"),
            check,
            "presentation",
            "展示表格",
        )
    if "raw" in spec:
        _compare_rows(spec["raw"], turn.get("raw_rows"), check, "quantity", "原始数据")
    if spec["task_status"] == "blocked":
        check(
            "state",
            "受阻后无业务结果行",
            False,
            bool(turn.get("raw_rows") or (presentation.get("table") or {}).get("rows")),
        )
    if "decomposition" in spec:
        _decomposition_checks(
            spec["decomposition"],
            presentation.get("decomposition") or {},
            check,
            "分解结果",
        )
    queries = _query_checks(spec["query_scope"], turn, state, check)
    if "quality" in spec:
        _quality_checks(spec["quality"], turn, state, queries, check)
    if "investigation" in spec:
        _investigation_checks(spec["investigation"], state, queries, check)
    if text := turn.get("answer_text"):
        check(
            "claim",
            "自由文本中的方向、结果限制或经营原因需要人工复核",
            actual=text,
            kind="review",
        )


def _public(value: Any) -> Any:
    if value is ABSENT:
        return None
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    return value


def score(expectation: Document, observation: Document) -> Document:
    """Score each expected turn using full native receipts and explicit tolerances."""
    issues: list[Document] = []
    statuses = {"passed"}

    def add(dimension: str, detail: str, **extra: Any) -> None:
        issues.append({"dimension": dimension, "detail": detail, **extra})

    try:
        expected = expectation.get("turns")
        actual = observation.get("turns")
        if not expected or not isinstance(actual, list):
            add("evidence", "缺少已确认的独立预期或执行回执")
            return {"status": "unjudgeable", "issues": issues}
        if len(actual) < len(expected):
            add(
                "evidence",
                "未获得全部轮次回执",
                expected=len(expected),
                actual=len(actual),
            )
            statuses.add("unjudgeable")
        if len(actual) > len(expected):
            add("scope", "执行了额外轮次", expected=len(expected), actual=len(actual))
            statuses.add("failed")
        for index, (spec, turn) in enumerate(zip(expected, actual, strict=False)):
            if "task_status" not in spec or "query_scope" not in spec:
                add("evidence", "独立预期缺少业务状态或执行范围", turn=index + 1)
                statuses.add("unjudgeable")
                continue

            def check(
                dimension: str,
                detail: str,
                expected: Any = ABSENT,
                actual: Any = ABSENT,
                *,
                tolerance: Any = None,
                kind: str = "failed",
                turn_number: int = index + 1,
            ) -> bool:
                if kind != "failed" or not _equal(actual, expected, tolerance):
                    statuses.add(kind)
                    evidence = {
                        "expected": _public(expected),
                        "actual": _public(actual),
                    }
                    add(dimension, detail, turn=turn_number, **evidence)
                    return False
                return True

            _score_turn(spec, turn, actual, index, check)
    except Exception as error:
        add("evaluator", "评价器无法解析预期或回执", actual=type(error).__name__)
        return {"status": "evaluator_error", "issues": issues}
    status = next(
        name
        for name in ("failed", "unjudgeable", "review", "passed")
        if name in statuses
    )
    return {"status": status, "issues": issues}
