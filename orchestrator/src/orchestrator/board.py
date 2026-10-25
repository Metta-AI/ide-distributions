"""Native Hermes operations on the explicitly named autoresearch board."""

import json
import os
import re
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ide_research_core.games.scope import check_game_name, clone_for, locate_game

from .context import Context
from .research import Research, ResearchError

BOARD = "autoresearch"


class CardFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    assignee: str | None = None
    tenant: str | None = None
    session_id: str | None = None
    include_archived: bool = False
    limit: int = Field(default=20, ge=1, le=200)
    order_by: Literal[
        "assignee", "completed-desc", "created", "created-desc", "priority",
        "priority-desc", "status", "title", "updated",
    ] | None = None


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


def prepare_clone(profile, env, game):
    """Make or refresh the assignee's clone of ``game`` with the assignee's own CLI, so a new
    canonical repository is seeded with the assignee's doctrine and a changed doctrine reaches
    the clone (``research workspace bootstrap``, idempotent)."""
    result = subprocess.run(
        [str(Path(profile) / "bin" / "research"), "workspace", "bootstrap", "--game", game],
        env={"PATH": os.environ["PATH"], **env},
        cwd=profile,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    try:
        envelope = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        envelope = {
            "ok": False,
            "error": {"reason": "setup", "message": result.stderr.strip()[-500:]},
        }
    if not envelope["ok"]:
        raise ResearchError(envelope)
    return envelope["data"]


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
        game=None,
    ):
        """Replay an execution command; native Hermes owns task and worktree identity.

        ``game`` (a shipped or installed game; omit for the neutral workspace) selects
        the assignee's clone of that game, which Hermes makes the card worktree from. It is
        recorded in the creation command, where every card-scoped effect reads it."""
        from agent.secret_scope import build_profile_secret_scope
        from gateway.session_context import get_session_env
        from hermes_cli import kanban_db as kb
        from hermes_cli import projects_db
        from hermes_cli.profiles import get_profile_dir
        from tools.approval_context import get_current_session_key
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
        env = {**build_profile_secret_scope(profile), "HERMES_HOME": str(profile)}
        command = {
            "assignee": assignee,
            "title": title,
            "body": body,
            "priority": priority,
            "skills": skills or [],
            "max_runtime_seconds": max_runtime_seconds,
            "failure_limit": failure_limit,
        }
        if game is None:  # a host with no default game: the assignee's own clone
            clone = Path(env.get("RESEARCH_WORKSPACE") or profile / "workspace")
        else:
            # Store the explicit choice; profile settings never select or move a card.
            command["game"] = game
            clone = clone_for(env, game)
            if game != env.get("RESEARCH_GAME"):
                locate_game(game, self.context.env)
            # Refresh default-game clones too before native Hermes branches the task.
            prepare_clone(profile, env, game)
        payload = json.dumps(command, sort_keys=True)
        with closing(connection()) as conn:
            # Serialize lookup and creation in the native task database. The task
            # and its immutable creation arguments commit together.
            with kb.write_txn(conn):
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
                    prior = conn.execute(
                        "SELECT payload FROM task_events WHERE task_id=? AND kind='creation_command'",
                        (task_id,),
                    ).fetchone()
                    recorded = json.loads(prior["payload"])
                    if recorded != json.loads(payload):
                        raise ValueError("command_id was already used with different arguments")
                else:
                    git(clone, "fetch", "origin")
                    with projects_db.connect_closing() as projects:
                        project = projects_db.find_by_primary_path(projects, str(clone))
                        project_id = (
                            project.id
                            if project
                            else projects_db.create_project(
                                projects,
                                name=f"{assignee} {game}" if game else assignee,
                                primary_path=str(clone),
                                board_slug=BOARD,
                            )
                        )
                    origin_session = (
                        _persisted_session_id(session_id)
                        or _persisted_session_id(_current_origin_session_id())
                        or _persisted_session_id(get_session_env("HERMES_SESSION_ID", ""))
                    )
                    source_run_id = get_current_session_key("")
                    if not re.fullmatch(r"run_[0-9a-f]{32}", source_run_id):
                        source_run_id = None
                    task_id = kb.create_task(
                        conn,
                        title=title,
                        body=body
                        + "\n\nCard context:\n```json\n"
                        + json.dumps({"source_run_id": source_run_id})
                        + "\n```\n",
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
                if not existing:
                    kb._append_event(conn, task_id, "creation_command", json.loads(payload))
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
            if task.status == "blocked" and not kb.list_runs(conn, task_id):
                kb.unblock_task(conn, task_id)
            return {
                "card": asdict(kb.get_task(conn, task_id)),
                "subscribed": subscribe(conn, task_id),
            }

    def card_list(self, filters=None):
        filters = CardFilters.model_validate(filters or {}).model_dump(exclude_none=True)
        from hermes_cli import kanban_db as kb

        with closing(connection()) as conn:
            return [asdict(task) for task in kb.list_tasks(conn, **filters)]

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

    def _card_game(self, id):
        """The game recorded in the card's creation command (None, for a card created before
        cards named their game: the neutral workspace)."""
        with closing(connection()) as conn:
            row = conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='creation_command' "
                "ORDER BY id LIMIT 1",
                (id,),
            ).fetchone()
        return json.loads(row["payload"]).get("game") if row else None

    def _research(self, id):
        """Shared research for the card's game, in this profile's clone of it."""
        return Research(self.context).in_game(self._card_game(id))

    def upload_build(self, id, run_id, build_dir, name, experiment=None):
        """Publish a committed artifact. Retry only with identical arguments; upload owns deduplication."""
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
        scoped = self._research(id).context
        context = Context(scoped.home, {**scoped.env, "HERMES_KANBAN_WORKSPACE": str(worktree)})
        research = Research(context)
        command = ("upload", experiment) if experiment else ("upload-build",)
        receipt = research.call("softmax", *command, "--build-dir", artifact, "--name", name)
        staged = research.call("workspace", "stage", "--worktree", worktree)
        return {**receipt, **staged}

    def workspace_integrate(self, id, run_id, commit):
        """Integrate a completed run. Retry the identical commit; Git owns deduplication."""
        task = self._admit_effect(id, run_id, "integration_admitted", {"commit": commit})
        return self._research(id).call(
            "workspace", "integrate", "--branch", task.branch_name, "--commit", commit
        )

    def submit_requested_version(self, id, run_id, version, strategy, request, league_id):
        """Submit explicit user intent. Retry identical arguments; promotion owns deduplication."""
        self._admit_effect(
            id, run_id, "submission_admitted", {"version": version, "request": request, "league_id": league_id}
        )
        return self._research(id).call(
            "promotion",
            "submit-requested",
            "--league",
            league_id,
            "--version",
            version,
            "--strategy",
            strategy,
            "--request",
            request,
        )

    def promotion_submit(self, id, run_id, experiment, league_id):
        self._admit_effect(id, run_id, "promotion_admitted", {"experiment": experiment, "league_id": league_id})
        return self._research(id).call("promotion", "submit", experiment, "--league", league_id)

    def bootstrap_champion(self, id, run_id, version, league_id):
        self._admit_effect(id, run_id, "bootstrap_admitted", {"version": version, "league_id": league_id})
        return self._research(id).call("promotion", "bootstrap", "--version", version, "--league", league_id)

    def game_install(self, id, run_id, name, replace=False):
        """Install the game folder a completed card committed at ``games/<name>/`` host-wide.
        Retry with identical arguments; reinstalling the same commit changes nothing."""
        from hermes_cli.kanban_db_workspace import resolve_workspace

        check_game_name(name)
        task = self._admit_effect(
            id, run_id, "game_install_admitted", {"name": name, "replace": replace}
        )
        worktree = resolve_workspace(task, board=BOARD)
        folder = f"games/{name}"
        git(worktree, "ls-files", "--error-unmatch", "--", f"{folder}/GAME.md")
        if git(
            worktree,
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--ignored=matching",
            "--",
            folder,
        ):
            raise ValueError("Stage the entire game folder before installing it")
        arguments = ["game", "install", "--name", name, "--worktree", worktree]
        arguments += ["--commit", git(worktree, "rev-parse", "HEAD"), "--card", id]
        arguments += ["--run", run_id, *(["--replace"] if replace else [])]
        return Research(self.context).call(*arguments)
