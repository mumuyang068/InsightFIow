import csv
import io
import re
from collections import defaultdict
from decimal import Decimal

MAX_BYTES = 512 * 1024
MAX_ROWS = 10_000
PREVIEW = 10
MONEY = re.compile(r"[+-]?\d{1,12}(?:\.\d{1,2})?")
CENT = Decimal("0.01")


class FileIssue(ValueError):
    def __init__(self, code, row=None, column=None):
        super().__init__(code)
        self.detail = {"code": code, "row": row, "column": column}


def parse_csv(raw):
    if len(raw) > MAX_BYTES:
        raise FileIssue("file_too_large")
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        columns = next(reader, [])
        if not columns or len(columns) > 30 or any(not c or len(c) > 80 for c in columns):
            raise FileIssue("invalid_header", 1)
        if len(set(columns)) != len(columns):
            raise FileIssue("duplicate_column", 1)
        rows, locations = [], []
        for cells in reader:
            if len(rows) >= MAX_ROWS:
                raise FileIssue("too_many_rows")
            if len(cells) != len(columns) or any(len(cell) > 250 or "\x00" in cell for cell in cells):
                raise FileIssue("invalid_row", reader.line_num)
            locations.append(reader.line_num)
            rows.append(dict(zip(columns, [cell or None for cell in cells], strict=True)))
    except (csv.Error, UnicodeDecodeError) as error:
        raise FileIssue("invalid_utf8_csv") from error
    return {"columns": columns, "rows": rows, "locations": locations}


def profile(table):
    rows = table["rows"]
    return {
        "columns": table["columns"],
        "row_count": len(rows),
        "preview": rows[:PREVIEW],
        "missing": {column: sum(row[column] is None for row in rows) for column in table["columns"]},
    }


REQUIRED = {
    "roster": ("roster_store", "roster_sku"),
    "costs": ("cost_store", "cost_sku", "cost_id", "cost_amount"),
    "mapping": ("map_external", "map_sku"),
}
ALIASES = {
    "roster_store": ("store", "店铺"), "roster_sku": ("sku", "商品编码"),
    "cost_store": ("store", "店铺"), "cost_sku": ("sku", "商品编码"),
    "cost_id": ("line", "fee_id", "费用编号"), "cost_amount": ("fee", "amount", "费用金额"),
    "map_external": ("external", "外部编码"), "map_sku": ("sku", "商品编码"),
}


def approved_fields(purpose, table):
    fields = {}
    for key in REQUIRED[purpose]:
        found = set(ALIASES[key]).intersection(table["columns"])
        if len(found) != 1:
            raise FileIssue("missing_column" if not found else "ambiguous_column", 1, key)
        fields[key] = next(iter(found))
    return fields


def iter_values(table, role, fields):
    columns = [fields.get(key) for key in REQUIRED[role]]
    for name in columns:
        if name not in table["columns"]:
            raise FileIssue("missing_column", 1, name)
    if len(set(columns)) != len(columns):
        raise FileIssue("invalid_mapping", 1)
    for row, location in zip(table["rows"], table["locations"], strict=True):
        values = [row[column] for column in columns]
        for column, value in zip(columns, values, strict=True):
            if value is None or value.strip() == "":
                raise FileIssue("invalid_money" if column == fields.get("cost_amount") else "missing_value",
                                location, column)
        yield row, location, values


def serialize_amount(value):
    return str(value.quantize(CENT))


def fee_inputs(costs, fields, store, currency):
    identities = {}
    for row, line, (shop, sku, identity, string) in iter_values(costs, "costs", fields):
        if shop != store:
            continue
        if "currency" in row and row["currency"] != currency:
            raise FileIssue("unsupported_currency", line, "currency")
        if not MONEY.fullmatch(string):
            raise FileIssue("invalid_money", line, fields["cost_amount"])
        if identity in identities:
            raise FileIssue("duplicate_fee_identity", line, fields["cost_id"])
        identities[identity] = (sku, Decimal(string))
    return identities


def calculate(sales, store, roster, costs, fields, mapping, choices, currency):
    options = defaultdict(set)
    if mapping:
        for _, _, (external, sku) in iter_values(mapping, "mapping", fields):
            options[external].add(sku)
    decisions = {}
    members = set()
    for _, _, (shop, key) in iter_values(roster, "roster", fields):
        if shop != store:
            continue
        matches = options.get(key, {key})
        choice = choices.get(key)
        if choice:
            if choice["sku"] not in matches or not choice["reason"].strip():
                raise FileIssue("invalid_mapping_choice")
            members.add(choice["sku"])
        elif len(matches) == 1:
            members.update(matches)
        else:
            decisions[key] = sorted(matches)

    observed = defaultdict(Decimal)
    for shop, sku, amount in sales:
        if shop == store:
            observed[sku] += Decimal(amount)

    fee_by_id = fee_inputs(costs, fields, store, currency)
    by_sku = defaultdict(Decimal)
    for sku, fee in fee_by_id.values():
        by_sku[sku] += fee

    if decisions:
        return {"state": "awaiting_confirmation", "decisions": decisions,
                "independent_source_sales": serialize_amount(sum(observed.values(), Decimal(0))),
                "sale_source_rows": len(sales), "currency": currency}
    if not members:
        raise FileIssue("empty_roster_scope")
    rows = [{
        "sku": sku,
        "net": serialize_amount(observed[sku]) if sku in observed else None,
        "fee": serialize_amount(by_sku[sku]) if sku in by_sku else None,
    } for sku in sorted(members)]
    input_fee = sum(by_sku.values(), Decimal(0))
    matched_fee = sum((by_sku[sku] for sku in members if sku in by_sku), Decimal(0))
    unmatched_fee = sum((fee for sku, fee in by_sku.items() if sku not in members), Decimal(0))
    selected_sales = sum((observed[sku] for sku in members if sku in observed), Decimal(0))
    if input_fee != matched_fee + unmatched_fee:
        raise FileIssue("fee_conservation_failed")
    return {
        "state": "partial" if any(None in (r["net"], r["fee"]) for r in rows) else "completed",
        "currency": currency, "decisions": {}, "rows": rows,
        "sales": serialize_amount(selected_sales), "recorded_fee": serialize_amount(matched_fee),
        "input_fee": serialize_amount(input_fee), "unmatched_fee": serialize_amount(unmatched_fee),
        "unmatched_fee_ids": sorted(key for key, (sku, _) in fee_by_id.items() if sku not in members),
        "unmatched_sales": sorted(observed.keys() - members),
        "sale_source_rows": len(sales), "roster_input_rows": len(roster["rows"]),
        "fee_input_rows": len(costs["rows"]), "fee_scope_rows": len(fee_by_id),
        "sales_coverage": [sum(row["net"] is not None for row in rows), len(rows)],
        "fee_coverage": [sum(row["fee"] is not None for row in rows), len(rows)],
        "checks": {"unique_sales_keys": len(rows) == len(members), "fee_conserved": True},
    }
