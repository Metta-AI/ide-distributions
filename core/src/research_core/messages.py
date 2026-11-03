"""OpenAI Responses user-message content and the pinned Hermes input adapter.

Files are materialized in the workspace because the pinned gateway accepts native text and
images, but rejects file/audio parts. The original typed message remains the durable receipt.
"""

import base64
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Content(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputText(Content):
    type: Literal["input_text"]
    text: str = Field(min_length=1)


class InputImage(Content):
    type: Literal["input_image"]
    image_url: str = Field(min_length=1)
    detail: Literal["auto", "low", "high"] = "auto"


class InputFile(Content):
    type: Literal["input_file"]
    filename: str = Field(min_length=1, max_length=255)
    file_data: str | None = None
    file_url: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.file_data is None) == (self.file_url is None):
            raise ValueError("input_file requires exactly one of file_data or file_url")
        if self.file_url is not None and urlparse(self.file_url).scheme != "https":
            raise ValueError("file_url requires HTTPS")
        return self


class UserMessage(Content):
    role: Literal["user"]
    content: list[Annotated[InputText | InputImage | InputFile, Field(discriminator="type")]] = (
        Field(min_length=1, max_length=32)
    )

    def text(self) -> str:
        return "\n".join(part.text for part in self.content if isinstance(part, InputText))

    def hermes_input(self, documents: Path) -> list[dict]:
        parts = []
        for part in self.content:
            if isinstance(part, InputText):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, InputImage):
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": part.image_url,
                            "detail": part.detail,
                        },
                    }
                )
            else:
                source = part.file_data if part.file_data is not None else part.file_url
                key = hashlib.sha256(source.encode()).hexdigest()
                target = documents / "attachments" / key
                if not target.is_file():
                    if part.file_data is not None:
                        if len(part.file_data) > 36 * 1024 * 1024:
                            raise ValueError("input_file exceeds 25 MiB")
                        data = base64.b64decode(part.file_data.split(",", 1)[-1], validate=True)
                    else:
                        with urlopen(part.file_url, timeout=60) as response:
                            data = response.read(25 * 1024 * 1024 + 1)
                    if len(data) > 25 * 1024 * 1024:
                        raise ValueError("input_file exceeds 25 MiB")
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    temporary = target.with_suffix(".pending")
                    temporary.write_bytes(data)
                    temporary.chmod(0o600)
                    temporary.replace(target)
                parts.append(
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "filename": part.filename,
                                "workspace_file": str(target),
                            }
                        ),
                    }
                )
        return [{"role": "user", "content": parts}]


def transcript_key(content) -> str:
    """Hermes collapses all-text content into a string; compare that native representation."""
    if isinstance(content, list) and all(part["type"] == "text" for part in content):
        content = "\n".join(part["text"] for part in content)
    return json.dumps(content, sort_keys=True)
