"""The orchestrator's approvals.deny list, matched the way Hermes v0.21.6 matches it.

Hermes lowercases each command variant and fnmatches it against every glob; the variants are
the whole command and the command from each executable position onward
(tools/approval_detection.py _deny_command_variants). Splitting on spaces approximates the
executable positions for these plain commands."""

import fnmatch
from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"
DENY = [rule.lower() for rule in yaml.safe_load(CONFIG.read_text())["approvals"]["deny"]]


def denied(command):
    words = command.lower().split()
    variants = [" ".join(words[start:]) for start in range(len(words))]
    return any(fnmatch.fnmatchcase(variant, rule) for variant in variants for rule in DENY)


@pytest.mark.parametrize("config", [CONFIG, CONFIG.parents[1] / "researcher/config.yaml"])
def test_profiles_never_prompt_for_approval(config):
    approvals = yaml.safe_load(config.read_text())["approvals"]
    assert approvals["mode"] == "off"  # Hermes's yolo mode
    for context in ("cron_mode", "single_query_mode", "unattended_mode"):
        assert approvals[context] == "approve"


@pytest.mark.parametrize("command", [
    "coworld submit pol --league l",
    'research softmax exec -- coworld submit pol --league l',
    "softmax login",
    "softmax get-token",
    "coworld player use ply_x",
    "git push --force origin main",
    "git -C repo push --force",
    "git push origin +main",
    "git push -f",
    "echo $SOFTMAX_PLAYER_CREDENTIAL",
])
def test_blocks_identity_raw_submission_force_push_and_credential_reads(command):
    assert denied(command)


@pytest.mark.parametrize("command", [
    "coworld submissions --player ply_x",
    "coworld xp-request create body.json",
    "coworld upload-policy --name p policy.bas",
    "git push origin card/t_1",
    "ls -la games",
    "python3 -c 'print(1)'",
    "softmax status",
])
def test_allows_ordinary_work(command):
    assert not denied(command)
