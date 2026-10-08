"""Tool policy matrix. Placeholders: {canon} {worker} {sibling} {cli} {home}."""

import os

import pytest

from autoresearch.plugin.policy import command_signature, tool_policy

WORKING = {"mode": "investigation", "finished": False}
FINISHED = {"mode": "investigation", "finished": True}
PLANNING = {"mode": "planning", "finished": False}


def _fill(value, layout):
    if isinstance(value, str):
        return value.format(
            canon=layout.canonical, worker=layout.worker, sibling=layout.sibling,
            cli=layout.cli, home=layout.home,
        )
    if isinstance(value, dict):
        return {key: _fill(item, layout) for key, item in value.items()}
    return value


def _terminal(layout, command, card=WORKING, **ctx_kwargs):
    args = {"command": _fill(command, layout)}
    return tool_policy("terminal", args, layout.ctx(**ctx_kwargs), card)


BLOCKED_COMMANDS = [
    # player switching
    "coworld player use X",
    "softmax login",
    "softmax logout",
    "sudo softmax login",
    "bash -lc 'softmax login'",
    "python3 -m coworld player use bob",
    "echo $(coworld player use bob)",
    "echo x > ~/.softmax/credentials.json",
    "cp creds $HOME/.softmax/config.json",
    "tee -a {home}/.softmax/token < creds",
    # raw platform mutations, plain and wrapped
    "coworld submit pv_1 league_x",
    "coworld upload-policy ./policy",
    "coworld xp-request create E-1",
    "/opt/bin/coworld submit pv_1",
    "coworld --server http://x submit pv_1",
    "uv run coworld submit pv_1",
    "uv run --with requests coworld upload-policy ./p",
    "uvx coworld submit pv_1",
    "python -m coworld submit pv_1",
    "FOO=1 coworld submit pv_1",
    "env FOO=1 BAR=2 coworld submit pv_1",
    "nohup timeout 5 coworld submit pv_1",
    "bash -c 'coworld submit pv_1'",
    'sh -c "cd /tmp && coworld upload-policy p"',
    "echo ok && coworld submit pv_1",
    "ls; coworld submit pv_1",
    "cat ids | xargs -n 1 coworld submit",
    "true || coworld xp-request create E-1",
    "(coworld submit pv_1)",
    "x=`coworld submit pv_1`",
    'eval "coworld submit pv_1"',
    "echo hi\ncoworld submit pv_1",
    "find . -name p -exec coworld upload-policy {{}} ;",
    '"$AUTORESEARCH_CLI" card show && coworld submit pv_1',
    '"$AUTORESEARCH_CLI" card show; uv run coworld submit pv_1',
    "coworld submit 'unterminated",
    # force pushes and protected refs
    "git push --force origin card/Q-1",
    "git push -f",
    "git push -fu origin card/Q-1",
    "git -C . push --force",
    "git push origin +main",
    "git push origin +card/Q-1",
    "git push --force-with-lease origin card/Q-1",
    "git push --force-with-lease=card/Q-1 origin card/Q-1",
    "git push origin main",
    "git push origin HEAD:main",
    "git push origin :card/Q-2",
    "git push --mirror",
    # model writes outside the worker
    "echo hi > {canon}/AGENTS.md",
    "echo hi >> ../Q-2/notes.md",
    "echo hi > ../../AGENTS.md",
    "tee {sibling}/x < input",
    "sed -i s/a/b/ {canon}/AGENTS.md",
    "sed -i.bak -e s/a/b/ {sibling}/f",
    "mv notes/a.md {canon}/b.md",
    "mv {canon}/AGENTS.md notes/",
    "cp notes/a.md {sibling}/",
    "rm -rf {canon}/.supervisor",
    "touch /tmp/outside",
    "cd {canon} && echo hi > x",
    "git -C {canon} commit -am sneaky",
    'cat > {canon}/x <<EOF\nhello\nEOF',
]


@pytest.mark.parametrize("command", BLOCKED_COMMANDS)
def test_blocked_commands(layout, command):
    allowed, reason = _terminal(layout, command)
    assert not allowed, command
    assert reason.startswith("autoresearch policy:")


