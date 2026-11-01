"""Agent-owned v1 application contract. Generate contract/openapi.json from these models."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, create_model
from pydantic.json_schema import models_json_schema

VERSION = "1.0.0"


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Snapshot(Arguments):
    store: Literal["workspace", "coworld"]


class Messages(Arguments):
    session_id: str = Field(min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class Run(Arguments):
    run_id: str = Field(min_length=1, max_length=200)


class Events(Run):
    last_seq: int = Field(default=0, ge=0)


class Approval(Run):
    request_id: str
    choice: Literal["once", "deny"]


class Client(Arguments):
    id: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=40)


class OpenSession(Arguments):
    client: Client
    session_context: dict[str, JsonValue] | None = None
    capabilities: dict[str, JsonValue] | None = None


class Message(Arguments):
    session_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=16000)
    context: dict[str, JsonValue]


class Cancel(Arguments):
    op_id: UUID


class Command[Name, Args: Arguments](Arguments):
    op_id: UUID
    name: Name
    args: Args


class SnapshotCommand(Command[Literal["workspace.snapshot"], Snapshot]):
    pass


class OpenCommand(Command[Literal["session.open"], OpenSession]):
    pass


class MessageCommand(Command[Literal["session.message"], Message]):
    pass


class CancelCommand(Command[Literal["session.cancel"], Cancel]):
    pass


class MessagesCommand(Command[Literal["session.messages"], Messages]):
    pass


class GetCommand(Command[Literal["run.get"], Run]):
    pass


class EventsCommand(Command[Literal["run.events"], Events]):
    pass


class StopCommand(Command[Literal["run.stop"], Run]):
    pass


class ApproveCommand(Command[Literal["run.approve"], Approval]):
    pass


class Request(
    RootModel[
        Annotated[
            SnapshotCommand
            | OpenCommand
            | MessageCommand
            | CancelCommand
            | MessagesCommand
            | GetCommand
            | EventsCommand
            | StopCommand
            | ApproveCommand,
            Field(discriminator="name"),
        ]
    ]
):
    pass


class Reply(BaseModel):
    """Native replies may expose additional runtime fields; these are the guaranteed fields."""

    model_config = ConfigDict(extra="allow")


class OpenReply(Reply):
    session_id: str


class MessageReply(Reply):
    session_id: str
    run_id: str | None


class CancelReply(Reply):
    op_id: UUID
    stop_requested: bool


class SnapshotReply(Reply):
    store: Literal["workspace", "coworld"]
    seq: int
    records: dict[str, JsonValue]


class NativeMessage(Reply):
    id: int | str
    role: str
    content: JsonValue


class MessagesReply(Reply):
    data: list[NativeMessage]


class RunReply(Reply):
    run_id: str
    status: str


class RunEvent(Reply):
    event: str
    run_id: str
    seq: int | None = None


class Failure(Reply):
    kind: str
    detail: JsonValue | None = None


# These classes are both exported and used at the image's public boundary.
REQUESTS = (
    SnapshotCommand,
    OpenCommand,
    MessageCommand,
    CancelCommand,
    MessagesCommand,
    GetCommand,
    EventsCommand,
    StopCommand,
    ApproveCommand,
)
NAMES = frozenset(model.model_fields["name"].annotation.__args__[0] for model in REQUESTS)
RESPONSES = {
    "workspace.snapshot": SnapshotReply,
    "session.open": OpenReply,
    "session.message": MessageReply,
    "session.cancel": CancelReply,
    "session.messages": MessagesReply,
    "run.get": RunReply,
    "run.stop": Reply,
    "run.approve": Reply,
}


Responses = create_model("Responses", **{name: (model, ...) for name, model in RESPONSES.items()})


def openapi() -> dict:
    """Schema-only OpenAPI document; the platform continues to own HTTP and authentication."""
    models = (Request, Responses, RunEvent, Failure)
    _, schema = models_json_schema(
        [(model, "validation") for model in models], ref_template="#/components/schemas/{model}"
    )
    return {
        "openapi": "3.1.0",
        "info": {"title": "IDE agent application contract", "version": VERSION},
        "paths": {},
        "components": {"schemas": schema["$defs"]},
    }
