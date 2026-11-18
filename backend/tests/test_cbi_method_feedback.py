from fastapi import HTTPException
import pytest

from apps.chat.api.cbi_method_feedback import transition


def test_feedback_requires_transition_and_comparison():
    transition("pending", "processing", None, None)
    transition("processing", "needs_info", None, None)
    transition("needs_info", "processing", None, None)
    transition("processing", "resolved", "condition_difference", {"run_id": "other"})
    with pytest.raises(HTTPException) as missing:
        transition("processing", "resolved", "condition_difference", None)
    assert missing.value.status_code == 422
    with pytest.raises(HTTPException) as closed:
        transition("resolved", "processing", None, None)
    assert closed.value.status_code == 409
