"""Pick the Queue backend from the environment (shared by the queue CLI and queue-keeper)."""

from collections.abc import Mapping
from typing import get_args

from autoresearch import runctx
from autoresearch.queue.base import Queue
from autoresearch.queue.card import Mode
from autoresearch.queue.file_queue import FileQueue
from autoresearch.queue.kanban_queue import KanbanQueue

ENV_QUEUE = "AUTORESEARCH_QUEUE"
FILE_QUEUE_RELATIVE = "file_queue"
ENV_CARD_MODELS = "AUTORESEARCH_CARD_MODELS"
# Mechanical modes (queue grooming, scouting reads, hub upkeep) run on the cheaper fallback
# model; investigation and experimentation keep the profile default (box spend report,
# 2026-10-08: these modes were about $9 of $36.5, all on kimi-k3).
CHEAP_MODEL = "z-ai/glm-5.3"
DEFAULT_CARD_MODELS = {"planning": CHEAP_MODEL, "scouting": CHEAP_MODEL, "maintenance": CHEAP_MODEL}
DEFAULT_MODEL_WORD = "default"  # a mode set to this runs on the profile default
NO_MODELS = "none"  # every mode runs on the profile default


def card_models(env: Mapping[str, str]) -> dict[str, str]:
    """Card mode -> model from ``AUTORESEARCH_CARD_MODELS`` (``mode=model,...``; ``default``
    as the model, or ``none`` as the whole value, means the profile default). Unset or empty:
    ``DEFAULT_CARD_MODELS``. ValueError for an unknown mode or an entry without a model."""
    raw = (env.get(ENV_CARD_MODELS) or "").strip()
    if not raw:
        return dict(DEFAULT_CARD_MODELS)
    if raw == NO_MODELS:
        return {}
    models = {}
    for entry in raw.split(","):
        mode, _, model = (part.strip() for part in entry.partition("="))
        if mode not in get_args(Mode):
            raise ValueError(f"{ENV_CARD_MODELS}: unknown card mode {mode!r}")
        if not model:
            raise ValueError(f"{ENV_CARD_MODELS}: {mode!r} has no model")
        if model != DEFAULT_MODEL_WORD:
            models[mode] = model
    return models


def queue_from_env(env: Mapping[str, str], ctx: runctx.RunContext) -> Queue:
    """``AUTORESEARCH_QUEUE=file`` -> FileQueue under the canonical runtime dir; otherwise
    Kanban, where the board and worker profile are both named after the profile home, with the
    per-mode models of ``card_models``."""
    if env.get(ENV_QUEUE) == "file":
        return FileQueue(runctx.runtime_dir(ctx) / FILE_QUEUE_RELATIVE)
    profile = ctx.hermes_home.name
    return KanbanQueue(board=profile, profile=profile, models=card_models(env))
