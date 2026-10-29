import subprocess
import sys
from pathlib import Path

from research_core.plugin import register

CORE_SRC = Path(__file__).resolve().parents[1] / "src"


class FakeContext:
    def __init__(self):
        self.tools = []
        self.hooks = []
        self.sections = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_system_prompt_section(self, section_id, content, **kwargs):
        self.sections.append((section_id, content, kwargs))


def test_make_register_builds_a_callable_per_role():
    for role in ("orchestrator", "researcher"):
        ctx = FakeContext()
        register.make_register(role)(ctx)
        for tool in ctx.tools:
            assert tool["toolset"] == f"ide-{role}"
            assert set(tool["schema"]) >= {"name", "description", "parameters"}


def test_plugin_imports_are_stdlib_only():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(CORE_SRC)!r})\n"
        "import research_core.plugin.register, research_core.plugin.executor\n"
        "import research_core.layout\n"
        "third = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'yaml','numpy','coworld','softmax_client','softmax','httpx','pydantic','jsonschema'})\n"
        "assert not third, third\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
