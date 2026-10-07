"""Resolver (Task 3.5): every core type at HEAD and at an older rev, anchors, game types."""

import json
from dataclasses import replace

import pytest
from runtime.workspace import AUTORESEARCH_DIR, Canonical

from autoresearch.comms.resolver import CORE_TYPES, ResolveError, resolve
from autoresearch.games.loader import load_game
from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import write_doc, write_json

XREQ = "xreq_b0c4ff36-2d9e-4ddb-8224-9ba8fa0c2fd5"
EPISODE = "ep_7f1c"
OPPONENT = "andre-v7"
SKILL = "replay-triage"
DOCTRINE = "ack-early"
JOURNAL_BODY = (
    "Narrative {v}.\n\n## Decisions\n\n"
    "- Keep the farm plan ({v}).\n  Because last hits rose.\n"
    "- Drop the tower dive ({v}).\n\n## Notes\n\n- not a decision\n"
)


class Workspace:
    """One file per core type, written at version ``v1`` then changed to ``v2``."""

    def __init__(self, root):
        self.root = root
        self.ids = {
            entity: new_id(entity)
            for entity in (
                "topic",
                "hypothesis",
                "experiment",
                "result",
                "version",
                "claim",
                "hub",
                "lesson",
                "journal",
                "evidence",
            )
        }
        self.episode_evidence = new_id("evidence")
        self.opponent_hub = new_id("hub")
        self.handoff = ("K-1", "r-1")
        gitops.git(root, "init", "-b", "main")
        gitops.git(root, "config", "user.email", "t@example.com")
        gitops.git(root, "config", "user.name", "T")

    def write(self, v: str) -> str:
        root, ids = self.root, self.ids
        for entity, directory in (
            ("topic", "topics"),
            ("hypothesis", "hypotheses"),
            ("experiment", "experiments"),
            ("result", "results"),
            ("claim", "kb/claims"),
            ("hub", "kb/hubs"),
            ("lesson", "lessons"),
        ):
            write_doc(root / directory / f"{ids[entity]}.md", {"id": ids[entity]}, f"{entity} {v}")
        write_doc(
            root / "journal" / f"{ids['journal']}.md",
            {"id": ids["journal"]},
            JOURNAL_BODY.format(v=v),
        )
        write_json(
            root / "versions" / ids["version"] / "version.json", {"id": ids["version"], "v": v}
        )
        write_json(
            root / "evidence" / "index" / f"{ids['evidence']}.json",
            {"id": ids["evidence"], "kind": "xp-result", "source_ids": {"xreq": XREQ}, "v": v},
        )
        write_json(
            root / "evidence" / "index" / f"{self.episode_evidence}.json",
            {
                "id": self.episode_evidence,
                "kind": "replay",
                "source_ids": {"episodes": [EPISODE]},
                "v": v,
            },
        )
        write_doc(
            root / "kb" / "hubs" / f"{self.opponent_hub}.md",
            {"id": self.opponent_hub, "opponent": OPPONENT},
            f"opponent {v}",
        )
        (root / "kb" / "index.md").write_text(f"# Index {v}\n")
        (root / "plan.md").write_text(f"# Plan {v}\n")
        (root / "notes").mkdir(exist_ok=True)
        (root / "notes" / "a.md").write_text(f"file {v}\n")
        write_json(root / ".supervisor/ledger/handoffs/K-1/r-1.json", {"v": v})
        (root / "skills-proposed" / SKILL).mkdir(parents=True, exist_ok=True)
        (root / "skills-proposed" / SKILL / "SKILL.md").write_text(f"skill {v}\n")
        (root / "doctrine-proposed").mkdir(exist_ok=True)
        (root / "doctrine-proposed" / f"{DOCTRINE}.md").write_text(f"doctrine {v}\n")
        return gitops.commit_all(root, v)

    def uris(self) -> dict[str, str]:
        ids = self.ids
        uris = {entity: f"ar://{entity}/{id_}" for entity, id_ in ids.items()}
        uris |= {
            "wiki": f"ar://wiki/{ids['claim']}",
            "plan": "ar://plan",
            "xreq": f"ar://xreq/{XREQ}",
            "episode": f"ar://episode/{EPISODE}",
            "file": "ar://file/notes/a.md",
            "handoff": "ar://handoff/K-1/r-1",
            "skill-proposal": f"ar://skill-proposal/{SKILL}",
            "doctrine-proposal": f"ar://doctrine-proposal/{DOCTRINE}",
            "opponent": f"ar://opponent/{OPPONENT}",
        }
        return uris


@pytest.fixture
def workspace(tmp_path):
    handle = Workspace(tmp_path)
    handle.old = handle.write("v1")
    handle.new = handle.write("v2")
    return handle


def test_the_core_types_are_the_spec_list():
    assert set(CORE_TYPES) == {
        "wiki",
        "hub",
        "claim",
        "topic",
        "hypothesis",
        "experiment",
        "result",
        "version",
        "lesson",
        "journal",
        "plan",
        "evidence",
        "xreq",
        "episode",
        "file",
        "handoff",
        "skill-proposal",
        "doctrine-proposal",
        "opponent",
    }