ALLOWED_COMMANDS = [
    '"$AUTORESEARCH_CLI" softmax submit E-1',
    '"$AUTORESEARCH_CLI" softmax xp create E-1',
    '"$AUTORESEARCH_CLI" card finish --outcome done --summary "never run coworld player use"',
    "${{AUTORESEARCH_CLI}} kb add notes/x.md",
    "$AUTORESEARCH_CLI card show",
    "{cli} card show",
    "FOO=1 {cli} card show 2>/dev/null",
    'git commit -m "avoid coworld submit"',
    'echo "coworld submit is blocked here"',
    'grep -rn "softmax login" docs || true',
    "coworld --help",
    "coworld player list",
    "softmax whoami",
    "git push origin card/Q-1",
    "git push -u origin HEAD:card/Q-1",
    "git status && git log --oneline -5 | head",
    "git commit -am done",
    "echo hi > notes/x.md",
    "echo hi >> {worker}/notes/x.md",
    "make test 2>&1 | tail -20",
    "ls > /dev/null 2>&1",
    "sed -n 1,5p {canon}/AGENTS.md",
    "sed -i s/a/b/ notes/x.md",
    "cp {canon}/AGENTS.md notes/",
    "cat ~/.softmax/README || true",
    "python -c 'print(1)'",
    'cat > notes/x.md <<EOF\nnever run coworld submit or softmax login\nEOF',
    'echo "unterminated',
    "uv run pytest -q",
]


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_allowed_commands(layout, command):
    allowed, reason = _terminal(layout, command)
    assert allowed, (command, reason)


def test_unparseable_shell_is_allowed_with_a_note(layout):
    allowed, note = _terminal(layout, 'echo "unterminated')
    assert allowed and "unparsed" in note


def test_terminal_workdir_is_the_base_for_relative_targets(layout):
    args = {"command": "echo hi > x", "workdir": str(layout.canonical)}
    assert not tool_policy("terminal", args, layout.ctx(), WORKING)[0]
    args = {"command": "echo hi > x", "workdir": str(layout.worker)}
    assert tool_policy("terminal", args, layout.ctx(), WORKING)[0]


FILE_TOOL_CASES = [
    ("write_file", {"path": "notes/x.md", "content": "x"}, True),
    ("write_file", {"path": "{worker}/notes/x.md", "content": "x"}, True),
    ("write_file", {"path": "{canon}/AGENTS.md", "content": "x"}, False),
    ("write_file", {"path": "{sibling}/x.md", "content": "x"}, False),
    ("write_file", {"path": "{worker}/../Q-2/x.md", "content": "x"}, False),
    ("write_file", {"path": "~/.softmax/config.json", "content": "x"}, False),
    ("patch", {"path": "notes/x.md", "old_string": "a", "new_string": "b"}, True),
    ("patch", {"path": "{canon}/AGENTS.md", "old_string": "a", "new_string": "b"}, False),
    ("patch", {"mode": "patch",
               "patch": "*** Begin Patch\n*** Update File: notes/x.md\n@@\n-a\n+b\n*** End Patch"},
     True),
    ("patch", {"mode": "patch",
               "patch": "*** Begin Patch\n*** Add File: {canon}/x.md\n+b\n*** End Patch"}, False),
    ("patch", {"mode": "patch",
               "patch": "*** Begin Patch\n*** Move File: notes/x.md -> ../Q-2/x.md\n*** End Patch"},
     False),
    ("read_file", {"path": "{canon}/AGENTS.md"}, True),
    ("search_files", {"pattern": "x", "path": "{canon}"}, True),
    ("web_search", {"query": "coworld submit"}, True),
    ("kanban_complete", {"summary": "done"}, True),
    ("skill_manage", {"operations": [{"action": "create", "name": "x", "content": "y"}]}, False),
]


@pytest.mark.parametrize(("tool", "args", "expected"), FILE_TOOL_CASES)
def test_file_tools(layout, tool, args, expected):
    allowed, reason = tool_policy(tool, _fill(args, layout), layout.ctx(), WORKING)
    assert allowed is expected, (tool, args, reason)


def test_symlink_out_of_the_worker_is_outside(layout):
    (layout.worker / "escape").symlink_to(layout.canonical)
    args = {"path": "escape/AGENTS.md", "content": "x"}
    assert not tool_policy("write_file", args, layout.ctx(), WORKING)[0]
    assert not _terminal(layout, "echo x > escape/AGENTS.md")[0]


