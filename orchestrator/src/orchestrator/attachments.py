"""Download a workspace attachment without passing its signed URL through the model."""

import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter

from .ide import IDE

MAX_BYTES = 25 * 1024 * 1024


class AttachmentDownload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Artifact(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=MAX_BYTES)


class InlineArtifact(Artifact):
    content_base64: str
    url: None = None


class StoredArtifact(Artifact):
    content_base64: None = None
    url: AnyHttpUrl


class Attachments:
    def __init__(self, context):
        self.context = context

    def attachment_download(self, sha256):
        request = AttachmentDownload(sha256=sha256)
        stored = TypeAdapter(
            InlineArtifact | StoredArtifact, config=ConfigDict(hide_input_in_errors=True)
        ).validate_python(
            IDE(self.context).request("GET", f"artifacts/{request.sha256}")
        )
        if stored.sha256 != request.sha256:
            raise ValueError("The platform returned a different attachment")
        if isinstance(stored, InlineArtifact):
            content = base64.b64decode(stored.content_base64, validate=True)
        else:
            if stored.url.scheme != "https":
                raise ValueError("Attachment downloads require HTTPS")
            # This separate request never carries the workspace's Authorization header.
            download = Request(str(stored.url), headers={"User-Agent": "autoresearch"})
            with urlopen(download, timeout=60) as response:
                content = response.read(stored.size_bytes + 1)
        if len(content) != stored.size_bytes or hashlib.sha256(content).hexdigest() != request.sha256:
            raise ValueError("Attachment size or SHA-256 verification failed")

        directory = self.context.home / "cache" / "attachments"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / request.sha256
        with TemporaryDirectory(dir=directory) as temporary:
            staged = Path(temporary) / request.sha256
            staged.write_bytes(content)
            staged.chmod(0o600)
            # Replace only after verification; the temporary and final files are private.
            staged.replace(target)
        return {"path": str(target), "sha256": request.sha256, "size_bytes": len(content)}
