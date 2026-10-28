"""Bounded source commands accepted by the installed profile CLI."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HermesPolicyFiles(BaseModel):
    """Policy source from the authenticated profile canonical repository, never an arbitrary host path."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["sources", "tree", "read", "write"]
    policy_name: str = Field(min_length=1, max_length=200)
    version_id: str = Field(default="", max_length=200)
    record_id: str = Field(default="", pattern=r"^(V-[A-Za-z0-9]+)?$")
    path: str = Field(default="", max_length=1024)
    offset: int = Field(default=0, ge=0, le=10000)
    content: str = Field(default="", max_length=60000)
    old_text: str = Field(default="", max_length=8000)
    new_text: str = Field(default="", max_length=8000)
    base_sha256: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")

    @model_validator(mode="after")
    def source_target(self) -> Self:
        if self.action != "sources" and not self.record_id:
            raise ValueError("File operations require an original version provenance record")
        if self.action in {"read", "write"} and not self.path:
            raise ValueError("File operations require a relative source file")
        if self.action == "write" and not self.base_sha256:
            raise ValueError("Writes require the SHA256 observed by the editor")
        if self.action != "write" and (self.content or self.base_sha256):
            raise ValueError("Read operations cannot carry a write")
        if self.path and (
            self.path.startswith("/")
            or "\\" in self.path
            or "\x00" in self.path
            or any(not part or part in {".", "..", ".git"} for part in self.path.split("/"))
        ):
            raise ValueError("Source paths must be relative without traversal")
        return self


class HermesProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coworld_id: str = Field(pattern=r"^cow_[a-f0-9-]{36}$")
    manifest_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    starter_id: str = Field(min_length=1, max_length=120)
    policy_name: str = Field(min_length=1, max_length=120)
    player_id: str | None = Field(default=None, pattern=r"^ply_[a-f0-9-]{36}$")
    runtime: Literal["game-hosted", "platform-hosted"]
    run: list[str] = Field(default_factory=list, max_length=100)
    source_url: str | None = Field(default=None, max_length=2048)
    image: str | None = Field(default=None, max_length=1024)
    file: str | None = Field(default=None, max_length=200)
    build_context: str = Field(default=".", max_length=240)
    dockerfile: str = Field(default="Dockerfile", max_length=240)


class HermesProjectSource(BaseModel):
    """A project is scoped canonical source, not a caller-supplied filesystem path or task."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["seed", "read", "write", "patch"]
    project_id: UUID
    league_id: str = Field(pattern=r"^league_[a-f0-9-]{36}$")
    conversation_id: str = Field(pattern=r"^ide_[a-f0-9-]{36}$")
    config: HermesProjectConfig | None = None
    archive_base64: str = Field(default="", max_length=700000)
    archive_sha256: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")
    archive_format: Literal["file", "zip"] = "file"
    include_contents: bool = False
    path: str = Field(default="", max_length=240)
    content: str = Field(default="", max_length=60000)
    old_text: str = Field(default="", max_length=8000)
    new_text: str = Field(default="", max_length=8000)
    source_revision: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")
    base_sha256: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")

    @model_validator(mode="after")
    def operation_fields(self) -> Self:
        if self.include_contents and self.action != "read":
            raise ValueError("Only reads can request a full source snapshot")
        if self.action == "seed":
            if self.config is None or not self.archive_base64 or not self.archive_sha256:
                raise ValueError("Seed requires published starter metadata and exact archive bytes")
        elif self.config is not None or self.archive_base64 or self.archive_sha256:
            raise ValueError("Existing projects retain their original starter identity")
        if self.action in {"write", "patch"} and (
            not self.path or not self.source_revision or not self.base_sha256
        ):
            raise ValueError("Save requires a file and both observed source identities")
        if self.action not in {"write", "patch"} and (
            self.content or self.source_revision or self.base_sha256
        ):
            raise ValueError("Only saves carry replacement content")
        if self.action == "patch":
            if not self.old_text or self.content:
                raise ValueError(
                    "Patch requires a nonempty unique old_text and new_text, not full content"
                )
        elif self.old_text or self.new_text:
            raise ValueError("Only patches carry text replacements")
        return self
