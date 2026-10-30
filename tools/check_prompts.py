"""Check the prompt text the profiles ship (design D.7, plan task 7.6).

Structural checks always run: word budgets and the leak test (through the prompt builder),
canonical vocabulary (never "promotion"), no store path, no retired machinery words, every tool
named in a SOUL or skill exists for that role, every skill named in a SOUL exists. The model
check runs only when ``OPENROUTER_API_KEY`` is set: it asks a model to find statements that
contradict each other across a role's SOUL and skills, and fails on a non-NONE answer.

Usage: python tools/check_prompts.py [--model] [--model-id openai/gpt-5-mini]
Exit 0 when everything passes; 1 with one line per problem otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "src"))

from research_core.prompts import build  # noqa: E402
from research_core.store import events, settings  # noqa: E402
from research_core.tools import catalog  # noqa: E402

SKILLS = REPO / "core" / "src" / "research_core" / "skills"
WAKES = REPO / "core" / "src" / "research_core" / "watcher" / "wakes"
# The role whose Session each wake prompt starts.
WAKE_ROLES = {
    "setup": "orchestrator",
    "management": "orchestrator",
    "review": "orchestrator",
    "anti_stall": "orchestrator",
    "task_start": "researcher",
    "task_resume": "researcher",
}
FORBIDDEN = (
    (re.compile(r"\bpromot(e|ion|ed|ing)\b", re.IGNORECASE), "says 'promotion'; say submission"),
    (re.compile(r"\.state/|store\.db"), "names a store path"),
    (re.compile(r"\bkanban\b|\bcoordinator\b", re.IGNORECASE), "names retired machinery"),
)
TOOL_REF = re.compile(
    r"`((?:goal|task|claim|experiment|version|directive|settings|ui|workspace|upload|submit|xp|"
    r"platform|forum_post|wiki_edit|softmax_api|github|attachment_download)\.[a-z_]+)`"
)
SKILL_REF = re.compile(r"`([a-z][a-z0-9-]+)` skill")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
QUESTION = (
    "Below are the system prompt (SOUL) and the skills of one agent role. List any two statements "
    "that contradict each other, quoting both and naming their files. If there is no "
    "contradiction, answer exactly NONE."
)


def skill_files(role: str) -> list[Path]:
    roots = [SKILLS / "common", SKILLS / role]
    return sorted(p for root in roots if root.exists() for p in root.rglob("SKILL.md"))


def skill_names() -> set[str]:
    return {p.parent.name for p in SKILLS.rglob("SKILL.md")}


def _label(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def structural(role: str) -> list[str]:
    problems = [f"{role}: {p}" for p in build.check(role)]
    texts = {f"{role}/SOUL.md": build.render(role)}
    for path in skill_files(role):
        texts[_label(path)] = path.read_text()
    for wake, wake_role in WAKE_ROLES.items():
        if wake_role == role and (WAKES / f"{wake}.md").exists():
            texts[_label(WAKES / f"{wake}.md")] = (WAKES / f"{wake}.md").read_text()
    names = skill_names()
    # Until the skills ship, a SOUL may name skills that do not exist yet; the check is for
    # a shipped tree, so skill existence is enforced only once some skill exists.
    check_skill_names = bool(names)
    for where, text in texts.items():
        for pattern, message in FORBIDDEN:
            if pattern.search(text):
                problems.append(f"{where}: {message}")
        # A common skill is read by both roles and may name the other role's step in a handoff;
        # a SOUL, a role skill or a wake prompt names only what its role has.
        allowed_roles = list(catalog.ROLES) if "/common/" in where else [role]
        for ref in set(TOOL_REF.findall(text)):
            if ref in events.KINDS or ref in settings.SETTINGS:  # an event kind or a setting
                continue
            if not any(build.tool_available(r, ref) for r in allowed_roles):
                problems.append(f"{where}: names `{ref}`, which the {role} does not have")
        for name in set(SKILL_REF.findall(text)) if check_skill_names else ():
            if name not in names and name != "hermes-agent":
                problems.append(f"{where}: names the `{name}` skill, which does not exist")
    return problems


def model_check(role: str, model_id: str, key: str) -> list[str]:
    corpus = [f"### {role}/SOUL.md\n{build.render(role)}"]
    for path in skill_files(role):
        corpus.append(f"### {path.relative_to(REPO)}\n{path.read_text()}")
    body = json.dumps(
        {
            "model": model_id,
            "messages": [
                {"role": "system", "content": QUESTION},
                {"role": "user", "content": "\n\n".join(corpus)},
            ],
            "temperature": 0,
        }
    ).encode()
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        payload = json.load(response)
    answer = payload["choices"][0]["message"]["content"].strip()
    if answer.upper().startswith("NONE"):
        return []
    return [f"{role}: the model found a contradiction:\n{answer}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", action="store_true", help="Also run the model-based check")
    parser.add_argument("--model-id", default="openai/gpt-5-mini")
    args = parser.parse_args(argv)
    problems: list[str] = []
    for role in catalog.ROLES:
        problems.extend(structural(role))
    key = os.environ.get("OPENROUTER_API_KEY")
    if args.model:
        if not key:
            print("model check skipped: OPENROUTER_API_KEY is not set")
        else:
            for role in catalog.ROLES:
                problems.extend(model_check(role, args.model_id, key))
    for problem in problems:
        print(problem)
    if not problems:
        print("prompt checks passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
