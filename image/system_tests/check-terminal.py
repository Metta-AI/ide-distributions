"""Exercise the packaged profiles' real Hermes terminal environment, without model calls."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, "/opt/hermes")
from hermes_constants import reset_hermes_home_override, set_hermes_home_override  # noqa: E402
from tools.environments.local import LocalEnvironment  # noqa: E402

for role in ("orchestrator", "researcher"):
    home = Path("/workspace/host/profiles") / role
    profile_env = dotenv_values(home / ".env")
    scope = set_hermes_home_override(home)
    terminal = LocalEnvironment(cwd="/workspace", env={**profile_env, "HERMES_HOME": str(home)})
    try:
        result = terminal.execute(
            "command -v research && command -v coworld "
            "&& research --version && coworld build-policy --help >/dev/null"
        )
        assert result["returncode"] == 0, (role, result)
        assert "/opt/workspace-env/bin/research" in result["output"], result
        assert "/opt/workspace-env/bin/coworld" in result["output"], result
        if "BUILDKIT_HOST" in os.environ:
            assert profile_env["BUILDKIT_HOST"] == os.environ["BUILDKIT_HOST"]
            result = terminal.execute('test -n "$BUILDKIT_HOST" && buildctl debug workers')
            assert result["returncode"] == 0, (role, result)
        print(f"PASS {role} terminal: packaged workspace commands")
    finally:
        terminal.cleanup()
        reset_hermes_home_override(scope)
