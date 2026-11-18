from decimal import Decimal

import pytest

from apps.chat.task.cbi_multi_files import FileIssue, calculate, parse_csv, profile


def csv(text):
    return parse_csv(text.encode("utf-8"))


ROSTER = "store,sku\nA,SKU-A\nA,SKU-B\n"
COSTS = "line,store,sku,fee\nF01,A,SKU-A,200\nF02,A,SKU-A,100\nF03,A,SKU-B,100\n"
SALES = [("A", "SKU-A", Decimal("900")), ("A", "SKU-B", Decimal("500"))]
FIELDS = {
    "roster_store": "store", "roster_sku": "sku", "cost_store": "store", "cost_sku": "sku",
    "cost_id": "line", "cost_amount": "fee",
}


def run(costs=COSTS, roster=ROSTER, mapping=None, choices=None, fields=None):
    return calculate(
        SALES, "A", csv(roster), csv(costs), fields or FIELDS,
        csv(mapping) if mapping else None, choices or {}, "CNY",
    )


def test_text_codes_and_ambiguity():
    roster = "store,sku\nA,001\nA,SKU-B\n"
    mapping = "external,sku\n001,SKU-A\n001,SKU-B\n"
    assert csv(roster)["rows"][0]["sku"] == "001"
    result = run(roster=roster, mapping=mapping, fields={**FIELDS, "map_external": "external", "map_sku": "sku"})
    assert result["state"] == "awaiting_confirmation"
    assert result["decisions"] == {"001": ["SKU-A", "SKU-B"]}
    resolved = run(roster=roster, mapping=mapping, fields={**FIELDS, "map_external": "external", "map_sku": "sku"},
                   choices={"001": {"sku": "SKU-A", "reason": "资料所有者确认"}})
    assert resolved["sales"] == "1400.00"


def test_previews_are_not_the_execution_input():
    costs = "line,store,sku,fee\n" + "".join(f"F{i},A,SKU-A,{10 if i < 100 else 1000}\n" for i in range(101))
    assert len(profile(csv(costs))["preview"]) < 101
    assert run(costs)["recorded_fee"] == "2000.00"


def test_duplicate_fee_identity_is_rejected():
    with pytest.raises(FileIssue, match="duplicate_fee_identity"):
        run(COSTS + "F02,A,SKU-B,199\n")


def test_many_fee_rows_do_not_expand_sales():
    result = run()
    assert result["sales"] == "1400.00"
    assert result["recorded_fee"] == "400.00"
    assert result["rows"][0]["fee"] == "300.00"


def test_missing_zero_unmatched_and_revisions():
    missing = run(COSTS.rsplit("F03", 1)[0])
    assert missing["rows"][1]["net"] == "500.00"
    assert missing["rows"][1]["fee"] is None
    assert missing["recorded_fee"] == "300.00"
    zero = run(COSTS.replace("SKU-B,100", "SKU-B,0"))
    assert zero["rows"][1]["fee"] == "0.00"
    unmatched = run(COSTS + "F04,A,SKU-C,50\n")
    assert unmatched["input_fee"] == "450.00"
    assert unmatched["unmatched_fee"] == "50.00"
    assert unmatched["recorded_fee"] == "400.00"
    assert run(COSTS.replace("F02,A,SKU-A,100", "F02,A,SKU-A,150"))["recorded_fee"] == "450.00"


@pytest.mark.parametrize(("rows", "error"), [
    ("line,store,sku,fee\nF01,A,SKU-A,NaN\n", "invalid_money"),
    ("line,store,sku,fee\nF01,A,SKU-A,\n", "invalid_money"),
    ("line,store,sku,fee,currency\nF01,A,SKU-A,1,USD\n", "unsupported_currency"),
    ("line,store,sku\nF01,A,SKU-A\n", "missing_column"),
])
def test_bad_inputs_never_turn_into_money(rows, error):
    with pytest.raises(FileIssue, match=error):
        run(rows)


def test_cross_store_same_sku_does_not_double_count():
    result = run(COSTS + "F01,B,SKU-A,500\n", ROSTER + "B,SKU-A\n")
    assert result["sales"] == "1400.00"
    assert result["input_fee"] == "400.00"
