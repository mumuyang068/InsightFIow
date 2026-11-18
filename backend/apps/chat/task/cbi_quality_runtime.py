"""Use deterministic quality results to decide which requested outputs may execute."""

from dataclasses import dataclass
from hashlib import sha256
from json import dumps


@dataclass
class Disposition:
    plan: dict | None
    status: str
    unavailable: list[dict]


def decide(topic, plan, report):
    affected = {}
    requested = list(dict.fromkeys([*plan["metrics"], *plan["overall_metrics"]]))
    observed = {
        scope["scope"]: scope["checked_count"] for scope in report.get("coverage", ())
    }
    for result in report["results"]:
        status = result["status"]
        if result["severity"] != "failure":
            continue
        if status == "no_samples" and (
            result["type"] == "freshness" or observed.get(result["scope"], 0) > 0
        ):
            status = "incomplete"
        if status in {"failed", "incomplete"}:
            reason = (
                "quality_incomplete" if status == "incomplete" else "quality_blocked"
            )
            affected.update(dict.fromkeys(result["metrics"], reason))
    if report["status"] == "incomplete" and not affected:
        affected.update(dict.fromkeys(requested, "quality_incomplete"))
    affected = {key: reason for key, reason in affected.items() if key in requested}
    if affected and plan["detail"]:
        affected.update(
            {key: "quality_blocked" for key in requested if key not in affected}
        )
    unavailable = [
        {"key": key, "label": topic.metrics[key].label, "reason": affected[key]}
        for key in requested
        if key in affected
    ]
    if not unavailable:
        return Disposition(plan, report["status"], [])
    working = dict(
        plan,
        metrics=[key for key in plan["metrics"] if key not in affected],
        overall_metrics=[key for key in plan["overall_metrics"] if key not in affected],
    )
    if working["metrics"]:
        return Disposition(working, "partial", unavailable)
    if working["overall_metrics"] and not plan["detail"]:
        independent = dict(
            working,
            group="none",
            metrics=working["overall_metrics"],
            overall_metrics=[],
        )
        return Disposition(independent, "partial", unavailable)
    incomplete = any(item["reason"] == "quality_incomplete" for item in unavailable)
    return Disposition(None, "incomplete" if incomplete else "blocked", unavailable)


def public_evidence(report, disposition, report_ids, checked_at):
    return {
        "status": disposition.status,
        "report_ids": report_ids,
        "checked_at": checked_at,
        "checks": [
            {
                "rule_id": item["rule_id"],
                "type": item["type"],
                "severity": item["severity"],
                "status": item["status"],
                "scope": item["scope"],
                "affected_metrics": item["metrics"],
            }
            for item in report["results"]
        ],
        "unavailable_outputs": disposition.unavailable,
    }


def missing_outputs(unavailable):
    return [
        {
            "id": item["key"],
            "label": item["label"],
            "value": None,
            "status": "missing",
            "reason": item["reason"],
        }
        for item in unavailable
    ]


def record_evidence(state, plan, public):
    # A resumed step replaces its current disclosure. Original reports remain
    # in their own records and under the preceding run.
    context = sha256(
        dumps(plan, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:24]
    entries = state.setdefault("quality_by_scope", {})
    entries[context] = public
    priority = (
        "incomplete",
        "blocked",
        "partial",
        "failed",
        "warning",
        "no_samples",
        "passed",
        "not_applicable",
    )
    available_statuses = {entry["status"] for entry in entries.values()}
    state["quality"] = {
        "status": next(status for status in priority if status in available_statuses),
        "checked_at": public["checked_at"],
        "checks": [check for entry in entries.values() for check in entry["checks"]],
        "report_ids": [
            key for entry in entries.values() for key in entry["report_ids"]
        ],
        "unavailable_outputs": list(
            {
                item["key"]: item
                for entry in entries.values()
                for item in entry["unavailable_outputs"]
            }.values()
        ),
    }
