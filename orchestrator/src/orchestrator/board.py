"""Native Hermes operations on the explicitly named autoresearch board."""

import json
import os
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from . import storage
from .context import Context
from .research import Research, ResearchError

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
        command_id,
        assignee,
        title,
        body,
        priority=0,
        skills=None,
        max_runtime_seconds=7200,
        failure_limit=2,
        session_id=None,
    ):
        """Replay an execution command; native Hermes owns task and worktree identity."""
        from agent.secret_scope import build_profile_secret_scope
        from gateway.session_context import get_session_env
        from hermes_cli import kanban_db as kb
        from hermes_cli import projects_db
        from hermes_cli.profiles import get_profile_dir
        from tools.async_delegation import _current_origin_session_id
        from tools.kanban_tools import _persisted_session_id

        if not command_id or len(command_id) > 200:
            raise ValueError("command_id must contain 1-200 characters")
        if skills:
            available = self.worker_skills(assignee)["skills"]
            names = {skill["name"] for skill in available}
            invalid = sorted(set(skills) - names)
            if invalid:
                raise ResearchError(
                    {
                        "ok": False,
                        "error": {
                            "reason": "invalid_worker_skills",
                            "message": "Select skills from the assigned worker's catalog.",
                            "details": {
                                "assignee": assignee,
                                "invalid": invalid,
                                "available": available,
                            },
                        },
                    }
                )
        profile = get_profile_dir(assignee)
        env = build_profile_secret_scope(profile)
        clone = Path(env.get("RESEARCH_WORKSPACE") or profile / "workspace")
        payload = json.dumps(
            {
                "assignee": assignee,
                "title": title,
                "body": body,
                "priority": priority,
                "skills": skills or [],
                "max_runtime_seconds": max_runtime_seconds,
                "failure_limit": failure_limit,
            },
            sort_keys=True,
        )
        with closing(storage.plugin_storage()) as lock, closing(connection()) as conn:
            # Persist the command BEFORE the native write. A crash between the two databases
            # recovers by exact native command key, including archived tasks.
            lock.execute("BEGIN IMMEDIATE")
            prior = lock.execute(
                "SELECT payload FROM commands WHERE id=?", (command_id,)
            ).fetchone()
            if prior and prior[0] != payload:
                raise ValueError("command_id was already used with different arguments")
            lock.execute(
                "INSERT OR IGNORE INTO commands(id, payload) VALUES (?, ?)", (command_id, payload)
            )
            lock.commit()
            lock.execute("BEGIN IMMEDIATE")
            key = f"command:{command_id}"
            existing = next(
                (
                    task
                    for task in kb.list_tasks(conn, include_archived=True)
                    if task.idempotency_key == key
                ),
                None,
            )
            if existing:
                task_id = existing.id
            else:
                git(clone, "fetch", "origin")
                with projects_db.connect_closing() as projects:
                    project = projects_db.find_by_primary_path(projects, str(clone))
                    project_id = (
                        project.id
                        if project
                        else projects_db.create_project(
                            projects,
                            name=assignee,
                            primary_path=str(clone),
                            board_slug=BOARD,
                        )
                    )
                origin_session = (
                    _persisted_session_id(session_id)
                    or _persisted_session_id(_current_origin_session_id())
                    or _persisted_session_id(get_session_env("HERMES_SESSION_ID", ""))
                )
                task_id = kb.create_task(
                    conn,
                    title=title,
                    body=body,
                    assignee=assignee,
                    created_by="orchestrator",
                    project_id=project_id,
                    workspace_kind="worktree",
                    max_retries=failure_limit,
                    max_runtime_seconds=max_runtime_seconds,
                    skills=skills or [],
                    priority=priority,
                    idempotency_key=key,
                    session_id=origin_session,
                    board=BOARD,
                    initial_status="blocked",
                )
            task = kb.get_task(conn, task_id)
            if task.status == "blocked" and not kb.list_runs(conn, task_id):
                # Native creation parks the task until its branch is anchored. Resuming an
                # already executed task belongs to card_answer, never creation replay.
                found = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(clone),
                        "show-ref",
                        "--verify",
                        "--quiet",
                        f"refs/heads/{task.branch_name}",
                    ],
                    check=False,
                )
                if found.returncode == 1:
                    git(clone, "branch", task.branch_name, "origin/main")
                elif found.returncode:
                    found.check_returncode()
                kb.unblock_task(conn, task_id)
            lock.commit()
            return {
                "card": asdict(kb.get_task(conn, task_id)),
                "subscribed": subscribe(conn, task_id),
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
        """Answer native blocked work; Git history does not determine task lifecycle."""
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn:
            comment = kb.add_comment(conn, id, "orchestrator", text)
            task = kb.get_task(conn, id)
            resumed = (
                kb.specify_triage_task(conn, id, author="orchestrator")
                if task.status == "triage"
                else kb.unblock_task(conn, id)
            )
            return {"comment": comment, "unblocked": resumed}

    def card_close(self, id, outcome, summary=None, metadata=None):
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn:
            if outcome == "done":
                changed = kb.complete_task(conn, id, summary=summary, metadata=metadata)
            elif outcome == "archived":
                # Revoke before asking native cancellation to stop the worker. A slow
                # or unconfirmed stop must not leave publication authority usable.
                with kb.write_txn(conn):
                    kb._append_event(conn, id, "publication_revoked", None)
                changed = kb.archive_task(conn, id)
            else:
                raise ValueError("outcome must be done or archived")
            return {"changed": changed}

    def _admit_effect(self, id, run_id, kind, payload):
        """Native archive and publication admission use the same transaction boundary."""
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn, kb.write_txn(conn):
            task = kb.get_task(conn, id)
            runs = kb.list_runs(conn, id)
            revoked = conn.execute(
                "SELECT 1 FROM task_events WHERE task_id=? AND kind='publication_revoked' LIMIT 1",
                (id,),
            ).fetchone()
            if (
                revoked
                or task.status != "done"
                or not runs
                or max(run.id for run in runs) != run_id
            ):
                raise ValueError("Only the latest completed native run may publish")
            kb._append_event(conn, id, kind, payload, run_id=run_id)
            return task

    def upload_build(self, id, run_id, build_dir, name, experiment=None):
        """Publish a committed artifact after the native worker has finished."""
        from hermes_cli.kanban_db_workspace import resolve_workspace

        task = self._admit_effect(
            id,
            run_id,
            "upload_admitted",
            {"build_dir": build_dir, "name": name, "experiment": experiment},
        )
        worktree = resolve_workspace(task, board=BOARD)
        artifact = (worktree / build_dir).resolve()
        if Path(build_dir).is_absolute() or not artifact.is_relative_to(worktree.resolve()):
            raise ValueError("build_dir must be inside the task's committed worktree")
        git(worktree, "ls-files", "--error-unmatch", "--", str(Path(build_dir) / "version.json"))
        if git(
            worktree,
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--ignored=matching",
            "--",
            build_dir,
        ):
            raise ValueError("Stage the entire verified build before upload")
        # Native cleanup may have removed a clean completed checkout. Its task branch
        # restores the committed build; version receipts belong to that same branch.
        context = Context(
            self.context.home, {**self.context.env, "HERMES_KANBAN_WORKSPACE": str(worktree)}
        )
        research = Research(context)
        command = ("upload", experiment) if experiment else ("upload-build",)
        receipt = research.call("softmax", *command, "--build-dir", artifact, "--name", name)
        staged = research.call("workspace", "stage", "--worktree", worktree)
        return {**receipt, **staged}

    def workspace_integrate(self, id, run_id, commit):
        task = self._admit_effect(id, run_id, "integration_admitted", {"commit": commit})
        return Research(self.context).call(
            "workspace", "integrate", "--branch", task.branch_name, "--commit", commit
        )

    def submit_requested_version(self, id, run_id, version, strategy, request):
        self._admit_effect(
            id, run_id, "submission_admitted", {"version": version, "request": request}
        )
        return Research(self.context).call(
            "promotion",
            "submit-requested",
            "--version",
            version,
            "--strategy",
            strategy,
            "--request",
            request,
        )

    def promotion_submit(self, id, run_id, experiment):
        self._admit_effect(id, run_id, "promotion_admitted", {"experiment": experiment})
        return Research(self.context).call("promotion", "submit", experiment)

    def bootstrap_champion(self, id, run_id, version):
        self._admit_effect(id, run_id, "bootstrap_admitted", {"version": version})
        return Research(self.context).call("promotion", "bootstrap", "--version", version)

    def event_ack(self, ids):
        with closing(storage.plugin_storage()) as conn:
            return storage.ack(conn, ids)
