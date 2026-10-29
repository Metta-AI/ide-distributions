"""Point the fake platform at a fakegame fixture repository (used by ``tools/local_stack.py``).

Run from the workspace environment with the Orchestrator's environment exported::

    python tools/local_stack/configure_fake_platform.py <upstream directory>

It builds ``<upstream directory>/fakegame_repo`` with ``core/tests/fixtures/fakegame_repo.py``
when it does not exist yet (a second run keeps the repository and its commit) and writes the
repository's URL and commit into the fake platform's state file, exactly as
``core/tests/hermes/test_seam_real_hermes.py`` does before the acceptance scenario.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "core"))  # the tests package, for the fixture

from tests.fixtures.fakegame_repo import make_fakegame_repo  # noqa: E402

from research_core import layout  # noqa: E402
from research_core.platform.fake import FakePlatform  # noqa: E402


def fixture_repo(upstream: Path) -> tuple[Path, str]:
    """The fixture repository under ``upstream`` and its HEAD commit; built when missing."""
    repo = upstream / "fakegame_repo"
    if not (repo / ".git").is_dir():
        return make_fakegame_repo(upstream)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return repo, head.stdout.strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    upstream = Path(argv[1])
    upstream.mkdir(parents=True, exist_ok=True)
    ws = layout.from_env(os.environ)
    ws.ensure_dirs()
    repo, commit = fixture_repo(upstream)
    state_path = ws.state / "fake_platform.json"
    FakePlatform(state_path).configure(source_url=repo.as_uri(), source_commit=commit)
    summary = {"source_url": repo.as_uri(), "source_commit": commit, "state": str(state_path)}
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
