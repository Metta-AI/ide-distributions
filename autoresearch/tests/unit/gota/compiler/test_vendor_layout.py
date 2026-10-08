"""The imported compiler closure must resolve inside the vendored tree."""

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

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


def test_gota_default_is_hermes_with_explicit_backends():
    compiler = Path(__file__).resolve().parents[4] / "games/gota/compiler"
    config = json.loads((compiler / "strategy/compiler/config.json").read_text())
    assert config["agent"] == "hermes"
    assert config["models"] == {"claude": "claude-opus-5-5", "codex": "gpt-6-astra",
                                "hermes": "moonshotai/kimi-k3"}


def test_gate_body_keeps_pinned_bytes():
    compiler = Path(__file__).resolve().parents[4] / "games/gota/compiler"
    data = (compiler / "tools/strategy_gates.py").read_bytes().splitlines(keepends=True)
    assert data[0].startswith(b"# Vendored from personal_labs_paintbot_pw@")
    body = b"".join(data[1:])
    assert hashlib.sha256(body).hexdigest() == "c212518f9a5fe5f59c63f9c60d89459fa7c81de189d13eb8ad079f90c4681cb4"


@pytest.mark.parametrize(('relative', 'source_hash'), [
    ('context/gota-guide.md', 'cae705ba912fed790a5d6e68225084daf29b2aea47477e2065e715197da8a7fc'),
    ('context/bassy-language.md', 'c8b3619608c107a25fed5d2f277c10eae42faaa4033d8c63e8479efc2f9633de'),
    ('context/structures.bas', 'fef65b46b9881fa593fdb52d1d3ee40b2210a15f69e304a35238f70f1e58445c'),
    ('host_surface.json', '0f77e77c2067114abde60de7de21780d0c70543766eb3ac13c276012c9998265'),
])
def test_gota_context_provenance_preserves_upstream_content(relative, source_hash):
    compiler = TOOLS.parent / 'strategy/compiler'
    data = (compiler / relative).read_bytes()
    provenance = (
        'Vendored from personal_labs_gota@09d3ed28d488fa965e36d4d4f6d99439952e18c7: '
        f'gods_of_the_arena_lab/strategy/compiler/{relative}'
    )
    lines = data.splitlines(keepends=True)
    if relative.endswith('.json'):
        parsed = json.loads(data)
        assert parsed.pop('_provenance') == provenance
        body = b''.join(lines[:1] + lines[2:])
        assert json.loads(body) == parsed
    else:
        header = f"' {provenance}" if relative.endswith('.bas') else f'<!-- {provenance} -->'
        assert lines[0].decode().rstrip('\n') == header
        body = b''.join(lines[1:])
    assert hashlib.sha256(body).hexdigest() == source_hash


@pytest.mark.parametrize(('destination', 'source'), [
    ('strategy/compiler/LESSONS.md', 'paintbot_pw_lab/strategy/compiler/LESSONS.md'),
    ('docs/policy-surface.md', 'paintbot_pw_lab/docs/policy-surface.md'),
])
def test_adapted_docs_retain_original_full_provenance(destination, source):
    lines = (TOOLS.parent / destination).read_text().splitlines()
    assert lines[0] == (
        '<!-- Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: '
        f'{source} -->'
    )
    assert lines[1].startswith(('<!-- Adapted from', '<!-- Destination adaptation'))
