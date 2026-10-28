"""The Hermes process loads the plugin without python-frontmatter.

Hermes' own interpreter need not have the profile's dependencies. ``frontmatter`` is used only
by research commands, which run in the profile's venv as a subprocess, so importing the plugin
must not import it. A real import in a fresh interpreter with ``frontmatter`` blocked locks that in.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = """
import importlib.util, sys
sys.modules['frontmatter'] = None  # `import frontmatter` now raises ImportError
spec = importlib.util.spec_from_file_location('plugin_under_test', sys.argv[1])
spec.loader.exec_module(importlib.util.module_from_spec(spec))
try:
    import ide_research_core.refs  # a module that does need frontmatter: proves the block works
except ImportError:
    print('blocked')
"""


def test_plugin_imports_without_frontmatter():
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(ROOT / "plugins/orchestrator/__init__.py")],
        env={"HERMES_HOME": str(ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"


LOAD = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('plugin_under_test', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.register.__module__, sys.modules['orchestrator'].__file__)
"""


def test_plugin_loads_the_image_copy_when_the_writable_src_is_broken(tmp_path):
    """Hosted profiles edit their own src. A broken edit must not take away the agent's tools:
    the plugin loads the image copy (recorded by hosting) and records the error for status."""
    profile = tmp_path / "orchestrator"
    entry = profile / "plugins/orchestrator/__init__.py"
    entry.parent.mkdir(parents=True)
    entry.write_text((ROOT / "plugins/orchestrator/__init__.py").read_text())
    broken = profile / "src/orchestrator"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("")
    (broken / "plugin.py").write_text("raise SyntaxError('agent edit')\n")
    (profile / "local").mkdir()
    (profile / "local/tooling-source.txt").write_text(str(ROOT))

    def load():
        return subprocess.run(
            [sys.executable, "-c", LOAD, str(entry)],
            env={"HERMES_HOME": str(profile), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, check=False, timeout=60,
        )

    result = load()
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["orchestrator.plugin", str(ROOT / "src/orchestrator/__init__.py")]
    record = json.loads((profile / "local/tooling-fallback.json").read_text())
    assert "agent edit" in record["error"] and record["using"] == str(ROOT / "src")

    (broken / "plugin.py").write_text("def register(ctx):\n    pass\n")
    result = load()
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[1] == str(broken / "__init__.py")
    assert not (profile / "local/tooling-fallback.json").exists()
