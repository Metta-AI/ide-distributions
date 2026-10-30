"""Five-minute snapshot publisher. Failures are retained in the native cron history."""

import json
import subprocess
import sys

from watcher import workspace_env, workspace_python

if __name__ == "__main__":
    completed = subprocess.run(
        [str(workspace_python()), "-m", "research_core.cli", "publish"],
        env=workspace_env(),
        check=False,
    )
    print(json.dumps({"wakeAgent": False}))
    sys.exit(completed.returncode)
