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


def parse_card_models(raw: str) -> tuple[dict[str, str], list[str]]:
    """Card mode -> model from an ``AUTORESEARCH_CARD_MODELS`` value (``mode=model,...``;
    ``default`` as the model, or ``none`` as the whole value, means the profile default).
    Empty: ``DEFAULT_CARD_MODELS``. Never raises: an entry with an unknown mode or no model is
    skipped (that mode runs on the profile default) and described in the problems, which
    queue-keeper reports."""
    raw = raw.strip()
    if not raw:
        return dict(DEFAULT_CARD_MODELS), []
    if raw == NO_MODELS:
        return {}, []
    models: dict[str, str] = {}
    problems = []
    for entry in raw.split(","):
        mode, _, model = (part.strip() for part in entry.partition("="))
        if mode not in get_args(Mode):
            problems.append(f"unknown card mode {mode!r} (skipped)")
        elif not model:
            problems.append(f"{mode!r} has no model (it uses the default model)")
        elif model != DEFAULT_MODEL_WORD:
            models[mode] = model
    return models, problems


def card_models(env: Mapping[str, str]) -> dict[str, str]:
    """The per-mode models of ``env`` (``parse_card_models``; problems are dropped here)."""
    return parse_card_models(env.get(ENV_CARD_MODELS) or "")[0]


def queue_from_env(env: Mapping[str, str], ctx: runctx.RunContext) -> Queue:
    """Kanban, where the board and worker profile are both named after the profile home, with
    the per-mode models of ``card_models``.

    ``AUTORESEARCH_QUEUE=file`` is a test hook (like ``AUTORESEARCH_SOFTMAX=fake``): it selects
    the FileQueue test double under the canonical runtime dir, so tests can run the real CLI
    entry points without Hermes. It is not an installer setting and is not on
    ``terminal.env_passthrough``: no shipped job runs FileQueue cards (amendment A13)."""
    if env.get(ENV_QUEUE) == "file":
        return FileQueue(runctx.runtime_dir(ctx) / FILE_QUEUE_RELATIVE)
    profile = ctx.hermes_home.name
    return KanbanQueue(board=profile, profile=profile, models=card_models(env))
