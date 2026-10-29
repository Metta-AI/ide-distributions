"""The tool surface (design C.2, amendment A3.1): counts, roles, executors, stdlib guard."""

import subprocess
import sys
from pathlib import Path

from research_core.tools import catalog

CORE_SRC = Path(__file__).resolve().parents[1] / "src"

ORCHESTRATOR = {
    "goal",
    "task",
    "claim",
    "experiment",
    "version",
    "directive",
    "settings",
    "ui",
    "workspace",
    "upload",
    "submit",
    "xp",
    "platform",
    "forum_post",
    "wiki_edit",
    "softmax_api",
    "github",
    "attachment_download",
}
RESEARCHER = ORCHESTRATOR - {"ui", "submit", "github", "attachment_download"}
INPROC = {"goal", "task", "directive", "claim", "settings", "ui"}


def names(role):
    return {spec.name for spec in catalog.tools_for(role)}


def test_no_tool_module_failed_to_import():
    catalog.tools_for("orchestrator")
    assert catalog.LOAD_ERRORS == {}, catalog.LOAD_ERRORS


def test_tool_counts_match_the_design():
    assert names("orchestrator") == ORCHESTRATOR and len(ORCHESTRATOR) == 18
    assert names("researcher") == RESEARCHER and len(RESEARCHER) == 14
    assert len(ORCHESTRATOR) <= catalog.TOOL_BUDGET


def test_executor_table():
    for spec in catalog.tools_for("orchestrator"):
        expected = "inproc" if spec.name in INPROC else "workspace"
        assert spec.executor == expected, (spec.name, spec.executor)


def test_schemas_render_for_both_roles():
    for role in catalog.ROLES:
        for spec in catalog.tools_for(role):
            schema = spec.schema(role)
            assert set(schema) == {"name", "description", "parameters"}
            if spec.actions is not None:
                assert schema["parameters"]["properties"]["action"]["enum"]


def test_in_process_imports_are_stdlib_only():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(CORE_SRC)!r})\n"
        "import research_core.tools, research_core.tools.catalog as c\n"
        "import research_core.plugin.register, research_core.plugin.advisory\n"
        "import research_core.plugin.turn, research_core.plugin.session_context\n"
        "import research_core.plugin.briefing\n"
        "class Ctx:\n"
        "    def register_tool(self, **k): pass\n"
        "    def register_hook(self, *a, **k): pass\n"
        "    def register_system_prompt_section(self, *a, **k): pass\n"
        "for role in ('orchestrator', 'researcher'):\n"
        "    research_core.plugin.register.make_register(role)(Ctx())\n"
        "assert not c.LOAD_ERRORS, c.LOAD_ERRORS\n"
        "third = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'yaml','numpy','coworld','softmax_client','softmax','httpx','pydantic','jsonschema'})\n"
        "assert not third, third\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
