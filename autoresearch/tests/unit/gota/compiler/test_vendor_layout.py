"""The imported compiler closure must resolve inside the vendored tree."""

import hashlib
import importlib
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[4] / "games/gota/compiler/tools"
sys.path.insert(0, str(TOOLS))


def test_import_closure_is_local():
    root = Path(__file__).resolve().parents[4]
    compiler = root / "games/gota/compiler"
    for name in (
        "strategy_format", "strategy_basic", "strategy_comms", "strategy_build",
        "strategy_gates", "pw_cli", "pw_release", "pw_strategy", "pw_local",
        "pw_terrain", "pw_intent", "pw_episodes", "strategy_audit",
        "strategy_audit_runtime", "strategy_audit_baseline", "strategy_audit_comms",
        "pw_scout", "pw_fights", "pw_metrics", "pw_public",
    ):
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(compiler)
    release = importlib.import_module("pw_release")
    assert release.LAB == compiler
    assert release.REPO == root.parent


def test_input_inventory_is_inside_checkout_and_complete():
    builds = importlib.import_module("strategy_build")
    source = Path(__file__).parent / "fixtures/strategy_trivial/STRATEGY.md"
    paths = builds.source_files(source)
    assert paths
    assert all(path.is_file() and path.is_relative_to(builds.REPO) for path in paths)
    assert not any("personal_labs_paintbot_pw" in str(path) for path in paths)


def test_provenance_config_does_not_change_agent_selection():
    compiler = Path(__file__).resolve().parents[4] / "games/gota/compiler"
    config = json.loads((compiler / "strategy/compiler/config.json").read_text())
    assert config["agent"] == "claude"
    assert config["models"] == {"claude": "claude-opus-5-5", "codex": "gpt-6-astra",
                                "hermes": "moonshotai/kimi-k3"}


def test_gate_body_keeps_pinned_bytes():
    compiler = Path(__file__).resolve().parents[4] / "games/gota/compiler"
    data = (compiler / "tools/strategy_gates.py").read_bytes().splitlines(keepends=True)
    assert data[0].startswith(b"# Vendored from personal_labs_paintbot_pw@")
    body = b"".join(data[1:])
    assert hashlib.sha256(body).hexdigest() == "c212518f9a5fe5f59c63f9c60d89459fa7c81de189d13eb8ad079f90c4681cb4"
