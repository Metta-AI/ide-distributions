import base64
import json

import pytest
from pydantic import ValidationError

from research_core.messages import UserMessage


def test_text_image_and_file_are_adapted_for_pinned_hermes(tmp_path):
    original = {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Review the attached policy"},
            {"type": "input_image", "image_url": "https://example.org/frame.png"},
            {
                "type": "input_file",
                "filename": "../../policy.bas",
                "file_data": base64.b64encode(b"PRINT 1").decode(),
            },
        ],
    }
    message = UserMessage.model_validate(original)
    (adapted,) = message.model_input(tmp_path)
    assert adapted["role"] == "user"
    assert adapted["content"][0] == {"type": "text", "text": "Review the attached policy"}
    assert adapted["content"][1]["image_url"]["url"] == "https://example.org/frame.png"
    ref = json.loads(adapted["content"][2]["text"])
    from pathlib import Path

    path = Path(ref["workspace_file"])
    assert path.is_relative_to(tmp_path) and path.read_bytes() == b"PRINT 1"
    assert message.model_input(tmp_path) == [adapted]
    assert original["content"][2]["type"] == "input_file"


@pytest.mark.parametrize(
    "part",
    [
        {"type": "input_audio", "input_audio": {"data": "abc", "format": "wav"}},
        {"type": "input_file", "filename": "x", "file_url": "http://localhost/secret"},
        {"type": "input_file", "filename": "x"},
    ],
)
def test_unsupported_content_is_rejected_before_dispatch(part):
    with pytest.raises(ValidationError):
        UserMessage.model_validate({"role": "user", "content": [part]})


def test_file_ids_reuse_verified_bytes_without_a_download(tmp_path):
    import hashlib

    message = UserMessage.model_validate(
        {
            "role": "user",
            "content": [
                {"type": "input_file", "filename": "policy", "file_data": "cG9saWN5"},
            ],
        }
    )
    original = message.model_input(tmp_path)
    file_id = "file-" + hashlib.sha256(b"policy").hexdigest()
    reference = UserMessage.model_validate(
        {
            "role": "user",
            "content": [
                {"type": "input_file", "filename": "policy", "file_id": file_id},
            ],
        }
    )
    assert reference.model_input(tmp_path) == original
    (tmp_path / "attachments" / file_id).write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        reference.model_input(tmp_path)
