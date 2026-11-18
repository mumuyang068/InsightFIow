from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy import text

from apps.db.db import get_session

from . import funnel_core, repurchase_core
from .contracts import binding_for, observation_seconds, validate_plan

TZ = timezone(timedelta(hours=8))


def epoch(value):
    return int(datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=TZ).timestamp())


def fingerprint(module):
    return sha256(Path(module.__file__).read_bytes()).hexdigest()


def fetch(source, topic, binding):
    schema, view = topic.view.split(".")
    statement = f'SELECT payload, manifest FROM "{schema}"."{view}" WHERE binding = :binding'
    with get_session(source) as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '10s'"))
        reader, readonly = conn.execute(text("select current_user,current_setting('transaction_read_only')")).one()
        if reader != topic.reader or readonly != "on":
            raise ValueError("method_source_not_readonly")
        row = conn.execute(text(statement), {"binding": binding}).one_or_none()
    if not row or row.manifest != topic.binding_manifests.get(binding):
        raise ValueError("binding_snapshot_unavailable")
    if {key: len(val) for key, val in row.payload.items()} != row.manifest["rows"]:
        raise ValueError("binding_input_incomplete")
    return row.payload, {"statement": statement, "params": {"binding": binding}, "reader": reader,
                         "read_only": True, "inputs": row.manifest}


def funnel(plan, facts, binding=None):
    events = [{**event, "t": int(event["t"])} for event in next(iter(facts.values()))]
    binding = binding_for(binding or plan["binding"])
    start, end, observation = (observation_seconds(binding, plan[key])
                               for key in ("entry_start", "entry_end", "observation_asof"))
    rows = funnel_core.observed_funnel(events, start, end, plan["window_hours"] * 3600, observation)
    unique = funnel_core.normalize(events)
    observed = {}
    for event in unique:
        if event["t"] <= observation:
            observed.setdefault(event["attempt"], []).append(event)
    for row in rows:
        group = observed[row["attempt"]]
        row["observed_events"] = sorted(group, key=lambda event: (event["t"], event["id"]))
        row["path_evidence"] = [
            {"event": name, "t": ts, "event_ids": sorted(e["id"] for e in group if (e["event"], e["t"]) == (name, ts))}
            for name, ts in row["strict_path"]
        ]
        channels = {(e.get("channel") or "unknown") for e in group if e["event"] == "view" and e["t"] == row["anchor"]}
        if len(channels) != 1:
            raise ValueError("anchor_channel_conflict")
        row["entry_channel"] = next(iter(channels))
    mature = [row for row in rows if row["mature_by_time"]]
    uncertain = [row["attempt"] for row in mature if row["ambiguous"]]
    members = [[row["attempt"] for row in mature if row["reached"] >= k] for k in (1, 2, 3)]
    counts = [len(group) for group in members]
    rate = lambda n, denominator: round(100 * n / denominator, 6) if denominator and not uncertain else None
    return {
        "status": "no_samples" if not rows else "no_mature" if not mature else "pending_sequence" if uncertain else "verified",
        "counts": counts, "numerator": counts[2], "denominator": counts[0],
        "rate_pct": rate(counts[2], counts[0]), "candidate_count": len(rows), "immature": len(rows) - len(mature),
        "members": members, "trace": rows, "uncertain": uncertain,
        "adjacent_pct": [rate(counts[1], counts[0]), rate(counts[2], counts[1])],
        "each_of_entry_pct": [rate(count, counts[0]) for count in counts],
        "future_events": sorted(e["id"] for e in unique if e["t"] > observation),
        "dedup": {"raw": len(events), "unique": len(unique)},
        "excluded": [{"attempt": key, "reason": "anchor_outside_entry_period" if any(e["event"] == "view" for e in events)
                      else "no_entry_event"} for key, events in sorted(observed.items()) if key not in {r["attempt"] for r in rows}],
    }


def repurchase(plan, facts, *, diagnostic=False):
    start, end, observation = (epoch(plan[k]) for k in ("entry_start", "entry_end", "observation_asof"))
    if plan["binding"] == "r11_repeat":
        orders = [{**row, "t": epoch(row["date"]), "amount": int(row["amount"]), "refund": int(row["refund"])}
                  for row in facts["r11-orders.csv"]]
    else:
        refunds = facts["e03-refunds.json"]
        orders = [{
            **row, "channel": None, "purpose": "normal",
            "refund": sum(r["amount"] for r in refunds if r["order"] == row["id"] and r["known_at"] <= observation),
            "refund_evidence": [r for r in refunds if r["order"] == row["id"] and r["known_at"] <= observation],
        } for row in facts["e03-orders.json"]]
    first, again = plan["cohort_channel"], plan["repeat_channel"]
    result = repurchase_core.calculate(orders, start, end, observation, plan["window_hours"] * 3600,
                                       plan["eligibility"] == "net", {"channel": first} if first else {},
                                       {"channel": again} if again else {}, diagnostic=diagnostic)
    result["candidate_count"] = result["denominator"] + result["immature"]
    result["status"] = ("verified" if result["denominator"] else "no_mature"
                        if result["candidate_count"] else "no_samples")
    result["verified_non_repeat"] = sorted(set(result["mature_members"]) - set(result["repeated_members"]))
    return result


def calculate(plan, facts, *, diagnostic=False, binding=None):
    validate_plan(plan, binding)
    if plan["method"] == "ordered_attempt_funnel":
        return funnel(plan, facts, binding), fingerprint(funnel_core)
    return repurchase(plan, facts, diagnostic=diagnostic), fingerprint(repurchase_core)
