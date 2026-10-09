"""Pick the Queue backend from the environment (shared by the queue CLI and queue-keeper)."""

from collections.abc import Mapping

from autoresearch import runctx
from autoresearch.queue.base import Queue
from autoresearch.queue.file_queue import FileQueue
from autoresearch.queue.kanban_queue import KanbanQueue

ENV_QUEUE = "AUTORESEARCH_QUEUE"
FILE_QUEUE_RELATIVE = "file_queue"


def queue_from_env(env: Mapping[str, str], ctx: runctx.RunContext) -> Queue:
    """Kanban, where the board and worker profile are both named after the profile home.

    ``AUTORESEARCH_QUEUE=file`` is a test hook (like ``AUTORESEARCH_SOFTMAX=fake``): it selects
    the FileQueue test double under the canonical runtime dir, so tests can run the real CLI
    entry points without Hermes. It is not an installer setting and is not on
    ``terminal.env_passthrough``: no shipped job runs FileQueue cards (amendment A13)."""
    if env.get(ENV_QUEUE) == "file":
        return FileQueue(runctx.runtime_dir(ctx) / FILE_QUEUE_RELATIVE)
    profile = ctx.hermes_home.name
    return KanbanQueue(board=profile, profile=profile)
