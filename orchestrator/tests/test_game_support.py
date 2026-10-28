import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ide_research_core.softmax_tool.discovery import Discovery
from ide_research_core.softmax_tool.protocol import Refused
from orchestrator.board import CardFilters
from orchestrator.plugin import TOOLS
from orchestrator.research import Research


@pytest.fixture
def discovery(tmp_path):
    client = Discovery({"HERMES_HOME": str(tmp_path)})
    guide = "Hook 🎯 then retreat.\n" * 1500
    league = {
        "id": "league_pudge", "name": "Pudge", "participation_url": "https://example.test",
        "game": {"id": "game_pudge", "name": "Pudge", "coworld_id": "cow_pudge"},
    }
    coworld = {"id": "cow_pudge", "name": "Pudge", "version": "1", "manifest": {"guide": guide}}
    calls = []

    def read(route):
        calls.append(route)
        return {
            "/observatory/v2/leagues/league_pudge": league,
            "/observatory/v2/coworlds/cow_pudge": coworld,
        }[route]

    client.cli.api_get_json = read
    client.cli.api_get_text = lambda route: guide
    client.client.whoami = lambda: SimpleNamespace(player_id="transport_player")
    return client, guide, coworld, calls


def test_summary_omits_documents_and_reads_exact_coworld(discovery):
    client, guide, _, calls = discovery
    result = client.game_support("league_pudge")
    assert result["coworld"] == {"id": "cow_pudge", "name": "Pudge", "version": "1"}
    assert result["document"] is None
    assert [item["id"] for item in result["documents"]] == ["participation", "manifest"]
    assert result["credential_player_id"] == "transport_player"
    assert result["research_bindings_available"] is False
    assert len(json.dumps(result).encode()) < 2000 < len(guide.encode())
    assert calls == ["/observatory/v2/leagues/league_pudge", "/observatory/v2/coworlds/cow_pudge"]


def test_unicode_pages_reconstruct_document_and_changed_release_fails(discovery):
    client, _, coworld, _ = discovery
    index = client.game_support("league_pudge")
    metadata = next(doc for doc in index["documents"] if doc["id"] == "manifest")
    parts, offset = [], 0
    while offset is not None:
        page = client.game_support("league_pudge", "manifest", offset, 6000, metadata["sha256"])["document"]
        assert len(page["content"]) <= 6000
        assert page["offset_unit"] == "unicode_characters"
        parts.append(page["content"])
        offset = page["next_offset"]
    assert json.loads("".join(parts)) == coworld["manifest"]
    coworld["manifest"]["guide"] = "new release"
    with pytest.raises(Refused) as error:
        client.game_support("league_pudge", "manifest", expected_sha256=metadata["sha256"])
    assert error.value.reason == "document_changed"


@pytest.mark.parametrize("arguments", [
    {"limit": 6001}, {"offset": -1}, {"document": "missing"},
    {"offset": 1}, {"document": "manifest", "offset": 1},
    {"document": "manifest", "offset": 1_000_000, "expected_sha256": "0" * 64},
])
def test_invalid_reads_fail_without_returning_partial_documents(discovery, arguments):
    client, _, _, _ = discovery
    with pytest.raises((Refused, ValidationError)):
        client.game_support("league_pudge", **arguments)


def test_wrong_coworld_is_not_returned_as_selected_game(discovery):
    client, _, coworld, _ = discovery
    coworld["id"] = "cow_other"
    with pytest.raises(Refused) as error:
        client.game_support("league_pudge")
    assert error.value.reason == "scope_mismatch"


def test_installed_guide_is_available_by_reference_without_changing_workspace(discovery, monkeypatch):
    from ide_research_core.softmax_tool import discovery as module

    client, _, _, _ = discovery
    root = Path(client.env["HERMES_HOME"])  # an installed game lives in the host's games/
    contract = root / "games/pudge/GAME.md"
    contract.parent.mkdir(parents=True)
    contract.write_text("installed contract")
    game = SimpleNamespace(
        name="pudge", league_id="league_pudge", guide="Check hooks before uploading.",
        bindings={"local_check": "check-policy"}, host_env=(), host_tools=(),
    )
    real = module.load_game  # shipped games load for real; only the installed one is faked
    monkeypatch.setattr(
        module, "load_game", lambda root, name: game if name == "pudge" else real(root, name)
    )
    index = client.game_support("league_pudge")
    assert index["research_bindings_available"] is False
    assert index["installed_research_support"][0]["bindings"] == game.bindings
    assert index["installed_research_support"][0]["readiness"]["operations"]["build"]["status"] == "blocked"
    metadata = next(doc for doc in index["documents"] if doc["id"] == "research:pudge")
    page = client.game_support("league_pudge", metadata["id"], expected_sha256=metadata["sha256"])
    assert page["document"]["content"] == game.guide
    assert contract.read_text() == "installed contract"
    assert not (root / "workspace").exists()


