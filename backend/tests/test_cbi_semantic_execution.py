"""Opt-in integration tests for the isolated DATA-01 source and least-privilege reader."""

import json
import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from apps.chat.task.cbi_semantic import (
    SemanticIntent,
    TopicSemantics,
    build_query,
    present,
    resolve,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("CBI_DATA01_ROOT"), reason="requires isolated DATA-01 runtime"
)


@pytest.fixture(scope="module")
def source():
    root = Path(os.environ["CBI_DATA01_ROOT"])
    args = json.loads((root / "runtime/demo-readers.private.json").read_bytes())
    topic = TopicSemantics.model_validate(
        json.loads((root / "config/semantic.json").read_bytes())["orders"]
    )
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            host="127.0.0.1",
            port=55442,
            database="cbi_metabase_sample",
            username="cbi_demo_orders",
            password=args["orders"],
        ),
        poolclass=NullPool,
    )
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        assert conn.exec_driver_sql(
            "SELECT current_user, current_database()"
        ).one() == ("cbi_demo_orders", "cbi_metabase_sample")
        yield conn, topic
    engine.dispose()


def run(source, name, metrics, view="demo_bi.order_analysis", **changes):
    conn, topic = source
    plan = resolve(topic, SemanticIntent(operation="new", metrics=metrics, **changes))
    statement, sql, _ = build_query(topic, view, plan)
    rows = [dict(r) for r in conn.execute(statement).mappings()]
    if folder := os.getenv("CBI_CLOSE_EVIDENCE"):
        info = {"sql": sql, "plan": plan, "row_count": len(rows), "rows": rows[:4]}
        try:
            info["outputs"] = present(topic, plan, rows)[1]
        except Exception as error:
            info["presentation_error"] = type(error).__name__
        (Path(folder) / (name + ".json")).write_text(
            json.dumps(info, ensure_ascii=False, default=str, indent=2), encoding="utf8"
        )
    return topic, plan, rows


def test_repeat_count_only_has_one_row(source):
    _, _, rows = run(source, "nonempty", ["repeat_accounts"])
    assert rows == [{"repeat_accounts": 37}]


def test_repeat_count_empty_has_zero_row_value(source):
    topic, plan, rows = run(
        source, "empty", ["repeat_accounts"], filters={"product_id": 3}
    )
    assert rows == [{"repeat_accounts": 0}]
    assert present(topic, plan, rows)[1][0]["value"] == 0


def test_repeat_count_mixed_metrics_keep_one_row(source):
    _, _, rows = run(source, "mixed-nonempty", ["ordering_accounts", "repeat_accounts"])
    assert rows == [{"ordering_accounts": 312, "repeat_accounts": 37}]
    _, _, rows = run(
        source,
        "mixed-empty",
        ["order_count", "repeat_accounts", "billed", "discount"],
        filters={"product_id": 3},
    )
    assert rows == [
        {"order_count": 0, "repeat_accounts": 0, "billed": None, "discount": 0}
    ]


def test_repeat_count_comparison_keeps_two_periods(source):
    topic, plan, rows = run(
        source,
        "comparison",
        ["repeat_accounts"],
        compare_start=date(1900, 9, 1),
        compare_end=date(1900, 10, 1),
    )
    assert {r["period"]: r["repeat_accounts"] for r in rows} == {
        "current": 37,
        "baseline": 0,
    }
    assert len(rows) == 2
    values = {x["id"]: x["value"] for x in present(topic, plan, rows)[1]}
    assert (
        values["repeat_accounts_delta"] == 37 and values["repeat_accounts_rate"] is None
    )


def test_null_account_is_excluded_on_postgres(source):
    # Use a transaction-local values CTE via a temp view on a connection opened
    # solely for the test, never add rows to the source or published relation.
    conn, topic = source
    conn.rollback()
    try:
        conn.exec_driver_sql("SET TRANSACTION READ WRITE")
        conn.exec_driver_sql("""CREATE TEMP VIEW cbi_close_nullable AS
            SELECT * FROM (VALUES (1),(1),(2),(NULL),(NULL)) x(user_id)
            CROSS JOIN (SELECT DATE '2026-10-01' AS event_day) d""")
        _, _, rows = run(
            source,
            "null-accounts",
            ["repeat_accounts"],
            view="pg_temp.cbi_close_nullable",
        )
        assert rows == [{"repeat_accounts": 1}]
    finally:
        conn.rollback()
