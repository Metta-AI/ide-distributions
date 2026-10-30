import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "check_prompts", REPO / "tools" / "check_prompts.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_prompts"] = module
    spec.loader.exec_module(module)
    return module


def test_structural_checks_pass_on_the_shipped_prompts():
    module = load_module()
    problems = [p for role in ("orchestrator", "researcher") for p in module.structural(role)]
    assert problems == []


def test_forbidden_words_are_caught(monkeypatch, tmp_path):
    module = load_module()
    bad = tmp_path / "skills" / "common" / "x" / "bad" / "SKILL.md"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "---\nname: bad\ndescription: Bad.\n---\nWe promote the winner from .state/store.db.\n"
    )
    monkeypatch.setattr(module, "SKILLS", tmp_path / "skills")
    problems = module.structural("researcher")
    assert any("promotion" in p for p in problems) and any("store path" in p for p in problems)


def test_main_skips_the_model_check_without_a_key(monkeypatch, capsys):
    module = load_module()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert module.main(["--model"]) == 0
    assert "model check skipped" in capsys.readouterr().out
