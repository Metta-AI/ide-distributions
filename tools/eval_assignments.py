"""Evaluate specialist routing with the shipped model and the real task.assign handler.

The baseline is the retired deterministic round-robin policy, starting at worker zero.
This checks assignment choice and durable acceptance, not native execution or child delivery.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from research_core.prompts import build
from research_core.store import coworlds, tasks
from research_core.store.actors import Actor
from research_core.tools.base import ToolContext, run_tool
from research_core.tools.task import SPEC

ROOT = Path(__file__).resolve().parents[1]
WORKERS = {
    "python": "Python packaging, asyncio, and Python test failures",
    "cpp": "C++ compiler errors, native memory ownership, and native profiling",
    "data": "SQL query plans, database migrations, and database integrity",
}


@dataclass(frozen=True)
class Case:
    title: str
    description: str
    expected: str


CASES = (
    Case("Native memory defect", "Diagnose a C++ use-after-free in the simulator.", "cpp"),
    Case(
        "Query regression",
        "Investigate PostgreSQL query-plan regressions after a migration.",
        "data",
    ),
    Case(
        "Async packaging defect",
        "Fix Python asyncio tests and Python package resolution.",
        "python",
    ),
)


def main() -> None:
    key = os.environ["OPENROUTER_API_KEY"]
    model = yaml.safe_load((ROOT / "orchestrator/config.yaml").read_text())["model"]["default"]
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="assignment-eval-") as temporary:
        for index, case in enumerate(CASES):
            ctx = ToolContext.from_env(
                "orchestrator",
                {
                    "IDE_WORKSPACE": str(Path(temporary) / str(index)),
                    "IDE_COWORLD_ID": "eval",
                    "IDE_SESSION_ID": "planning-eval",
                    "IDE_RESEARCHER_PROFILES": ",".join(WORKERS),
                    "IDE_RESEARCHER_CAPABILITIES": json.dumps(WORKERS),
                },
            )
            ctx.workspace.ensure_dirs()
            coworlds.register(ctx.wstore, Actor.harness(), "eval", folder="eval")
            coworlds.activate(ctx.wstore, Actor.harness(), "eval")
            task = tasks.create(
                ctx.store,
                Actor.harness(),
                type="maintenance",
                title=case.title,
                description=case.description,
            )
            prompt = {
                "task": task,
                "workers": WORKERS,
                "slots": 1,
                "instruction": "The task and worker descriptions are complete. "
                "Use task.assign exactly once now; "
                "do not read or execute tools first. Choose the appropriate specialist.",
            }
            request = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": build.render("orchestrator")},
                            {"role": "user", "content": json.dumps(prompt)},
                        ],
                        "tools": [{"type": "function", "function": SPEC.schema("orchestrator")}],
                        "tool_choice": "required",
                        "provider": {"only": ["moonshotai"], "allow_fallbacks": False},
                    }
                ).encode(),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                message = json.load(response)["choices"][0]["message"]
            calls = message["tool_calls"]
            assert len(calls) == 1, calls
            args = json.loads(calls[0]["function"]["arguments"])
            result = json.loads(run_tool("task", args, ctx))
            outcomes.append(
                {
                    "case": asdict(case),
                    "baseline": list(WORKERS)[index],
                    "selected": args["profile"],
                    "valid_assignment": result["ok"],
                    "passed": result["ok"] and args["profile"] == case.expected,
                }
            )
            ctx.store.close()
            ctx.wstore.close()
    report = {
        "model": model,
        "cases": outcomes,
        "baseline_correct": sum(row["baseline"] == row["case"]["expected"] for row in outcomes),
        "candidate_correct": sum(row["passed"] for row in outcomes),
        "total": len(outcomes),
    }
    print(json.dumps(report, indent=2))
    assert report["candidate_correct"] == len(CASES), report


if __name__ == "__main__":
    main()
