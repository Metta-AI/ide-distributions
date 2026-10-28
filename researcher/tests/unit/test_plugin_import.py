"""The Hermes process loads the researcher plugins without python-frontmatter.

Hermes' own interpreter need not have the profile's dependencies. ``frontmatter`` is used only
by research commands, which run in the profile's venv as a subprocess, so importing a plugin
must not import it. A real import in a fresh interpreter with ``frontmatter`` blocked locks that in.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PROBE = """
import importlib.util, sys
sys.modules['frontmatter'] = None  # `import frontmatter` now raises ImportError
spec = importlib.util.spec_from_file_location('plugin_under_test', sys.argv[1])
spec.loader.exec_module(importlib.util.module_from_spec(spec))
sys.path.insert(0, sys.argv[2])
try:
    import ide_research_core.refs  # a module that does need frontmatter: proves the block works
except ImportError:
    print('blocked')
"""


@pytest.mark.parametrize("plugin", ["researcher_tools", "researcher_guard"])
def test_plugin_imports_without_frontmatter(plugin):
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(ROOT / f"plugins/{plugin}/__init__.py"), str(ROOT / "src")],
        env={"HERMES_HOME": str(ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"
