"""Thin synchronous adapters for Claude's shared CLI contract."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

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

    def _canonical_text(self, path):
        """Shared decisions come from canonical history, never an unfinished local draft."""
        canonical = self.context.env["RESEARCH_CANONICAL"]
        present = subprocess.run(
            ["git", "--git-dir", canonical, "ls-tree", "--name-only", "main", path],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        if not present:
            return None
        pause = subprocess.run(
            ["git", "--git-dir", canonical, "show", f"main:{path}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return pause.stdout

    def control_read(self):
        text = self._canonical_text("control/pause.json")
        return json.loads(text) if text is not None else None

    def plan_read(self):
        return self._canonical_text("plan.md")

    def budget_read(self):
        from ide_research_core.budget import EpisodeBudget

        if "RESEARCH_EPISODE_LIMIT" not in self.context.env:
            return None
        return EpisodeBudget(
            Path(self.context.env["RESEARCH_CANONICAL"]) / "episode-budget.db",
            int(self.context.env["RESEARCH_EPISODE_LIMIT"]),
        ).snapshot()

    def _publish(self, path, text, message):
        target = self.context.workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return self.call("workspace", "publish", "--message", message, "--path", path)

    def promotion_submit(self, experiment):
        return self.call("promotion", "submit", experiment)

    def bootstrap_champion(self, version):
        return self.call("promotion", "bootstrap", "--version", version)

    def accept_champion(self):
        return self.call("promotion", "accept")

    def xp_status(self, experiment):
        return self.call("softmax", "xp", "status", experiment)

    def budget_limit(self, episodes):
        from ide_research_core.budget import EpisodeBudget

        return EpisodeBudget(
            Path(self.context.env["RESEARCH_CANONICAL"]) / "episode-budget.db",
            int(self.context.env["RESEARCH_EPISODE_LIMIT"]),
        ).lower(episodes)

    def credits(self):
        return self.call("softmax", "credits")

    def leaderboard(self):
        return self.call("softmax", "leaderboard")

    def latest_league_result(self):
        return self.call("softmax", "latest-league-result")
