"""Native Hermes operations on the explicitly named autoresearch board."""

import json
import os
import re
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict

from . import storage
from .research import ResearchError

BOARD = "autoresearch"


def git(clone, *args):
    return subprocess.run(
        ["git", "-C", str(clone), *args], check=True, capture_output=True, text=True, timeout=60
    ).stdout.strip()


def connection():
    from hermes_cli.kanban_db_connect import connect

    return connect(board=BOARD)


def subscribe(conn, task_id):
    from hermes_cli.kanban_db_notify import add_notify_sub
    from tools.kanban_tools import _resolve_notify_target

    target = _resolve_notify_target()
    if target is None:
        return False
    add_notify_sub(conn, task_id=task_id, **target)
    return True


class Board:
    def __init__(self, context):
        self.context = context

    def worker_skills(self, assignee):
        """Discover skills with the same Hermes interpreter and profile as the worker."""
        from agent.secret_scope import build_profile_secret_scope
        from hermes_cli.profiles import get_profile_dir

        profile = get_profile_dir(assignee)
        scope = build_profile_secret_scope(profile)
        env = {
            "PATH": os.environ["PATH"],
            "PYTHONPATH": os.pathsep.join(sys.path),
            "HERMES_HOME": str(profile),
            "HERMES_PROFILE": assignee,
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """import json
from tools.skills_tool import _skill_catalog, skill_view
from hermes_cli.plugins import discover_plugins, get_plugin_manager
skills = [{"name": s["load_name"], "description": s["description"]}
          for s in _skill_catalog(include_hidden=True) if s["load_name"]]
discover_plugins()
skills.extend(get_plugin_manager().list_plugin_skill_metadata())
print(json.dumps([{"name": s["name"], "description": s["description"]}
                  for s in skills
                  if json.loads(skill_view(s["name"], preprocess=False))["success"]]))
""",
            ],
            env=env,
            cwd=scope.get("RESEARCH_WORKSPACE") or profile / "workspace",
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"assignee": assignee, "skills": json.loads(result.stdout)}

    def card_create(
        self,
        kind,
        entity,
        assignee,
        title,
        body,
        attempt=1,
        refs=None,
        priority=0,
        skills=None,
        max_runtime_seconds=7200,
        failure_limit=2,
        parents=None,
        card_sha=None,
        session_id=None,
    ):
        from agent.secret_scope import build_profile_secret_scope
        from gateway.session_context import get_session_env
        from hermes_cli import kanban_db as kb
        from hermes_cli.profiles import get_profile_dir
        from tools.async_delegation import _current_origin_session_id
        from tools.kanban_tools import _persisted_session_id

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", entity):
            raise ValueError("entity must be a record ID or a git-safe repair entity key")
        if (
            kind
            not in {
                "investigation",
                "scouting",
                "experimentation",
                "maintenance",
                "planning",
                "review",
                "repair",
            }
            or attempt < 1
        ):
            raise ValueError("unknown kind or non-positive attempt")
        if skills:
            available = self.worker_skills(assignee)["skills"]
            names = {skill["name"] for skill in available}
            invalid = sorted(set(skills or []) - names)
            if invalid:
                raise ResearchError(
                    {
                        "ok": False,
                        "error": {
                            "reason": "invalid_worker_skills",
                            "message": "Select skills from the assigned worker's catalog and retry card_create.",
                            "details": {
                                "assignee": assignee,
                                "invalid": invalid,
                                "available": available,
                            },
                        },
                    }
                )
        key = f"{kind}:{entity}:{attempt}"
        slug = f"{kind}-{entity}-{attempt}"
        branch = f"{'repair' if kind == 'repair' else 'card'}/{slug}"
        profile = get_profile_dir(assignee)
        env = build_profile_secret_scope(profile)
        clone = profile / "workspace"
        if env.get("RESEARCH_WORKSPACE"):
            from pathlib import Path

            clone = Path(env["RESEARCH_WORKSPACE"])
        with closing(storage.plugin_storage()) as lock, closing(connection()) as conn:
            # Covers lookup AND creation: Hermes's own idempotency lookup is not atomic.
            # Only local bare-origin fetch/branch preparation happens inside this transaction.
            lock.execute("BEGIN IMMEDIATE")
            tasks = kb.list_tasks(conn, include_archived=True)
            existing = next((t for t in tasks if t.idempotency_key == key), None)
            if existing:
                task_id = existing.id
            else:
                for task in tasks:
                    parts = (task.idempotency_key or "").split(":")
                    if (
                        len(parts) == 3
                        and parts[1] == entity
                        and task.status not in {"done", "archived"}
                    ):
                        raise ValueError(f"entity {entity} already has open card {task.id}")
                # Branch from canonical main as fetched; the researcher's checked-out main is
                # its integrate's business and may be mid-merge.
                git(clone, "fetch", "origin")
                if kind == "repair" and not card_sha:
                    raise ValueError("repair requires the failed pushed card_sha")
                base = git(clone, "rev-parse", card_sha if kind == "repair" else "origin/main")
                # A crash after branch creation is replayable without a reservation table.
                found = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(clone),
                        "show-ref",
                        "--verify",
                        "--quiet",
                        f"refs/heads/{branch}",
                    ],
                    check=False,
                )
                if found.returncode != 0:
                    git(clone, "branch", branch, base)
                else:
                    base = git(clone, "rev-parse", branch)
                origin_session = (
                    _persisted_session_id(session_id)
                    or _persisted_session_id(_current_origin_session_id())
                    or _persisted_session_id(get_session_env("HERMES_SESSION_ID", ""))
                )
                details = {
                    "kind": kind,
                    "entity": entity,
                    "attempt": attempt,
                    "refs": refs or [],
                    "base_sha": base,
                    "clone": str(clone),
                    "branch": branch,
                }
                task_id = kb.create_task(
                    conn,
                    title=title,
                    body=body
                    + "\n\nCard context:\n```json\n"
                    + json.dumps(details, indent=2)
                    + "\n```\n",
                    assignee=assignee,
                    created_by="orchestrator",
                    workspace_kind="worktree",
                    workspace_path=str(clone / ".worktrees" / slug),
                    branch_name=branch,
                    max_retries=failure_limit,
                    max_runtime_seconds=max_runtime_seconds,
                    skills=skills or [],
                    parents=[] if kind == "repair" else (parents or []),
                    idempotency_key=key,
                    session_id=origin_session,
                    board=BOARD,
                )
            lock.commit()
            # Native wake routing is optional; reconciliation covers turns with no destination.
            try:
                subscribed = subscribe(conn, task_id)
                subscription_error = None
            except Exception as error:  # noqa: BLE001 - tool boundary reports native failures
                subscribed, subscription_error = False, str(error)
            return {
                "card": asdict(kb.get_task(conn, task_id)),
                "subscribed": subscribed,
                "subscription_error": subscription_error,
            }

    def card_list(self, filters=None):
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn:
            return [asdict(task) for task in kb.list_tasks(conn, **(filters or {}))]

    def card_show(self, id):
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn:
            task = kb.get_task(conn, id)
            if task is None:
                raise ValueError(f"unknown card {id}")
            return {
                "card": asdict(task),
                "runs": [asdict(r) for r in kb.list_runs(conn, id)],
                "comments": [asdict(c) for c in kb.list_comments(conn, id)],
            }

    def card_comment(self, id, body):
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn:
            return {"table": "task_comments", "id": kb.add_comment(conn, id, "orchestrator", body)}

    def card_answer(self, id, text):
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn:
            comment = kb.add_comment(conn, id, "orchestrator", text)
            return {"comment": comment, "unblocked": kb.unblock_task(conn, id)}

    def card_close(self, id, outcome, summary=None, metadata=None):
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn:
            if outcome == "done":
                changed = kb.complete_task(conn, id, summary=summary, metadata=metadata)
            elif outcome == "archived":
                changed = kb.archive_task(conn, id)
            else:
                raise ValueError("outcome must be done or archived")
            return {"changed": changed}

    def event_ack(self, ids):
        with closing(storage.plugin_storage()) as conn:
            return storage.ack(conn, ids)
