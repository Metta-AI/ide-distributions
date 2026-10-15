"""Worker-side staging: write an outbound message into the worktree (Overview C5).

A card worker stages messages at ``comms/staged/<M-id>.json`` in its own worktree. They reach
canonical when the card merges, and the runtime then publishes them to ``comms/outbox/``
(``publisher.publish_staged``). Refs default to ``rev: "@result"``, the commit the card merges
as, so a ref to an entity the card created resolves at the published revision.
"""

from ide_research_core.comms.envelope import FROM_AGENT, Ref, build_message, check_message
from ide_research_core.io.docs import write_json
from ide_research_core.paths import Layout

__all__ = ["Ref", "stage"]


def stage(layout: Layout, kind: str, title: str, body: str, refs: list, **fields) -> str:
    """Validate and write one staged message under ``layout`` (the worker worktree); return
    its id. Raises ``MessageProblems`` and writes nothing if the message is invalid."""
    message = build_message(kind, title, body, refs, FROM_AGENT, **fields)
    check_message(message)
    write_json(layout.comms_staged / f"{message['id']}.json", message)
    return message["id"]
