"""Thin synchronous adapters for Claude's shared CLI contract."""

import json
import subprocess
from datetime import UTC, datetime

from .context import Context
from .launch import ensure_venv


class ResearchError(RuntimeError):
    def __init__(self, envelope):
        self.envelope = envelope
        super().__init__(envelope["error"]["message"])


class Research:
    def __init__(self, context: Context):
        self.context = context

    def call(self, *args):
        ensure_venv(self.context.home)
        result = subprocess.run(
            [
                str(self.context.home / "local/venv/bin/python"),
                "-m",
                "ide_research_core",
                *map(str, args),
            ],
            env=self.context.env,
            cwd=self.context.home,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        envelope = json.loads(result.stdout)
        if not envelope["ok"]:
            raise ResearchError(envelope)
        result.check_returncode()
        return envelope["data"]

    def records_find(self, entity, filters=None):
        return self.call(
            "records", "find", entity, *(f"{key}={value}" for key, value in (filters or {}).items())
        )

    def records_show(self, id):
        return self.call("records", "show", id)

    def plan_write(self, text):
        return self._publish("plan.md", text, "Update research plan")

    def control_write(self, action, reason):
        if action not in ("pause", "resume"):
            raise ValueError("action must be pause or resume")
        payload = {
            "paused": action == "pause",
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
            "by": "orchestrator",
        }
        return self._publish(
            "control/pause.json",
            json.dumps(payload, indent=2) + "\n",
            f"Research {action}: {reason}",
        )

    def control_read(self):
        """Read the published pause, not an uncommitted file in this profile's clone."""
        canonical = self.context.env["RESEARCH_CANONICAL"]
        present = subprocess.run(
            ["git", "--git-dir", canonical, "ls-tree", "--name-only", "main", "control/pause.json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if not present:
            return None
        pause = subprocess.run(
            ["git", "--git-dir", canonical, "show", "main:control/pause.json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(pause.stdout)

    def _publish(self, path, text, message):
        target = self.context.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return self.call("workspace", "publish", "--message", message, "--path", path)

    def promotion_submit(self, experiment):
        return self.call("promotion", "submit", experiment)

    def bootstrap_champion(self, version):
        return self.call("promotion", "bootstrap", "--version", version)

    def release_program(self, policy_version):
        return self.call("promotion", "release", "--policy-version", policy_version)

    def accept_champion(self):
        return self.call("promotion", "accept")

    def workspace_integrate(self, branch):
        return self.call("workspace", "integrate", "--branch", branch)

    def xp_status(self, experiment):
        return self.call("softmax", "xp", "status", experiment)

    def credits(self):
        return self.call("softmax", "credits")

    def leaderboard(self):
        return self.call("softmax", "leaderboard")