def test_document_arguments_survive_subprocess_adapter(shared_fixture):
    result = Research(shared_fixture).game_support("league_pudge", "manifest", 12, 100, "a" * 64)
    assert result["args"] == [
        "softmax", "game-support", "--league", "league_pudge", "--offset", "12", "--limit", "100",
        "--document", "manifest", "--expected-sha256", "a" * 64,
    ]


def test_advertised_filters_reject_the_observed_sort_error():
    # The filters are top-level parameters, so the natural card_list(limit=5) validates.
    schema = {"type": "object", "properties": TOOLS["card_list"][2], "required": TOOLS["card_list"][3]}
    assert Draft202012Validator(schema).is_valid({"limit": 5})
    assert Draft202012Validator(schema).is_valid({"order_by": "updated", "limit": 10})
    assert not Draft202012Validator(schema).is_valid({"order_by": "updated_at"})
    assert not Draft202012Validator(schema).is_valid({"limit": 0})
    with pytest.raises(ValidationError):
        CardFilters(order_by="updated_at")
    with pytest.raises(ValidationError):
        CardFilters(league_id="invented")
    assert CardFilters().limit == 20


def test_starter_read_verifies_bytes_and_pins_pagination(discovery):
    import hashlib

    client, _, coworld, _ = discovery
    data = ('PRINT "hello 🎯"\n' * 1000).encode()
    digest = hashlib.sha256(data).hexdigest()
    coworld["manifest"]["player"] = [{"id": "base", "name": "Base", "file": f"sha256:{digest}"}]
    calls = []
    def download(path, *, max_bytes):
        calls.append((path, max_bytes))
        return data
    client.cli.api_get_bytes = download
    index = client.game_support("league_pudge")
    assert index["published_starters"][0]["sha256"] == digest
    parts, offset = [], 0
    while offset is not None:
        page = client.game_starter("league_pudge", "base", digest, offset, 6000)
        assert page["verified"] is True
        assert page["source_kind"] == "published_starter_not_player_submission"
        parts.append(page["content"])
        offset = page["next_offset"]
    assert ''.join(parts).encode() == data
    assert calls[0] == (f"/observatory/v2/coworlds/cow_pudge/player-files/{digest}", 256_000)
    client.cli.api_get_bytes = lambda *a, **kw: b"tampered"
    with pytest.raises(Refused, match="published hash"):
        client.game_starter("league_pudge", "base", digest)
    with pytest.raises(Refused, match="Starter changed"):
        client.game_starter("league_pudge", "base", "0" * 64)
    with pytest.raises(Refused, match="Choose a published starter"):
        client.game_starter("league_pudge", "../private", digest)


def test_static_readiness_detects_stubs_and_host_dependencies(tmp_path):
    from ide_research_core.games.loader import BINDING_NAMES
    from ide_research_core.games.readiness import binding_readiness

    (tmp_path / "stub.py").write_text('raise NotImplementedError("TODO")')
    (tmp_path / "evaluate.py").write_text('print("real evaluator")')
    game = SimpleNamespace(
        bindings={name: "python3 stub.py" for name in BINDING_NAMES}, host_env=(), host_tools=(),
    )
    game.bindings["evaluate"] = "python3 evaluate.py"
    result = binding_readiness(game, tmp_path, {})
    assert result["operations"]["build"]["reasons"] == ["stub_marker_requires_review"]
    assert result["operations"]["evaluate"]["status"] == "configured"
    assert result["execution_verified"] is False
    game.host_env = ("ENGINE_ROOT",)
    game.host_tools = ("nonexistent-engine",)
    result = binding_readiness(game, tmp_path, {})
    assert result["missing_environment"] == ["ENGINE_ROOT"]
    assert result["missing_tools"] == ["nonexistent-engine"]
    assert all(item["status"] == "blocked" for item in result["operations"].values())
