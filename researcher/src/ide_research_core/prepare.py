"""Apply distribution-owned research policy after installation or a retained update.

Hosting supplies paths, credentials and capacity. This operation owns profile policy,
workspace doctrine and the coordinator lifecycle; it never calls a platform service.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from pydantic import BaseModel, ConfigDict

from ide_research_core import workspace as workspaces
from ide_research_core.budget import EpisodeBudget


class Job(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    no_agent: bool = False
    script: str | None = None
    prompt: str


class Jobs(BaseModel):
    model_config = ConfigDict(extra="allow")
    jobs: list[Job]


def reconcile_workspace(profile: Path, workspace: Path, remote: Path, source: Path) -> None:
    """Publish current doctrine and merge it without discarding retained edits."""
    workspaces.write_cli_shim(profile)
    warning = profile / "local/doctrine-migration-warning.txt"
    if not (workspace / ".git").exists():
        workspaces.bootstrap(workspace, remote, None, source)
    canonical = subprocess.check_output(
        ["git", "--git-dir", str(remote), "show", "main:AGENTS.md"], text=True
    )
    wanted = workspaces.agents_md(source)
    if wanted is not None and wanted != canonical:
        with TemporaryDirectory(prefix="doctrine-", dir=profile / "local") as temporary:
            workspaces.bootstrap(Path(temporary), remote, None, source)
        canonical = subprocess.check_output(
            ["git", "--git-dir", str(remote), "show", "main:AGENTS.md"], text=True
        )
    git = ["git", "-C", str(workspace)]
    agents = workspace / "AGENTS.md"
    conflicts = subprocess.check_output([*git, "diff", "--name-only", "--diff-filter=U"], text=True)
    if conflicts:
        warning.write_text(
            f"{profile.name}: doctrine update deferred because this workspace has unresolved Git conflicts. "
            "Ask the agent to resolve them, then restart. Your work is retained."
        )
        return
    if not agents.exists() or agents.read_text() != canonical:
        subprocess.run([*git, "fetch", "origin", "main"], check=True)
        result = subprocess.run(
            [*git, "merge", "--no-edit", "origin/main"], capture_output=True, text=True, check=False
        )
        if result.returncode:
            merge_head = subprocess.run(
                [*git, "rev-parse", "--verify", "-q", "MERGE_HEAD"],
                capture_output=True,
                check=False,
            )
            if merge_head.returncode == 0:
                subprocess.run([*git, "merge", "--abort"], check=True)
            warning.write_text(
                f"{profile.name}: doctrine update deferred because Git could not merge canonical main. "
                "The attempted merge was aborted; your work is retained. "
                "Ask the agent to resolve overlapping edits, then restart."
            )
            return
        if agents.read_text() != canonical:
            warning.write_text(
                f"{profile.name}: doctrine update deferred because local doctrine differs from canonical main. "
                "Ask the agent to reconcile AGENTS.md, then restart. Your work is retained."
            )
            return
    warning.with_suffix(".reported").unlink(missing_ok=True)
    warning.unlink(missing_ok=True)


def prepare(source: Path, hermes: str, activate_supervisor: bool) -> None:
    profile = Path(os.environ["HERMES_HOME"])
    home = Path(os.environ["HERMES_REAL_HOME"])
    workspace = Path(os.environ["RESEARCH_WORKSPACE"])
    remote = Path(os.environ["RESEARCH_CANONICAL"])
    (profile / "local").mkdir(exist_ok=True)
    (home / "games").mkdir(exist_ok=True)
    reconcile_workspace(profile, workspace, remote, source)
    defaults = yaml.safe_load((source / "config.yaml").read_text())
    append = {"terminal.env_passthrough": defaults["terminal"]["env_passthrough"]}
    coordinator = (source / "coordinator.json").exists()
    if coordinator:
        append["platform_toolsets.api_server"] = defaults["platform_toolsets"]["api_server"]
    else:
        append["approvals.deny"] = defaults["approvals"]["deny"]
    for key, required in append.items():
        current = json.loads(
            subprocess.check_output([hermes, "config", "get", key, "--json"], text=True)
        )
        desired = list(dict.fromkeys([*current, *required]))
        if desired != current:
            subprocess.run([hermes, "config", "set", key, json.dumps(desired)], check=True)
    if not coordinator:
        return
    EpisodeBudget(
        remote / "episode-budget.db", int(os.environ["RESEARCH_EPISODE_LIMIT"])
    ).configure()
    jobs_path = profile / "cron/jobs.json"
    jobs = Jobs.model_validate_json(jobs_path.read_text())
    shipped = Jobs.model_validate_json((source / "cron/jobs.json").read_text())
    supervisor = next(job for job in shipped.jobs if job.id == "supervisor")
    for job in jobs.jobs:
        if job.id == "supervisor":
            job.no_agent, job.script, job.prompt = (
                supervisor.no_agent,
                supervisor.script,
                supervisor.prompt,
            )
    pending = jobs_path.with_suffix(".pending")
    pending.write_text(jobs.model_dump_json(indent=2) + "\n")
    pending.replace(jobs_path)
    subprocess.run(
        [
            hermes,
            "config",
            "set",
            "platform_toolsets.cron",
            json.dumps(defaults["platform_toolsets"]["cron"]),
        ],
        check=True,
    )
    root_env = dict(os.environ, HERMES_HOME=str(home))
    subprocess.run(
        [hermes, "config", "set", "kanban.auto_decompose", "false"],
        env=root_env,
        check=True,
    )
    assignee = subprocess.check_output(
        [hermes, "config", "get", "kanban.default_assignee"], env=root_env, text=True
    )
    if assignee.strip():
        subprocess.run(
            [hermes, "config", "unset", "kanban.default_assignee"],
            env=root_env,
            check=True,
        )
    marker = profile / ".softmax-prepared"
    if activate_supervisor and not marker.exists():
        subprocess.run(
            [hermes, "kanban", "boards", "create", "autoresearch"],
            env=root_env,
            check=True,
        )
        subprocess.run([hermes, "cron", "resume", "supervisor"], check=True)
        marker.touch()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Pristine distribution payload")
    parser.add_argument("--hermes", required=True, help="Supported Hermes CLI executable")
    parser.add_argument(
        "--activate-supervisor",
        action="store_true",
        help="Enable on first preparation only",
    )
    args = parser.parse_args()
    prepare(args.source, args.hermes, args.activate_supervisor)


if __name__ == "__main__":
    main()