@pytest.mark.parametrize("kind", sorted(CORE_TYPES))
def test_every_core_type_resolves_now_and_at_an_older_rev(workspace, kind):
    uri = workspace.uris()[kind]
    now = resolve(workspace.root, uri)
    then = resolve(workspace.root, uri, rev=workspace.old)
    assert b"v2" in now.content and now.rev == workspace.new
    assert b"v1" in then.content and b"v2" not in then.content and then.rev == workspace.old
    assert now.path == then.path


def test_paths_of_derived_types(workspace):
    ids = workspace.ids
    assert resolve(workspace.root, workspace.uris()["xreq"]).path == (
        f"evidence/index/{ids['evidence']}.json"
    )
    assert resolve(workspace.root, workspace.uris()["episode"]).path == (
        f"evidence/index/{workspace.episode_evidence}.json"
    )
    assert resolve(workspace.root, workspace.uris()["opponent"]).path == (
        f"kb/hubs/{workspace.opponent_hub}.md"
    )
    assert resolve(workspace.root, "ar://wiki").path == "kb/index.md"
    assert resolve(workspace.root, workspace.uris()["skill-proposal"]).path == (
        f"skills-proposed/{SKILL}/SKILL.md"
    )


def test_opponent_falls_back_to_an_opponent_claim_then_the_model(workspace):
    root = workspace.root
    claim = new_id("claim")
    write_doc(
        root / "kb" / "claims" / f"{claim}.md",
        {"id": claim, "kind": "opponent", "opponent": "zed"},
        "x",
    )
    model = root / "kb" / "opponents" / "kim" / "STRATEGY.md"
    model.parent.mkdir(parents=True)
    model.write_text("model\n")
    gitops.commit_all(root, "opponents")
    assert resolve(root, "ar://opponent/zed").path == f"kb/claims/{claim}.md"
    assert resolve(root, "ar://opponent/kim").path == "kb/opponents/kim/STRATEGY.md"
    with pytest.raises(ResolveError):
        resolve(root, "ar://opponent/nobody")


def test_journal_decision_anchor(workspace):
    uri = f"ar://journal/{workspace.ids['journal']}"
    first = resolve(workspace.root, f"{uri}#d1")
    assert first.content == b"Keep the farm plan (v2).\nBecause last hits rose."
    assert first.path.startswith("journal/")
    assert resolve(workspace.root, f"{uri}#d2", rev=workspace.old).content == (
        b"Drop the tower dive (v1)."
    )
    with pytest.raises(ResolveError):
        resolve(workspace.root, f"{uri}#d3")  # "## Notes" items are not decisions


def test_lesson_anchor(workspace):
    lesson = workspace.ids["lesson"]
    for uri in (f"ar://lesson#{lesson}", f"ar://lesson/{lesson}#{lesson}"):
        resolved = resolve(workspace.root, uri)
        assert resolved.path == f"lessons/{lesson}.md" and b"lesson v2" in resolved.content
    with pytest.raises(ResolveError):
        resolve(workspace.root, f"ar://lesson/{lesson}#{new_id('lesson')}")
    with pytest.raises(ResolveError):
        resolve(workspace.root, f"ar://lesson#{new_id('lesson')}")


def test_archived_claim(workspace):
    root, claim = workspace.root, workspace.ids["claim"]
    (root / "kb" / "archive").mkdir()
    (root / "kb" / "claims" / f"{claim}.md").rename(root / "kb" / "archive" / f"{claim}.md")
    gitops.commit_all(root, "archive")
    assert resolve(root, f"ar://claim/{claim}").path == f"kb/archive/{claim}.md"
    assert resolve(root, f"ar://claim/{claim}", rev=workspace.old).path == f"kb/claims/{claim}.md"


@pytest.mark.parametrize(
    "uri",
    [
        "ar://widget/W-1",
        "ar://xreq/xreq_unknown",
        "ar://skill-proposal/../etc",
        "ar://doctrine-proposal/a/b",
        "ar://wiki/H-01K6Z3V8QX0000000000000000",
        "ar://replay/3:1",  # a game type, but no game was given
    ],
)
def test_unresolvable(workspace, uri):
    with pytest.raises(ResolveError):
        resolve(workspace.root, uri)


@pytest.fixture
def fakegame():
    game = load_game(AUTORESEARCH_DIR, "fakegame")
    assert "replay" in game.reference_types
    return game


def test_game_declared_type_runs_the_game_resolver(workspace, fakegame):
    resolved = resolve(workspace.root, "ar://replay/3:1", rev=workspace.old, game=fakegame)
    payload = json.loads(resolved.content)
    assert payload["seed"] == 3 and payload["tick"] == 1
    assert payload["rev"] == workspace.old and resolved.rev == workspace.old
    assert resolved.path == "replay/3:1"


def test_game_resolver_failure_is_a_resolve_error(workspace, fakegame):
    with pytest.raises(ResolveError):
        resolve(workspace.root, "ar://replay/not-a-segment", game=fakegame)


def test_game_cannot_shadow_a_core_type(workspace, fakegame):
    game = replace(fakegame, reference_types={"claim": "games/fakegame/tools/fake_ref.py"})
    resolved = resolve(workspace.root, f"ar://claim/{workspace.ids['claim']}", game=game)
    assert b"claim v2" in resolved.content


def test_minimal_canonical_still_resolves(tmp_path):
    handle = Canonical(tmp_path)
    handle.seed()
    assert b"Focus on last hits" in resolve(tmp_path, "ar://plan").content
