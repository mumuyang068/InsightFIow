import asyncio
from inspect import unwrap
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from apps.chat.task import cbi_analysis


@pytest.fixture
def export(monkeypatch):
    from apps.chat.api import chat

    monkeypatch.setattr(cbi_analysis, "check_saved_access", lambda *_: None)
    monkeypatch.setattr(chat, "get_chat_chart_data", lambda **_: {"data": []})
    monkeypatch.setattr(
        chat,
        "get_chart_config",
        lambda *_: {
            "title": "品类比较",
            "columns": [{"name": "品类", "value": "dimension"}],
        },
    )

    def get_workbook(state):
        record = SimpleNamespace(
            create_by=1, chat_id=2, analysis_state=state, predict_record_id=None
        )
        session = SimpleNamespace(get=lambda *_: record)

        async def request():
            response = await unwrap(chat.export_excel)(
                session, SimpleNamespace(id=1), 3, 2, lambda key: key
            )
            content = b"".join([chunk async for chunk in response.body_iterator])
            return load_workbook(BytesIO(content), read_only=True)

        return asyncio.run(request())

    return get_workbook


def test_empty_reconciled_investigation_exports_totals_and_evidence(export):
    state = {
        "presentation": {"decomposition": {"reconciled": True, "rows": []}},
        "scope": {"current": "2026-10-31"},
        "request": {"parent_record_id": "4"},
        "outputs": [{"id": "amount_delta", "value": "0", "status": "verified"}],
        "investigation": {
            "status": "completed",
            "reason_status": "unconfirmed",
            "stop_reason": "no_nonzero_groups",
            "plan": {"method_version": "additive-change-v1"},
            "steps": [],
            "findings": [],
        },
    }

    workbook = export(state)
    try:
        assert list(workbook["Sheet1"].values) == [("品类",)]
        assert list(workbook["Outputs"].values)[1] == ("amount_delta", "0", "verified")
        assert workbook["Investigation"]["C2"].value == "no_nonzero_groups"
    finally:
        workbook.close()


def test_empty_ordinary_chart_keeps_existing_export_error(export):
    with pytest.raises(HTTPException) as error:
        export(None)

    assert error.value.status_code == 500
    assert error.value.detail == "i18n_excel_export.data_is_empty"
