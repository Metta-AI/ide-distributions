"""Exercise the installed (image-pinned in CI) Hermes normalizer, not a copied implementation."""

import json
import subprocess

from research_core.messages import UserMessage


def test_pinned_hermes_accepts_adapted_content(hermes_bin, tmp_path):
    message = UserMessage.model_validate(
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Inspect the file"},
                {"type": "input_image", "image_url": "https://example.org/frame.png"},
                {"type": "input_file", "filename": "policy.txt", "file_data": "cG9saWN5"},
            ],
        }
    )
    content = message.hermes_input(tmp_path)[0]["content"]
    python = hermes_bin.parent / "python"
    script = """
import json, sys
from gateway.platforms.api_server import _normalize_multimodal_content
content = json.load(sys.stdin)
normalized = _normalize_multimodal_content(content)
assert normalized == content, normalized
try:
    _normalize_multimodal_content([{"type": "input_file", "file_data": "cG9saWN5"}])
except ValueError as error:
    assert "unsupported_content_type" in str(error)
else:
    raise AssertionError("Pinned file capability changed; review the adapter")
print("PASS installed Hermes text/image adapter; unsupported native file diagnosed")
"""
    result = subprocess.run(
        [str(python), "-c", script],
        input=json.dumps(content),
        text=True,
        capture_output=True,
        check=True,
    )
    assert "PASS installed Hermes" in result.stdout
