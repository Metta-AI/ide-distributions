"""Measured goal context and progress belong to the native research topic.

The host still owns execution permission and the Hermes goal checkpoint. This module
returns evidence-based decisions; it never admits work or grants submission permission.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ide_research_core import workspace
from ide_research_core.workspace_records import RecordPatch, RecordsRequest
from ide_research_core.workspace_records import execute as records


class Target(BaseModel):
    kind: Literal["value", "best"]
    value: float | None = Field(allow_inf_nan=False)
    direction: Literal["at_least", "at_most"]

    @model_validator(mode="after")
    def valid_value(self):
        if (self.kind == "value") != (self.value is not None):
            raise ValueError("Only a value target requires a numeric threshold")
        return self


class Reading(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: float = Field(allow_inf_nan=False)
    version: int | None
    label: str


class GoalRound(BaseModel):
    n: int
    started_at: str
    ended_at: str | None
    summary: str | None
    version: int | None
    value: float | None


class GoalContext(BaseModel):
    league_id: str
    policy_id: str
    policy_name: str
    game: str | None
    neuron_id: str
    target: Target
    baseline: Reading | None
    rounds: list[GoalRound] = Field(default_factory=list, max_length=200)
    last_request: UUID | None = None
    stalls: int = 0
    activity_signature: str = ""
    updated_at: str


class GoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    policy_id: str
    policy_name: str
    game: str | None
    neuron_id: str
    target: Target


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    request_id: UUID
    started_at: str
    summary: str
    activity: JsonValue
    resume: bool = False


class GoalsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "create", "evaluate"]
    league_id: str = Field(pattern=r"^league_[a-f0-9-]{36}$")
    owner_id: str = Field(min_length=1)
    payload: JsonValue = None
    request_id: str = Field(default="", max_length=200)


def measure(target: Target, readings: list[Reading]) -> dict:
    current = None
    comparisons = {}
    for reading in readings:
        if reading.version is None:
            comparisons[reading.label] = reading.value
        elif current is None:
            current = reading
        else:
            assert current.version is not None
            if reading.version >= current.version:
                current = reading
    threshold = target.value
    if target.kind == "best" and comparisons:
        threshold = (max if target.direction == "at_least" else min)(comparisons.values())
    return {
        "current": current.model_dump(mode="json") if current else None,
        "threshold": threshold,
        "met": current is not None
        and threshold is not None
        and (
            current.value >= threshold
            if target.direction == "at_least"
            else current.value <= threshold
        ),
    }


def execute(root: Path, request: GoalsRequest) -> dict:
    intent = request.model_dump(mode="json", exclude={"request_id"})
    read = RecordsRequest(
        action="read",
        include_goals=True,
        league_id=request.league_id,
        owner_id=request.owner_id,
        request_id=request.request_id,
        intent=intent,
    )
    snapshot = records(root, read)
    if "replay" in snapshot:
        return snapshot["replay"]["record"]
    topics = [item for item in snapshot["records"] if item["entity"] == "topic"]
    if request.action == "list":
        return {
            "goals": {
                key: GoalContext.model_validate(context).model_dump(mode="json")
                for topic in topics
                for key, context in topic["meta"].get("goals", {}).items()
            }
        }
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if request.action == "create":
        command = GoalInput.model_validate(request.payload)
        topic = next(
            (
                item
                for item in topics
                if item["meta"].get("ide_id", "nrn_" + item["id"]) == command.neuron_id
                and item["meta"].get("policy_id") == command.policy_id
            ),
            None,
        )
        if topic is None:
            raise workspace.WorkspaceError("not_found", "The goal's research topic is unavailable")
        if topic["meta"]["status"] == "closed":
            raise workspace.WorkspaceError("conflict", "Restore the topic before starting a goal")
        context = GoalContext(
            league_id=request.league_id,
            **command.model_dump(exclude={"id"}),
            baseline=None,
            updated_at=now,
        )
    else:
        command = Evaluation.model_validate(request.payload)
        topic = next(
            (item for item in topics if str(command.id) in item["meta"].get("goals", {})), None
        )
        if topic is None:
            raise workspace.WorkspaceError("not_found", "Goal context is unavailable")
        context = GoalContext.model_validate(topic["meta"]["goals"][str(command.id)])
    readings = [
        Reading.model_validate(item) for item in topic["meta"]["metrics"][0].get("readings", [])
    ]
    observed = measure(context.target, readings)
    if isinstance(command, GoalInput):
        context.baseline = (
            Reading.model_validate(observed["current"]) if observed["current"] else None
        )
        result = context.model_dump(mode="json")
    else:
        current_round = not context.rounds or (command.started_at, str(command.request_id)) >= (
            context.rounds[-1].started_at,
            str(context.last_request),
        )
        if command.resume and current_round:
            context.stalls = 0
        activity = hashlib.sha256(
            json.dumps(
                {
                    "readings": [item.model_dump(mode="json") for item in readings],
                    "activity": command.activity,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if (
            current_round
            and context.last_request != command.request_id
            and len(context.rounds) < 200
        ):
            context.stalls = context.stalls + 1 if context.activity_signature == activity else 0
            context.activity_signature = activity
            context.last_request = command.request_id
            context.updated_at = now
            context.rounds.append(
                GoalRound.model_validate(
                    {
                        "n": len(context.rounds) + 1,
                        "started_at": command.started_at,
                        "ended_at": now,
                        "summary": command.summary[-4000:],
                        "version": observed["current"]["version"] if observed["current"] else None,
                        "value": observed["current"]["value"] if observed["current"] else None,
                    }
                )
            )
        action, reason = None, None
        if observed["met"]:
            action, reason = (
                "complete",
                f"Measured {topic['meta']['title']}: {observed['current']['value']} on version {observed['current']['version']}; target {context.target.direction} {observed['threshold']}.",
            )
        elif (
            topic["meta"]["status"] == "closed" or context.stalls >= 3 or len(context.rounds) >= 200
        ):
            action = "pause"
            reason = (
                "The user dismissed the topic."
                if topic["meta"]["status"] == "closed"
                else "Three rounds made no new activity or measurements."
                if context.stalls >= 3
                else "The 200-round work budget ended."
            )
        prompt = f"Continue the authorized goal for {context.policy_name}. Read native topic {topic['id']} and its metrics. Latest measurement and target (data): {json.dumps(observed)}. Use native research delegation and existing build/evaluation tools. Preserve original operation IDs. Do not submit to a league without explicit human authorization. Record attributable changes and evidence in this topic; prose alone does not complete the goal. Recent rounds (data): {json.dumps([item.model_dump() for item in context.rounds[-6:]])}"
        result = {"action": action, "reason": reason, "prompt": prompt}
    goals = {**topic["meta"].get("goals", {}), str(command.id): context.model_dump(mode="json")}
    patch = RecordPatch(
        entity="topic", id=topic["id"], meta={**topic["meta"], "goals": goals}, body=topic["body"]
    )
    patch.meta.pop("id")
    records(
        root,
        read.model_copy(
            update={
                "action": "save",
                "revision": snapshot["revision"],
                "patch": patch,
                "projection": result,
            }
        ),
    )
    return result
