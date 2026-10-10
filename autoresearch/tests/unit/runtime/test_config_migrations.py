"""C9 configuration policies (Overview C9): every key is applied, tuned values survive."""

import yaml

from autoresearch.runtime import config_migrations as cm

EXPECTED_PASSTHROUGH = [
    "SOFTMAX_PLAYER_CREDENTIAL",
    "AUTORESEARCH_PLAYER_ID",
    "AUTORESEARCH_WORKSPACE",
    "AUTORESEARCH_WORKSPACE_REMOTE",
    "AUTORESEARCH_GAME",
    "AUTORESEARCH_SOFTMAX",
    "AUTORESEARCH_CLI",
    "OPENROUTER_MODEL",
    "AUTORESEARCH_INBOX_WRITERS",
    "AUTORESEARCH_CHAMPION_GRACE_MINUTES",
    "COGAMES_API_URL",
    "AUTORESEARCH_CARD_MODELS",
    "AUTORESEARCH_MAX_CARD_RUNS",
    "AUTORESEARCH_MAX_CARD_USD",
    "AUTORESEARCH_DAILY_BUDGET_USD",
    "AUTORESEARCH_FALLBACK_PRICES",
    "AUTORESEARCH_RELAUNCH_MIN_CREDITS",
    "AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD",
    "AUTORESEARCH_AUTONOMY",
    "AUTORESEARCH_CHAT_DAILY_USD",
    "TMPDIR",
]


