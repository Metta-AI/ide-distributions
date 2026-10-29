"""One watcher tick: the design's twelve steps in order (Appendix H "Watcher tick order").

Each step is a module ``research_core.watcher.<name>`` exposing ``run(ctx: TickContext) ->
dict`` (a small report). The tick imports a step lazily by name; a step that is not built yet
reports ``{"skipped": "not built"}`` and a step that raises reports ``{"error": "<Type>:
<message>"}``, and in both cases the tick continues: one failing step never stops the others.

Pause (step 1): while the workspace ``paused`` setting is true only the store checks (step 2)
and the heartbeat and panel (step 12) run, plus ``drain`` once per pause (``meta
paused_drained``, amendment A5.7). Events queue and are processed in order on resume; user
turns are Hermes's business and never the watcher's.

``main`` is ``research watcher tick``: it builds the context from the environment, runs the
tick and prints one JSON line for Hermes (``wakeAgent`` false keeps the cron job silent). It
exits 1 only when the workspace store cannot be opened, so Hermes raises a failure notice.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from research_core.store.errors import SchemaMismatch, StoreError
from research_core.watcher import context, store_checks
from research_core.watcher.context import TickContext

STEP_PACKAGE = "research_core.watcher"
# Steps 2–12 by module name, in the design's order. Step 1 (pause) is the tick's own check.
STEP_ORDER: tuple[str, ...] = (
    "store_checks",
    "champion_sync",
    "waits",
    "recovery",
    "messages",
    "merge",
    "goals",
    "checks",
    "dispatch",
    "wakes",
    "heartbeat",
    "context_panel",
)
# What still runs while paused: store protection and the heartbeat and panel.
PAUSED_STEPS = frozenset({"store_checks", "heartbeat", "context_panel"})
DRAIN_STEP = "drain"
PAUSED_DRAINED_KEY = "paused_drained"
NOT_BUILT = {"skipped": "not built"}
PAUSED = {"skipped": "paused"}


@dataclass
class TickReport:
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    has_work: bool = False
    paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "has_work": self.has_work,
            "errors": list(self.errors),
            "steps": dict(self.steps),
        }


def run_step(ctx: TickContext, name: str) -> dict[str, Any]:
    """Run ``research_core.watcher.<name>.run(ctx)`` and report it, never raising."""
    module_name = f"{STEP_PACKAGE}.{name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name:
            return dict(NOT_BUILT)
        return {"error": _describe(error)}
    except Exception as error:
        return {"error": _describe(error)}
    try:
        report = module.run(ctx)
    except Exception as error:
        return {"error": _describe(error)}
    return report if isinstance(report, dict) else {"result": report}


def tick(ctx: TickContext) -> TickReport:
    steps: dict[str, dict[str, Any]] = {}
    try:
        paused = store_checks.pause_state(ctx)
    except Exception as error:
        paused = False
        steps["pause"] = {"error": _describe(error)}
    if paused:
        _drain_once(ctx, steps)
    elif ctx.meta(PAUSED_DRAINED_KEY) == "1":
        ctx.set_meta(PAUSED_DRAINED_KEY, "0")
    for name in STEP_ORDER:
        if paused and name not in PAUSED_STEPS:
            steps[name] = dict(PAUSED)
        else:
            steps[name] = run_step(ctx, name)
    errors = [f"{name}: {report['error']}" for name, report in steps.items() if "error" in report]
    return TickReport(
        steps=steps, errors=errors, has_work=ctx.meta("has_work") == "1", paused=paused
    )


def _drain_once(ctx: TickContext, steps: dict[str, dict[str, Any]]) -> None:
    """Stop running Researcher work once per pause (A5.7); a drain that did not run is retried."""
    if ctx.meta(PAUSED_DRAINED_KEY) == "1":
        steps[DRAIN_STEP] = {"skipped": "already drained"}
        return
    report = run_step(ctx, DRAIN_STEP)
    steps[DRAIN_STEP] = report
    if "error" not in report and "skipped" not in report:
        ctx.set_meta(PAUSED_DRAINED_KEY, "1")


# --- the command ----------------------------------------------------------------------------


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """``research watcher tick``. ``argv`` is accepted for the console-script shape; unused."""
    env = dict(os.environ if env is None else env)
    if not env.get("HERMES_HOME"):
        _print(
            {
                "wakeAgent": False,
                "error": "HERMES_HOME is not set; the watcher runs inside"
                " the Orchestrator profile and needs its home for the runs API",
            }
        )
        return 1
    try:
        ctx = _build_context(env)
    except (SchemaMismatch, StoreError, sqlite3.Error, OSError) as error:
        _print({"wakeAgent": False, "error": f"workspace store cannot be opened: {error}"})
        return 1
    except Exception as error:
        _print({"wakeAgent": False, "error": f"watcher context not built: {_describe(error)}"})
        return 0
    try:
        report = tick(ctx)
    finally:
        ctx.wstore.close()
        if ctx.store is not None:
            ctx.store.close()
    _print({"wakeAgent": False, **report.to_dict()})
    return 0


def _build_context(env: Mapping[str, str]) -> TickContext:
    try:
        return context.build(env)
    except SchemaMismatch:
        store_checks.repair_unopenable(env)
        return context.build(env)


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, default=str), flush=True)


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"
