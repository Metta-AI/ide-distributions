"""Exercise the packaged profiles' real Hermes terminal environment, without model calls."""

import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, "/opt/hermes")
from hermes_constants import reset_hermes_home_override, set_hermes_home_override  # noqa: E402
from tools.environments.local import LocalEnvironment  # noqa: E402
from tools.process_registry import process_registry  # noqa: E402
from tools.terminal_tool import FOREGROUND_MAX_TIMEOUT, terminal_tool  # noqa: E402

for role in ("orchestrator", "researcher"):
    home = Path("/workspace/host/profiles") / role
    profile_env = dotenv_values(home / ".env")
    scope = set_hermes_home_override(home)
    terminal = LocalEnvironment(cwd="/workspace", env={**profile_env, "HERMES_HOME": str(home)})
    try:
        command = (
            "command -v research && command -v coworld && command -v softmax "
            "&& research --version && coworld build-policy --help >/dev/null"
        )
        if "BUILDKIT_HOST" in os.environ:
            command += ' && test -n "$BUILDKIT_HOST" && buildctl debug workers'
        result = terminal.execute(command)
        assert result["returncode"] == 0, (role, result)
        assert "/opt/workspace-env/bin/research" in result["output"], result
        assert "/opt/workspace-env/bin/coworld" in result["output"], result
        for options in ({"background": True}, {"timeout": FOREGROUND_MAX_TIMEOUT + 1}):
            started = json.loads(
                terminal_tool(
                    command=command,
                    task_id=f"packaged-terminal-{role}",
                    workdir="/workspace",
                    **options,
                )
            )
            assert started["error"] is None, (role, options, started)
            assert "session_id" in started, (role, options, started)
            finished = process_registry.wait(started["session_id"], timeout=60)
            assert finished["exit_code"] == 0, (role, options, finished)
            assert "/opt/workspace-env/bin/coworld" in finished["output"], finished
        if "BUILDKIT_HOST" in os.environ:
            assert profile_env["BUILDKIT_HOST"] == os.environ["BUILDKIT_HOST"]
            result = terminal.execute('test -n "$BUILDKIT_HOST" && buildctl debug workers')
            assert result["returncode"] == 0, (role, result)
        print(f"PASS {role} terminal: packaged workspace commands")
    finally:
        terminal.cleanup()
        reset_hermes_home_override(scope)
