"""One retained workspace intent; retries only query existing platform publications."""

import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, JsonValue

from ide_research_core.io.locks import file_lock
from ide_research_core.softmax_tool.protocol import ArtifactType, PublishedPolicy, Refused, Softmax
from ide_research_core.source_io import write_file


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, JsonValue]
    policy: PublishedPolicy | None = None


def recover(storage: Path, operation_id: UUID, sm: Softmax) -> Publication:
    """Read the saved inputs; absence is unresolved, never permission to upload again."""
    relative = f"{operation_id}.json"
    with file_lock(storage / f"{operation_id}.lock"):
        intent = Publication.model_validate_json((storage / relative).read_bytes())
        if sm.require_player().player_id != intent.inputs["player_id"]:
            raise Refused("wrong_player", "Publication belongs to another workspace player")
        if intent.policy is None:
            intent.policy = sm.publication_status(intent.inputs)
            write_file(storage, relative, intent.model_dump_json().encode())
        return intent


def publish(
    storage: Path,
    artifact: Path,
    name: str,
    player_id: str,
    artifact_type: ArtifactType,
    sm: Softmax,
) -> tuple[UUID, Publication]:
    """Persist exact inputs before upload; an existing intent always takes the read-only path."""
    if sm.require_player().player_id != player_id:
        raise Refused("wrong_player", "Publication must use the workspace player")
    inputs = sm.upload_inputs(artifact, name, player_id=player_id, artifact_type=artifact_type)
    operation_id = uuid5(NAMESPACE_URL, json.dumps(inputs, sort_keys=True))
    relative = f"{operation_id}.json"
    with file_lock(storage / f"{operation_id}.lock"):
        if not (storage / relative).exists():
            # Also confirms the installed lookup/API contract before attempting publication.
            policy = sm.publication_status(inputs)
            write_file(
                storage,
                relative,
                Publication(inputs=inputs, policy=policy).model_dump_json().encode(),
            )
            if policy is None:
                sm.upload(artifact, name, player_id=player_id, artifact_type=artifact_type)
    return operation_id, recover(storage, operation_id, sm)
