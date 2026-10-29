"""Workspace-owned publication intent and read-only recovery on retained storage.

The original operation UUID is deterministic for one immutable publication intent and
is persisted before upload. An interrupted attempt is queried, never uploaded again.
"""

from pathlib import Path
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ide_research_core.io.locks import file_lock
from ide_research_core.softmax_tool.protocol import ArtifactType, PublishedPolicy, Refused, Softmax
from ide_research_core.source_io import write_file
from ide_research_core.source_snapshots import SourceSnapshot, promote_sources


class PublicationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    player_id: str
    policy_name: str
    artifact_type: ArtifactType
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @property
    def operation_id(self) -> UUID:
        return uuid5(NAMESPACE_URL, "softmax:workspace-publication:v1:" + self.model_dump_json())


class PendingPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pending"] = "pending"
    operation_id: UUID
    intent: PublicationIntent


class CompletedPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["completed"] = "completed"
    operation_id: UUID
    intent: PublicationIntent
    policy: PublishedPolicy


Publication = Annotated[PendingPublication | CompletedPublication, Field(discriminator="kind")]
publication_adapter = TypeAdapter(Publication)


def recover(storage: Path, operation_id: UUID, sm: Softmax) -> Publication:
    """Resolve only this original operation; an absent platform receipt stays pending."""
    relative = f"operations/{operation_id}.json"
    with file_lock(storage / "operations" / f"{operation_id}.lock"):
        receipt = publication_adapter.validate_json((storage / relative).read_bytes())
        if receipt.operation_id != operation_id or receipt.intent.operation_id != operation_id:
            raise ValueError("Publication receipt does not match its operation")
        if sm.require_player().player_id != receipt.intent.player_id:
            raise Refused("wrong_player", "Publication belongs to another workspace player")
        if isinstance(receipt, CompletedPublication):
            return receipt
        policy = sm.publication_status(operation_id)
        if policy is None:
            return receipt
        if policy.name != receipt.intent.policy_name:
            raise ValueError("Platform publication does not match the retained policy name")
        if receipt.intent.source_snapshot is not None:
            promote_sources(
                storage,
                snapshot=receipt.intent.source_snapshot,
                version_id=policy.id,
                policy_name=policy.name,
                artifact_sha256=receipt.intent.artifact_sha256,
            )
        completed = CompletedPublication(
            operation_id=operation_id, intent=receipt.intent, policy=policy
        )
        write_file(storage, relative, completed.model_dump_json().encode())
        return completed


def publish(storage: Path, intent: PublicationIntent, artifact: Path, sm: Softmax) -> Publication:
    """Admit once, then recover the exact platform UUID; storage survives worker cleanup."""
    from ide_research_core.softmax_tool.upload import artifact_sha256

    if sm.require_player().player_id != intent.player_id:
        raise Refused("wrong_player", "Publication must use the workspace player")
    if artifact_sha256(artifact) != intent.artifact_sha256:
        raise Refused("artifact_mismatch", "Publication artifact changed after validation")
    if intent.source_snapshot is not None:
        snapshot = SourceSnapshot.model_validate_json(
            (storage / "staged" / intent.source_snapshot / "snapshot.json").read_bytes()
        )
        if snapshot.digest != intent.source_snapshot:
            raise ValueError("Retained source snapshot does not match publication intent")
    operation_id = intent.operation_id
    relative = f"operations/{operation_id}.json"
    with file_lock(storage / "operations" / f"{operation_id}.lock"):
        path = storage / relative
        if not path.exists():
            existing = sm.publication_status(operation_id)
            receipt = PendingPublication(operation_id=operation_id, intent=intent)
            write_file(storage, relative, receipt.model_dump_json().encode())
            if existing is None:
                sm.upload(
                    artifact,
                    intent.policy_name,
                    player_id=intent.player_id,
                    artifact_type=intent.artifact_type,
                    operation_id=operation_id,
                )
    return recover(storage, operation_id, sm)


def reconcile(storage: Path, sm: Softmax) -> list[Publication]:
    """Startup/retry reconciliation makes reads and source promotions, never uploads."""
    return [
        recover(storage, UUID(path.stem), sm)
        for path in sorted((storage / "operations").glob("*.json"))
        if isinstance(publication_adapter.validate_json(path.read_bytes()), PendingPublication)
    ]
