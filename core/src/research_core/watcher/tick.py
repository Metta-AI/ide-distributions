"""Cron requests reconciliation; only research serve owns execution and transitions."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

PAUSED_DRAINED_KEY = "paused_drained"


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Cron requests observation; research serve alone runs the watcher steps."""
    from research_core import layout
    from research_core.store.actors import Actor
    from research_core.store.db import Store

    env = dict(os.environ if env is None else env)
    with Store.open(layout.from_env(env).store_path, kind="workspace") as store:
        with store.tx(Actor.harness()) as tx:
            seq = tx.event("coordinator.observation_requested", "workspace", {})
    _print({"wakeAgent": False, "queued": True, "seq": seq})
    return 0


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, default=str), flush=True)
