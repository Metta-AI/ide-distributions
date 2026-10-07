"""Pick the Queue backend from the environment (shared by the queue CLI and queue-keeper)."""

from collections.abc import Mapping

from autoresearch import runctx
from autoresearch.queue.base import Queue
from autoresearch.queue.file_queue import FileQueue
from autoresearch.queue.kanban_queue import KanbanQueue

ENV_QUEUE = "AUTORESEARCH_QUEUE"
FILE_QUEUE_RELATIVE = "file_queue"


def queue_from_env(env: Mapping[str, str], ctx: runctx.RunContext) -> Queue:
    """``AUTORESEARCH_QUEUE=file`` -> FileQueue under the canonical runtime dir; otherwise
    Kanban, where the board and worker profile are both named after the profile home."""
    if env.get(ENV_QUEUE) == "file":
        return FileQueue(runctx.runtime_dir(ctx) / FILE_QUEUE_RELATIVE)
    profile = ctx.hermes_home.name
    return KanbanQueue(board=profile, profile=profile)