def test_finished_card_blocks_every_model_write(layout):
    ctx = layout.ctx()
    assert not tool_policy("write_file", {"path": "notes/x.md", "content": "x"}, ctx, FINISHED)[0]
    assert not tool_policy(
        "patch", {"path": "notes/x.md", "old_string": "a", "new_string": "b"}, ctx, FINISHED,
    )[0]
    assert not _terminal(layout, "echo hi > notes/x.md", card=FINISHED)[0]
    assert not _terminal(layout, "git commit -am late", card=FINISHED)[0]
    assert not _terminal(layout, "rm notes/x.md", card=FINISHED)[0]


def test_finished_card_still_allows_reads_trusted_cli_and_completion(layout):
    ctx = layout.ctx()
    assert tool_policy("read_file", {"path": "notes/x.md"}, ctx, FINISHED)[0]
    assert tool_policy("kanban_complete", {"summary": "s"}, ctx, FINISHED)[0]
    assert _terminal(layout, '"$AUTORESEARCH_CLI" card status', card=FINISHED)[0]
    assert _terminal(layout, "git status && ls notes 2>/dev/null", card=FINISHED)[0]


@pytest.mark.parametrize(
    ("card", "expected"),
    [(PLANNING, True), (WORKING, False), ({"mode": "experimentation"}, False), (None, True)],
)
def test_memory_writes_only_on_planning_cards(layout, card, expected):
    args = {"action": "add", "target": "memory", "content": "lesson"}
    allowed, reason = tool_policy("memory", args, layout.ctx(), card)
    assert allowed is expected, reason


def test_memory_blocked_after_a_planning_card_finished(layout):
    card = PLANNING | {"finished": True}
    assert not tool_policy("memory", {"action": "add", "content": "x"}, layout.ctx(), card)[0]


def test_non_card_session_has_only_the_global_blocks(layout):
    ctx = layout.ctx(worker=False)
    assert tool_policy("write_file", {"path": "/tmp/x", "content": "x"}, ctx, None)[0]
    assert tool_policy("memory", {"action": "add", "content": "x"}, ctx, None)[0]
    assert _terminal(layout, "echo hi > {canon}/x", card=None, worker=False)[0]
    assert not _terminal(layout, "coworld submit pv_1", card=None, worker=False)[0]
    assert not _terminal(layout, "softmax login", card=None, worker=False)[0]
    softmax_file = {"path": "~/.softmax/x", "content": "x"}
    assert not tool_policy("write_file", softmax_file, ctx, None)[0]


def test_execute_code_with_a_raw_mutation_is_blocked(layout):
    code = "import subprocess\nsubprocess.run(['coworld', 'submit', 'pv_1'])"
    assert not tool_policy("execute_code", {"code": code}, layout.ctx(), WORKING)[0]
    assert tool_policy("execute_code", {"code": "print(1)"}, layout.ctx(), WORKING)[0]


def test_bad_args_do_not_raise(layout):
    assert tool_policy("terminal", None, layout.ctx(), WORKING)[0]
    assert tool_policy("terminal", {"command": 7}, layout.ctx(), WORKING)[0]
    assert tool_policy("write_file", {"path": None}, layout.ctx(), WORKING)[0]


def test_command_signature_strips_env_and_secrets():
    signature = command_signature(
        "terminal", {"command": "TOKEN=abc FOO=1 curl -s --token sekrit https://x/y"},
    )
    assert "abc" not in signature and "sekrit" not in signature and "TOKEN" not in signature
    assert signature.startswith("terminal: curl")
    assert command_signature("terminal", {"command": "tool --api-key=zzz run"}) == "terminal: tool"
    assert command_signature("write_file", {"path": "notes/x.md"}) == "write_file: notes/x.md"
    assert command_signature("web_search", {"query": "q"}) == "web_search"
    assert len(command_signature("terminal", {"command": "x " * 500})) <= 120
    assert command_signature("terminal", {"command": 'echo "unterminated'}).startswith("terminal")


def test_absolute_cli_path_is_trusted_through_a_symlink(layout, tmp_path):
    link = tmp_path / "cli-link"
    link.symlink_to(layout.cli)
    assert _terminal(layout, f"{link} card show")[0]
    assert os.path.realpath(link) == os.path.realpath(layout.cli)
