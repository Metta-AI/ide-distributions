"""The tool surface (design C.2, amendment A3.1): counts, roles and executors."""

import pytest

from research_core.tools import catalog

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
}
RESEARCHER = ORCHESTRATOR - {"ui", "submit", "github"}
INPROC = {"goal", "task", "directive", "claim", "settings", "ui"}


def names(role):
    return {spec.name for spec in catalog.tools_for(role)}


def test_a_broken_tool_module_fails_loudly(tmp_path, monkeypatch):
    (tmp_path / "broken_tool_module.py").write_text("def f():\n        x = 1\n    y = 2\n")
    monkeypatch.syspath_prepend(tmp_path)
    monkeypatch.setattr(catalog, "TOOL_MODULES", ("broken_tool_module",))
    monkeypatch.setattr(catalog, "_loaded", False)
    with pytest.raises(IndentationError):
        catalog.tools_for("orchestrator")
    assert catalog._loaded is False


def test_tool_counts_match_the_design():
    assert names("orchestrator") == ORCHESTRATOR and len(ORCHESTRATOR) == 17
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
