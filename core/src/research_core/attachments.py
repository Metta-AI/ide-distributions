"""Resolve OpenAI file IDs, then let pinned Hermes ingest the bytes."""

import base64
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
from pydantic import BaseModel

from research_core import layout, platform
from research_core.messages import UserMessage

MAX_BYTES = 25 * 1024 * 1024


class AttachmentError(Exception):
    """A file that cannot be delivered; fail its admission, not the dispatcher."""


class Artifact(BaseModel):
    sha256: str
    size_bytes: int
    content_base64: str | None
    url: str | None


def prepare(
    message: UserMessage, *, ws: layout.Workspace, env: dict, hermes_bin: str
) -> UserMessage:
    content = message.root["content"]
    if isinstance(content, str) or all(p["type"] in {"text", "image_url"} for p in content):
        return message
    parts = []
    for part in content:
        if part["type"] == "file":
            file = dict(part["file"])
            if "file_id" in file:
                file_id = file.pop("file_id")
                if not re.fullmatch(r"[0-9a-f]{64}", file_id):
                    raise AttachmentError("Unknown file ID")
                client = platform.from_env(env, workspace=ws)
                player = client.whoami("player").subject_id
                artifact = Artifact.model_validate(
                    client.request("GET", f"/v2/cogents/{player}/artifacts/{file_id}")
                )
                if not 0 <= artifact.size_bytes <= MAX_BYTES:
                    raise AttachmentError("Attachments must be at most 25 MiB")
                if artifact.content_base64 is not None:
                    data = base64.b64decode(artifact.content_base64, validate=True)
                else:
                    with httpx.stream(
                        "GET", artifact.url, timeout=60, follow_redirects=True
                    ) as response:
                        response.raise_for_status()
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise AttachmentError("Attachment exceeds 25 MiB")
                            chunks.append(chunk)
                        data = b"".join(chunks)
                if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != file_id:
                    raise AttachmentError("Attachment bytes do not match their file ID")
                file["file_data"] = (
                    "data:application/octet-stream;base64," + base64.b64encode(data).decode()
                )
            if "file_data" not in file:
                raise AttachmentError("A file needs file_id or file_data")
            file.setdefault("filename", "attachment")
            parts.append({"type": "file", "file": file})
        else:
            parts.append(part)
    executable = Path(shutil.which(hermes_bin) or hermes_bin).resolve().parent / "python"
    result = subprocess.run(
        [str(executable), str(Path(__file__).with_name("native_attachments.py"))],
        input=json.dumps(
            {"workspace": str(ws.root), "message": {"role": "user", "content": parts}}
        ),
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    if result.returncode:
        raise AttachmentError(result.stderr[-2000:])
    return UserMessage.model_validate_json(result.stdout)