class Recorder:
    """A fake ``hermes`` runner: records argv and applies it to the profile config.yaml."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, home):
        self.calls.append(argv)
        path = home / "config.yaml"
        config = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
        node = config
        *parents, last = argv[2].split(".")
        for part in parents:
            node = node.setdefault(part, {})
        if argv[1] == "set":
            value = argv[3]
            node[last] = value if value in ("off", "on") else yaml.safe_load(value)
        else:
            node.pop(last, None)
        path.write_text(yaml.safe_dump(config))


def config_of(home):
    return yaml.safe_load((home / "config.yaml").read_text())


def test_env_passthrough_is_the_exact_c9_list_with_queue_and_model():
    assert cm.ENV_PASSTHROUGH == EXPECTED_PASSTHROUGH


def test_empty_profile_gets_every_row(tmp_path):
    runner = Recorder()
    cm.apply(runner, tmp_path)
    config = config_of(tmp_path)
    assert config["plugins"]["enabled"] == ["autoresearch"]
    assert config["kanban"] == {
        "auto_decompose": False,
        "review_dispatch": False,
        "auto_subscribe_on_create": False,
        "max_in_progress": 2,
    }
    assert config["memory"] == {
        "memory_enabled": True,
        "user_profile_enabled": False,
        "write_approval": False,
    }
    assert config["auxiliary"]["background_review"]["enabled"] is False
    assert config["tool_loop_guardrails"]["hard_stop_enabled"] is True
    assert config["agent"] == {"max_turns": 150, "run_budget_seconds": 5400}
    assert config["terminal"]["timeout"] == 1800  # the longest process_manage wait
    # One tool call may run longer than that wait, or Hermes cuts the wait at 420 s.
    assert config["timeouts"]["tools"] == {"sequential_call": 1900, "concurrent_batch": 1900}
    assert config["approvals"]["mode"] == "off"
    assert config["approvals"]["deny"] == cm.APPROVAL_DENY
    assert config["terminal"]["env_passthrough"] == EXPECTED_PASSTHROUGH
    assert config["skills"]["create_dir"] == str(tmp_path / "local" / "skills-staging")
    assert config["updates"]["check"] is False
    assert config["curator"]["prune_builtins"] is False
    assert "default_assignee" not in config["kanban"]
    assert cm.pending(tmp_path) == []


def test_missing_only_rows_keep_tuned_values(tmp_path):
    tuned = {
        "kanban": {"max_in_progress": 3},
        "tool_loop_guardrails": {"hard_stop_enabled": False},
        "agent": {"max_turns": 40, "run_budget_seconds": 600},
        "terminal": {"timeout": 600},
        "timeouts": {"tools": {"sequential_call": 2400, "concurrent_batch": 0}},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(tuned))
    cm.apply(Recorder(), tmp_path)
    config = config_of(tmp_path)
    assert config["kanban"]["max_in_progress"] == 3
    assert config["tool_loop_guardrails"]["hard_stop_enabled"] is False
    assert config["agent"] == {"max_turns": 40, "run_budget_seconds": 600}
    assert config["terminal"]["timeout"] == 600
    assert config["timeouts"]["tools"] == {"sequential_call": 2400, "concurrent_batch": 0}


def test_always_rows_overwrite_drifted_values(tmp_path):
    drifted = {
        "kanban": {"auto_decompose": True, "review_dispatch": True},
        "memory": {"user_profile_enabled": True},
        "approvals": {"mode": "manual", "deny": []},
        "terminal": {"env_passthrough": ["SOFTMAX_PLAYER_CREDENTIAL"]},
        "updates": {"check": True},
        "curator": {"prune_builtins": True},
        "plugins": {"enabled": []},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(drifted))
    cm.apply(Recorder(), tmp_path)
    config = config_of(tmp_path)
    assert config["kanban"]["auto_decompose"] is False
    assert config["kanban"]["review_dispatch"] is False
    assert config["memory"]["user_profile_enabled"] is False
    assert config["approvals"]["mode"] == "off"
    assert config["terminal"]["env_passthrough"] == EXPECTED_PASSTHROUGH
    assert config["updates"]["check"] is False
    assert config["curator"]["prune_builtins"] is False
    assert config["plugins"]["enabled"] == ["autoresearch"]


def test_default_assignee_is_unset_when_present(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"kanban": {"default_assignee": "x"}}))
    runner = Recorder()
    cm.apply(runner, tmp_path)
    assert ["config", "unset", "kanban.default_assignee"] in runner.calls
    assert "default_assignee" not in config_of(tmp_path)["kanban"]


def test_second_apply_runs_nothing(tmp_path):
    cm.apply(Recorder(), tmp_path)
    runner = Recorder()
    assert cm.apply(runner, tmp_path) == []
    assert runner.calls == []


def test_openrouter_model_env_overrides_the_model(tmp_path):
    """C9: ``OPENROUTER_MODEL`` in the profile .env sets ``model.default`` (workers read the
    model only from config.yaml); without it the shipped or installer-set model stays."""
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "a/b"}}))
    runner = Recorder()
    cm.apply(runner, tmp_path, {"OPENROUTER_MODEL": "z-ai/glm-5.3-flash"})
    assert ["config", "set", "model.default", "z-ai/glm-5.3-flash"] in runner.calls
    assert config_of(tmp_path)["model"]["default"] == "z-ai/glm-5.3-flash"
    assert cm.pending(tmp_path, {"OPENROUTER_MODEL": "z-ai/glm-5.3-flash"}) == []

    cm.apply(Recorder(), tmp_path, {})
    assert config_of(tmp_path)["model"]["default"] == "z-ai/glm-5.3-flash"


def test_earlier_shipped_defaults_are_upgraded_but_tuned_values_kept(tmp_path):
    old = {
        "agent": {"max_turns": 150, "run_budget_seconds": 2700},
        "terminal": {"timeout": 180},
        "timeouts": {"tools": {"sequential_call": 420, "concurrent_batch": 420}},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(old))
    cm.apply(Recorder(), tmp_path)
    config = config_of(tmp_path)
    assert config["agent"]["run_budget_seconds"] == 5400
    assert config["terminal"]["timeout"] == 1800
    assert config["timeouts"]["tools"] == {"sequential_call": 1900, "concurrent_batch": 1900}
    assert cm.pending(tmp_path) == []


def test_the_coach_chat_gets_only_its_toolset_and_no_research_skills(tmp_path):
    cm.apply(Recorder(), tmp_path)
    config = config_of(tmp_path)
    assert config["platform_toolsets"] == {"api_server": ["autoresearch_coach"]}
    assert config["known_plugin_toolsets"] == {"cli": ["autoresearch_coach"]}
    hidden = config["skills"]["platform_disabled"]["api_server"]
    shipped = {path.parent.name for path in cm.SKILLS_DIR.rglob("SKILL.md")}
    assert set(hidden) == shipped and {"queue", "submit", "build-upload"} <= set(hidden)


def test_the_shipped_config_already_has_every_always_row(tmp_path):
    """Bootstrap only adds the home-dependent skills.create_dir to the shipped config.yaml."""
    (tmp_path / "config.yaml").write_text((cm.SKILLS_DIR.parent / "config.yaml").read_text())
    assert [argv[2] for argv in cm.pending(tmp_path)] == ["skills.create_dir"]


def test_the_cli_known_toolsets_keep_other_plugins(tmp_path):
    """M1: Hermes lists every plugin toolset there; bootstrap adds ours and keeps the rest."""
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(
        {"known_plugin_toolsets": {"cli": ["spotify", "google_meet"]}}
    ))
    cm.apply(Recorder(), tmp_path)
    assert config_of(tmp_path)["known_plugin_toolsets"]["cli"] == [
        "spotify", "google_meet", "autoresearch_coach",
    ]
    assert not [argv for argv in cm.pending(tmp_path) if "known_plugin_toolsets" in argv[2]]
