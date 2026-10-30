from __future__ import annotations

import pytest

from research_core.store.actors import Actor


@pytest.mark.parametrize(
    ("text", "kind", "ident"),
    [
        ("session:api_1761690245_0badcafe", "session", "api_1761690245_0badcafe"),
        ("user:u_42", "user", "u_42"),
        ("harness", "harness", "harness"),
    ],
)
def test_parse_and_str_round_trip(text, kind, ident):
    actor = Actor.parse(text)
    assert actor.kind == kind
    assert actor.id == ident
    assert actor.epoch is None
    assert str(actor) == text
    assert Actor.parse(str(actor)) == actor


def test_str_leaves_out_the_epoch():
    actor = Actor(kind="session", id="s1", epoch=3)
    assert str(actor) == "session:s1"
    assert Actor.parse(str(actor)) == Actor(kind="session", id="s1")


@pytest.mark.parametrize(
    "text", ["", "session:", "user:", "harness:x", "robot:1", "session", "Session:abc", " user:1"]
)
def test_parse_rejects_other_shapes(text):
    with pytest.raises(ValueError):
        Actor.parse(text)


def test_constructors():
    assert Actor.harness() == Actor(kind="harness", id="harness")
    assert Actor.session("s1") == Actor(kind="session", id="s1")
    assert Actor.session("s1", epoch=2).epoch == 2
    assert Actor.user("u1") == Actor(kind="user", id="u1")
    assert Actor.harness().is_harness and not Actor.user("u1").is_harness
    assert Actor.user("u1").is_user and not Actor.session("s1").is_user
    assert Actor.session("s1").is_session


def test_actor_is_frozen_and_validated():
    with pytest.raises(ValueError):
        Actor(kind="robot", id="x")
    with pytest.raises(ValueError):
        Actor(kind="user", id="")
    with pytest.raises(ValueError):
        Actor(kind="harness", id="other")
    with pytest.raises(AttributeError):
        Actor.harness().id = "x"  # type: ignore[misc]


def test_from_env_session_from_ide_session_id():
    actor = Actor.from_env({"IDE_SESSION_ID": "api_1_abc", "HERMES_SESSION_ID": "ignored"})
    assert actor == Actor(kind="session", id="api_1_abc")


def test_from_env_session_from_hermes_session_id():
    assert Actor.from_env({"HERMES_SESSION_ID": "h_1"}) == Actor(kind="session", id="h_1")


def test_from_env_epoch_from_ide_lease_epoch():
    actor = Actor.from_env({"IDE_SESSION_ID": "s1", "IDE_LEASE_EPOCH": "4"})
    assert actor.epoch == 4
    assert Actor.from_env({"IDE_SESSION_ID": "s1", "IDE_LEASE_EPOCH": ""}).epoch is None


def test_from_env_ide_actor_wins():
    env = {"IDE_ACTOR": "user:u_9", "IDE_SESSION_ID": "s1"}
    assert Actor.from_env(env) == Actor(kind="user", id="u_9")
    env = {"IDE_ACTOR": "session:s2", "IDE_SESSION_ID": "s1", "IDE_LEASE_EPOCH": "7"}
    assert Actor.from_env(env) == Actor(kind="session", id="s2", epoch=7)
    assert Actor.from_env({"IDE_ACTOR": "harness", "IDE_SESSION_ID": "s1"}) == Actor.harness()


def test_from_env_defaults_to_harness():
    assert Actor.from_env({}) == Actor.harness()
    assert Actor.from_env({"IDE_SESSION_ID": "", "HERMES_SESSION_ID": ""}) == Actor.harness()
