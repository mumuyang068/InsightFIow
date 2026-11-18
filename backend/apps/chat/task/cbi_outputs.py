from decimal import ROUND_HALF_UP, Decimal


def comparison(current, baseline):
    if current is None or baseline is None:
        return None, None
    delta = Decimal(str(current)) - Decimal(str(baseline))
    rate = None if baseline <= 0 else delta / Decimal(str(baseline)) * 100
    return delta, rate


def presentation(semantics, units):
    mode = {"additive_partition": "stacked", "alternative_definitions": "grouped"}.get(
        semantics, "grouped"
    )
    return {
        "series_mode": mode if len(set(units)) == 1 else "none",
        "series_semantics": semantics,
        "units": units,
    }


def number(value):
    if isinstance(value, Decimal):
        return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
    return value


def outputs(state, receipts, evidence=None):
    scope = state["scope"]
    by_date = {
        r["date"]: r
        for r in receipts
        if r["store"] == scope["store"] and r["definition"] == scope["definition"]
    }
    now = by_date.get(scope["current"], {})
    before = by_date.get(scope["baseline"], {})
    delta, rate = comparison(now.get("amount"), before.get("amount"))
    target = now.get("target")
    amount = now.get("amount")
    attainment = (
        Decimal(str(amount)) / Decimal(str(target)) * 100
        if target not in (None, 0) and amount is not None
        else None
    )
    values = {
        "current": amount,
        "baseline": before.get("amount"),
        "delta": delta,
        "rate": rate,
        "target": target,
        "attainment": attainment,
    }
    field_bindings = {
        "current": [(now, "amount")],
        "baseline": [(before, "amount")],
        "delta": [(now, "amount"), (before, "amount")],
        "rate": [(now, "amount"), (before, "amount")],
        "target": [(now, "target")],
        "attainment": [(now, "amount"), (now, "target")],
    }
    result = []
    for key in state["request"]["outputs"]:
        if key == "refund_members":
            result.append(
                {
                    "id": key,
                    "status": evidence["status"] if evidence else "missing",
                    "reason": evidence.get("reason")
                    if evidence
                    else "evidence_pending",
                    "references": evidence.get("references", []) if evidence else [],
                }
            )
            continue
        value = values[key]
        inapplicable = (key == "rate" and delta is not None and rate is None) or (
            key == "attainment" and amount is not None and target == 0
        )
        result.append(
            {
                "id": key,
                "value": number(value),
                "unit": "%" if key in {"rate", "attainment"} else "CNY",
                "status": "verified"
                if value is not None
                else "not_applicable"
                if inapplicable
                else "missing",
                "reason": "nonpositive_baseline"
                if key == "rate" and inapplicable
                else "zero_target"
                if inapplicable
                else "missing_data"
                if value is None
                else None,
                "references": [
                    {
                        "record_id": row["record_id"],
                        "field": field,
                        "date": row["date"],
                        "definition": row["definition"],
                    }
                    for row, field in field_bindings[key]
                    if row and row.get(field) is not None
                ],
            }
        )
    return result


def completion(items):
    if items and all(i["status"] in {"verified", "not_applicable"} for i in items):
        return "completed"
    return "partial" if any(i["status"] == "verified" for i in items) else "blocked"
