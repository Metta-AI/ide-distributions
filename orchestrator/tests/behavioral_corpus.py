"""Bounded live-model decision evaluation; tool effects are simulated, never published."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import httpx

CASES = [
    (
        "waiting_worker",
        "The requested build is running in t_running. There is no new result or correction. Save the next step and let its completion notification resume the coordinator; do not poll or create work.",
        {"running": ["t_running"]},
        None,
    ),
    (
        "failed_upload",
        "Task t_blocked built and smoke-tested B-ready, but upload was refused before acceptance. The service is healthy now. Preserve the ready build and continue this same work through upload; do not rebuild or run a superiority evaluation.",
        {"blocked": ["t_blocked"]},
        "card_answer",
    ),
    (
        "explicit_build",
        "Build, smoke-test, upload and submit a new policy. Do not run a superiority evaluation. Our existing champion remains a statistical baseline only.",
        {},
        "card_create",
    ),
    (
        "blocked_checkpoint",
        "For task t_blocked, use retreat 40 and continue the same task. Its earlier checkpoint is already integrated.",
        {"blocked": ["t_blocked"]},
        "card_answer",
    ),
    (
        "intentional_repeat",
        "Repeat the completed build as a new independent build. I intentionally want another run.",
        {"done": ["t_old"]},
        "card_create",
    ),
    (
        "cancelled_then_new",
        "The previous task t_old is already archived and its worker stopped. Start a new build of retreat 40 now.",
        {"archived": ["t_old"]},
        "card_create",
    ),
    (
        "stale_notification",
        "Background duplicate completion event 15 for t_old. Its output was accepted and this event already acknowledged. There is no new user request or pending work.",
        {"done": ["t_old"]},
        None,
    ),
    (
        "worker_question",
        "Researcher t_blocked asks which retreat setting to use. My answer is 40. Resume its existing work.",
        {"blocked": ["t_blocked"]},
        "card_answer",
    ),
    (
        "budget_exhausted",
        "The episode budget is exhausted. Tell me that and wait. Do not launch work or increase the budget.",
        {},
        None,
    ),
]
MUTATIONS = {
    "card_create",
    "card_answer",
    "card_close",
    "promotion_submit",
    "submit_requested_version",
    "bootstrap_champion",
    "upload_build",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--hermes-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case")
    args = parser.parse_args()
    source = args.root / "orchestrator"
    schema_code = "from orchestrator.plugin import TOOLS; import json; print(json.dumps([{'type':'function','function':{'name':k,'description':v[1],'parameters':{'type':'object','properties':v[2],'required':v[3]}}} for k,v in TOOLS.items()]))"
    schemas = json.loads(
        subprocess.check_output(
            [os.sys.executable, "-c", schema_code],
            env={**os.environ, "PYTHONPATH": str(source / "src")},
            text=True,
        )
    )
    os.sys.path.insert(0, str(args.hermes_source))
    from tools.schema_sanitizer import strip_pattern_and_format

    # Match the native provider recovery for unsupported regex constraints.
    schemas, _ = strip_pattern_and_format(schemas)
    system = (
        (source / "SOUL.md").read_text()
        + "\nDecision evaluation: use the provided tools. The supplied tool responses are current authoritative workspace facts."
    )
    results = []
    for name, prompt, cards, expected in CASES:
        if args.case and name != args.case:
            continue
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        actions = []
        usage = []
        started = time.monotonic()
        for _ in range(6):
            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model": "openai/gpt-6-luna",
                    "messages": messages,
                    "tools": schemas,
                    "temperature": 0,
                    "max_tokens": 1200,
                },
                headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
                timeout=120,
            )
            if response.status_code >= 400:
                print(response.text, flush=True)
            response.raise_for_status()
            reply = response.json()
            usage.append(reply.get("usage", {}))
            message = reply["choices"][0]["message"]
            messages.append(message)
            calls = message.get("tool_calls", [])
            if not calls:
                break
            for call in calls:
                tool = call["function"]["name"]
                parameters = json.loads(call["function"]["arguments"])
                actions.append({"tool": tool, "arguments": parameters})
                if tool == "status":
                    data = {
                        "cards": cards,
                        "research": {"champion": {"version": "V-existing"}},
                        "control": {"paused": False},
                        "promotion": {"in_flight": None},
                    }
                elif tool == "board_reconcile":
                    data = {"events": [], "pending": [], "cards": []}
                elif tool == "card_list":
                    data = [
                        {"id": task, "status": status, "assignee": "researcher-1"}
                        for status, tasks in cards.items()
                        for task in tasks
                    ]
                elif tool == "card_show":
                    data = {
                        "card": {
                            "id": parameters["id"],
                            "status": next(
                                (
                                    status
                                    for status, tasks in cards.items()
                                    if parameters["id"] in tasks
                                ),
                                "done",
                            ),
                            "assignee": "researcher-1",
                            "body": "Build the requested policy",
                            "branch_name": "native/task",
                        },
                        "runs": [],
                        "comments": [],
                    }
                elif tool == "worker_skills":
                    data = {"assignee": "researcher-1", "skills": []}
                elif tool == "credits":
                    data = {"available": True, "credits": 0 if name == "budget_exhausted" else 200}
                else:
                    data = {"ok": True}
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(data)}
                )
            if any(call["function"]["name"] in MUTATIONS for call in calls):
                break
        mutations = [action for action in actions if action["tool"] in MUTATIONS]
        passed = (
            not mutations
            if expected is None
            else bool(mutations) and mutations[0]["tool"] == expected
        )
        results.append(
            {
                "case": name,
                "passed": passed,
                "expected": expected,
                "actions": actions,
                "seconds": round(time.monotonic() - started, 2),
                "usage": usage,
            }
        )
        args.output.write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps({"case": name, "passed": passed}), flush=True)
    print(json.dumps({"passed": sum(item["passed"] for item in results), "total": len(results)}))


if __name__ == "__main__":
    main()
