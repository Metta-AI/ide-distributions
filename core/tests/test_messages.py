"""Protect the SDK's lazy iterable boundary and attachment reference validation."""

import pytest
from pydantic import ValidationError

from research_core.messages import UserMessage


def test_sdk_message_parts_survive_multiple_serializations():
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Read my notes"},
            {"type": "file", "file": {"file_id": "abc", "filename": "notes.txt"}},
            {"type": "image_url", "image_url": {"url": "https://example.com/chart.png"}},
        ],
    }
    parsed = UserMessage.model_validate(message)
    assert parsed.model_dump(mode="json") == message
    assert (
        UserMessage.model_validate_json(parsed.model_dump_json()).model_dump(mode="json") == message
    )
    assert parsed.model_dump(mode="json") == message


@pytest.mark.parametrize(
    "file", [{}, {"file_id": "abc", "file_data": "data:text/plain;base64,eA=="}]
)
def test_file_needs_one_source(file):
    with pytest.raises(ValidationError, match="exactly one"):
        UserMessage.model_validate({"role": "user", "content": [{"type": "file", "file": file}]})
