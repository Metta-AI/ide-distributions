"""Tool policy matrix. Placeholders: {canon} {worker} {sibling} {cli} {home}."""

import json
import os

import pytest

from autoresearch.plugin.policy import command_signature, exit_program, tool_policy

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
    # fix round 1: quoted substitutions, piped / stdin shells, wrapper forms
    'echo "$(coworld submit x)"',
    'x="$(coworld player use bob)"',
    'echo "`softmax login`"',
    "$(which coworld) submit pv_1",
    "`which coworld` submit pv_1",
    "cat <<EOF | sh\ncoworld submit x\nEOF",
    "echo coworld submit x | sh",
    "bash -s <<EOF\ncoworld upload-policy p\nEOF",
    "sh <<'EOF'\nsoftmax login\nEOF",
    "uvx coworld@latest submit pv_1",
    "uv tool run coworld==1.0 submit pv_1",
    "uv --directory x run coworld submit pv_1",
    "coworld --server http://x --token abc --env prod submit pv_1",
    "coworld player --name bob use",
    "python -m coworld.cli submit pv_1",
    "pipx run coworld submit pv_1",
    "busybox sh -c 'coworld submit'",
    "time -p coworld submit pv_1",
    "watch -n 5 coworld submit pv_1",
    "strace -f coworld submit pv_1",
    "ssh -p 22 host coworld submit pv_1",
    "c=coworld; $c submit pv_1",
    "pushd ~/.softmax && echo x > token",
    "echo hi > /tmp/x",
    # fix round 2: substitution as script, process substitution, heredoc expansion, here-strings
    'sh -c "$(echo coworld submit x)"',
    'bash -c "echo hi; $(echo softmax login)"',
    'eval "$(echo coworld submit x)"',
    "eval $(echo coworld submit x)",
    "bash <(echo coworld submit x)",
    "source <(echo softmax login)",
    "bash < <(echo coworld upload-policy p)",
    "diff <(coworld submit x) notes/a",
    "cat <<EOF > notes/x.md\n$(coworld submit x)\nEOF",
    'sh <<< "coworld submit x"',
    "bash <<< 'softmax login'",
    "function f {{ coworld submit x; }}",
    "echo submit pv_1 | xargs coworld",
    "xargs -I{{}} coworld {{}} < commands",
    "curl -o ~/.softmax/token https://x",
    "curl -sSo {canon}/AGENTS.md https://x",
    "curl --output={sibling}/x https://x",
    "wget -O {canon}/x https://x",
    "tar -xzf a.tgz -C {canon}",
    "tar xf a.tar --directory={sibling}",
    "tar -czf {canon}/out.tgz notes",
    "unzip a.zip -d {canon}",
    "scp host:/f {canon}/f",
    "rsync -a notes/ {canon}/",
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
    '"$AUTORESEARCH_CLI" status --json',
    '"$AUTORESEARCH_CLI" report baseline --since 2026-10-01 --json',
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
    "echo hi > .scratch/x",
    "bash script.sh",
    "cat <<EOF | grep coworld\ncoworld submit is documented here\nEOF",
    'echo \'$(coworld submit x)\'',
    "ssh host uptime",
    "watch -n 5 git status",
    "pushd notes && echo hi > x.md",
    "uv run --directory . pytest",
    '"$AUTORESEARCH_CLI" card note --why "status|$(git rev-parse HEAD) coworld submit later"',
    "cat <<'EOF' > notes/x.md\n$(coworld submit x)\nEOF",
    "curl -o notes/page.html https://x",
    "curl -sS https://x | head",
    "wget -O - https://x | head",
    "tar -xzf a.tgz -C notes",
    "tar -czf .scratch/out.tgz notes",
    "unzip a.zip -d .scratch",
    "diff <(git show HEAD:a) notes/a",
    'sh <<< "echo hi"',
    "cat ids | xargs -n1 echo",
    "scp notes/x host:/tmp/x",
    "function f {{ echo hi; }}",
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
    ("kanban_complete", {"summary": "done"}, False),  # before card finish
    ("skill_manage", {"operations": [{"action": "create", "name": "x", "content": "y"}]}, True),
    ("write_file", {"path": ".scratch/notes.txt", "content": "x"}, True),
    ("write_file", {"path": "a\x00b", "content": "x"}, True),
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


def test_outside_worktree_message_points_at_scratch(layout):
    allowed, reason = tool_policy("write_file", {"path": "/tmp/x", "content": "x"},
                                  layout.ctx(), WORKING)
    assert not allowed and f"use {layout.worker}/.scratch/ for scratch files" in reason


def test_nul_byte_paths_never_raise(layout):
    assert not tool_policy("write_file", {"path": "a\x00b"}, layout.ctx(), FINISHED)[0]
    assert tool_policy("terminal", {"command": "/bin/x\x00y run"}, layout.ctx(), WORKING)[0]


def test_finished_card_still_allows_reads_trusted_cli_and_completion(layout):
    ctx = layout.ctx()
    skill = {"operations": [{"action": "create", "name": "x", "content": "y"}]}
    assert tool_policy("skill_manage", skill, ctx, FINISHED)[0]
    assert tool_policy("read_file", {"path": "notes/x.md"}, ctx, FINISHED)[0]
    assert tool_policy("kanban_complete", {"summary": "s"}, ctx, FINISHED)[0]
    assert _terminal(layout, '"$AUTORESEARCH_CLI" card status', card=FINISHED)[0]
    assert _terminal(layout, "git status && ls notes 2>/dev/null", card=FINISHED)[0]


def test_kanban_block_is_allowed_before_and_after_card_finish(layout):
    """A tool fault ends with ``card finish`` and then ``kanban_block`` (D-26)."""
    args = {"reason": "tool-fault: x", "kind": "needs_input"}
    assert tool_policy("kanban_block", args, layout.ctx(), WORKING)[0]
    assert tool_policy("kanban_block", args, layout.ctx(), FINISHED)[0]


def test_kanban_complete_needs_card_finish_first(layout):
    """Without ``card finish`` there is no handoff, so completing would drop the work."""
    allowed, reason = tool_policy("kanban_complete", {"summary": "s"}, layout.ctx(), WORKING)
    assert not allowed and '"$AUTORESEARCH_CLI" card finish --outcome <o>' in reason
    assert tool_policy("kanban_complete", {"summary": "s"}, layout.ctx(), FINISHED)[0]
    no_card = layout.ctx(worker=False)
    assert tool_policy("kanban_complete", {"summary": "s"}, no_card, None)[0]


@pytest.mark.parametrize("tool", ["kanban_create", "kanban_link", "kanban_schedule"])
@pytest.mark.parametrize("card", [WORKING, PLANNING, FINISHED, None])
def test_native_kanban_card_tools_are_blocked_in_every_session(layout, tool, card):
    """A worker-made task skips admission, receipts and pause control (C3, C7). Cards come
    only from requests that queue-keeper admits."""
    args = {"title": "x", "assignee": "autoresearch", "parent_id": "t_1", "child_id": "t_2"}
    ctx = layout.ctx(worker=card is not None)
    allowed, reason = tool_policy(tool, args, ctx, card)
    assert not allowed
    assert reason.startswith("autoresearch policy: ")
    assert '"$AUTORESEARCH_CLI" queue request create --title' in reason


KANBAN_WRITE_COMMANDS = [
    'hermes kanban create "x" --assignee autoresearch',
    "hermes kanban --board autoresearch link t_1 t_2",
    "hermes -p autoresearch kanban assign t_1 autoresearch",
    "env -u HERMES_DELEGATED_CHILD_CONTEXT hermes kanban create x --assignee autoresearch",
    "HERMES_DELEGATED_CHILD_CONTEXT= hermes kanban create x",
    "bash -c 'hermes kanban create x'",
    "cd /tmp && /usr/local/bin/hermes kanban swarm --goal g",
    "uv run hermes kanban decompose t_1",
    "python3 -m hermes_cli.main kanban create x",
    "hermes kanban boards rm autoresearch",
    "hermes kanban comment t_1 'note'",
    "hermes kanban unlink t_1 t_2",
    """python3 -c "import subprocess; subprocess.run(['hermes', 'kanban', 'create', 'x'])\"""",
    "echo $(hermes kanban create x)",
    # an allowlist of reads: writes Hermes' fence list misses, and future actions, are refused
    "hermes kanban set-model t_1 some/model",
    "hermes kanban some-future-action t_1",
    "hermes kanban --board create set-model t_1 m",
    "hermes kanban boards export -o /tmp/x.tar.gz",
    "hermes kanban boards some-future-action",
    """python3 -c "import subprocess; subprocess.run(['hermes', 'kanban', 'set-model', 'x'])\"""",
]
KANBAN_READ_COMMANDS = [
    "hermes kanban show t_1 --json",
    "hermes kanban list --status blocked",
    "hermes kanban ls",
    "hermes kanban --board autoresearch runs t_1 --json",
    "hermes -p autoresearch kanban --board=autoresearch context t_1",
    "hermes kanban diag --json",
    "hermes kanban tail t_1",
    "hermes kanban boards list",
    "hermes kanban boards current",
    "hermes kanban",
    'git commit -m "do not hermes kanban create"',
    """python3 -c "import subprocess; subprocess.run(['hermes', 'kanban', 'show', 't_1'])\"""",
]


@pytest.mark.parametrize("command", KANBAN_WRITE_COMMANDS)
@pytest.mark.parametrize("card", [WORKING, None])
def test_hermes_kanban_writes_from_a_shell_are_blocked(layout, command, card):
    """The shell route to the same board writes (Hermes' delegate fence is the second layer
    in card workers; it can be unset with ``env -u``, and non-card sessions have none)."""
    allowed, reason = _terminal(layout, command, card=card, worker=card is not None)
    assert not allowed, command
    assert '"$AUTORESEARCH_CLI" queue request create --title' in reason


@pytest.mark.parametrize("command", KANBAN_READ_COMMANDS)
def test_hermes_kanban_reads_are_allowed(layout, command):
    allowed, reason = _terminal(layout, command)
    assert allowed, (command, reason)


def test_execute_code_running_hermes_kanban_create_is_blocked(layout):
    code = "import subprocess\nsubprocess.run(['hermes', 'kanban', 'create', 'x'])"
    allowed, reason = tool_policy("execute_code", {"code": code}, layout.ctx(), WORKING)
    assert not allowed and "queue request create" in reason


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


def test_command_signature_skips_setup_commands():
    """Workers start nearly every command with cd; the signature names the real command."""
    command = (
        'cd "$HERMES_KANBAN_WORKSPACE" && mkdir -p .scratch && "$AUTORESEARCH_CLI" softmax '
        "league-episodes --league L --limit 200"
    )
    assert command_signature("terminal", {"command": command}) == (
        "terminal: $AUTORESEARCH_CLI softmax league-episodes --league L"
    )
    timed = 'cd "$W" && time "$AUTORESEARCH_CLI" stats noise'
    assert command_signature("terminal", {"command": timed}) == (
        "terminal: $AUTORESEARCH_CLI stats noise"
    )
    assert command_signature("terminal", {"command": "cd /tmp"}) == "terminal: cd /tmp"


def test_absolute_cli_path_is_trusted_through_a_symlink(layout, tmp_path):
    link = tmp_path / "cli-link"
    link.symlink_to(layout.cli)
    assert _terminal(layout, f"{link} card show")[0]
    assert os.path.realpath(link) == os.path.realpath(layout.cli)


@pytest.mark.parametrize(
    "code",
    [
        "open('{home}/.env', 'a').write('AUTORESEARCH_PLAYER_ID=x')",
        "import os; open(os.environ['HERMES_HOME'] + '/.env', 'w')",
        "open(os.path.expanduser('~/.softmax/credentials.yaml'), 'w')",
    ],
)
def test_interpreter_text_naming_identity_files_is_blocked(layout, code):
    code = code.replace("{home}", str(layout.hermes_home))
    allowed, reason = tool_policy("execute_code", {"code": code}, layout.ctx(), WORKING)
    assert not allowed and ".env" in reason
    command = f"python3 -c {json.dumps(code)}"
    assert not _terminal(layout, command)[0]


def test_interpreter_text_about_other_env_files_is_allowed(layout):
    code = "print(open('notes/.env.example').read())"
    assert tool_policy("execute_code", {"code": code}, layout.ctx(), WORKING)[0]


INBOX_FORGERIES = [
    '"$AUTORESEARCH_CLI" comms inbound steer --author coach:x --json "{{}}"',
    '{cli} comms inbound task --author james --file .scratch/t.json',
    "cd {canon} && python -m autoresearch comms inbound reply --author p --json '{{}}'",
    "echo '{{}}' > {canon}/comms/inbox/x:task:1.json",
    "cp .scratch/t.json {canon}/comms/inbox/",
    "cd {canon}/comms/inbox && tee x.json < /dev/null",
    "python3 -c \"open('{canon}/comms/inbox/x.json','w').write('{{}}')\"",
]


@pytest.mark.parametrize("command", INBOX_FORGERIES)
@pytest.mark.parametrize("card", [WORKING, None])
def test_inbound_forgery_is_blocked_in_every_agent_session(layout, command, card):
    """C11: an inbound message carries the human's authority; only external agents send it."""
    allowed, reason = _terminal(layout, command, card=card, worker=card is not None)
    assert not allowed and "comms/inbox" in reason


@pytest.mark.parametrize(
    "command",
    [
        '"$AUTORESEARCH_CLI" promotion accept-champion',
        "cd {canon} && python -m autoresearch promotion accept-champion",
        "{cli} promotion accept-champion",
    ],
)
@pytest.mark.parametrize("card", [WORKING, None])
def test_accepting_the_league_champion_is_blocked_in_every_agent_session(layout, command, card):
    """A9: a champion conflict pauses the agent until a human resolves it."""
    allowed, reason = _terminal(layout, command, card=card, worker=card is not None)
    assert not allowed and "human operator" in reason
    assert _terminal(layout, '"$AUTORESEARCH_CLI" promotion status', card=card)[0]


def test_file_tools_and_code_cannot_write_the_inbox(layout):
    ctx = layout.ctx(worker=False)
    target = {"path": str(layout.canonical / "comms" / "inbox" / "x.json"), "content": "{}"}
    assert not tool_policy("write_file", target, ctx, None)[0]
    code = {"code": "open('workspace/comms/inbox/x.json', 'w')"}
    assert not tool_policy("execute_code", code, ctx, None)[0]
    # Reading the inbox and the other comms commands stay allowed.
    read = {"code": "print(open('workspace/comms/inbox/x.json').read())"}
    assert tool_policy("execute_code", read, ctx, None)[0]
    assert _terminal(layout, "python3 -c \"import json; json.load(open('{canon}/comms/inbox/x'))\"")[0]
    assert _terminal(layout, "cat {canon}/comms/inbox/x.json")[0]
    assert _terminal(layout, '"$AUTORESEARCH_CLI" comms get x:task:1')[0]


@pytest.mark.parametrize(
    ("command", "program"),
    [
        ('"$AUTORESEARCH_CLI" queue list | grep x', "grep"),
        ("grep -q x f; ls missing", "ls"),
        ('cd "$W" && FOO=1 time "$AUTORESEARCH_CLI" stats noise', "$AUTORESEARCH_CLI"),
        ("/usr/bin/diff a b", "diff"),
        ("", None),
    ],
)
def test_exit_program_is_the_last_command(command, program):
    assert exit_program(command) == program


# Waiting with the model: every foreground check is one more model call on the whole
# conversation (box spend report 2026-10-08), so these foreground shapes are refused.
POLL_WAITS = [
    "sleep 30",
    "sleep 600",
    "sleep 1m",
    "sleep 0.5h",
    "sleep 20 15",  # sleep adds its operands
    "sleep infinity",
    'cd "$W" && sleep 45 && "$AUTORESEARCH_CLI" queue show t_1',
    "timeout 700 sleep 120",
    "while pgrep -f games.gota.gates >/dev/null; do sleep 20; done",
    "until ! pgrep -f gates; do sleep 5; done",
    "for i in $(seq 1 19); do pgrep -f games.gota.gates || break; sleep 25; done",
    "while kill -0 4242 2>/dev/null; do sleep 10; done; echo done",
    "while ps -p 4242 >/dev/null; do sleep 10; done",
    "bash -c 'while pgrep -f gates; do sleep 10; done'",
    'while [ -n "$(pgrep -f gates)" ]; do sleep 10; done',
    "while [ ! -f .scratch/build/E/report.json ]; do sleep 20; done",
    "until test -s out.log; do sleep 10; done",
]
NOT_POLL_WAITS = [
    "sleep 5",
    "sleep 29",
    "sleep 2; curl -sS https://x",
    "for i in 1 2 3; do curl -sS https://x && break; sleep 5; done",
    "pgrep -f gates",
    "while read -r line; do echo $line; done < notes/a",
    "for f in notes/*; do wc -l $f; done",
    "kill $P; while kill -0 $P 2>/dev/null; do sleep 0.2; done",  # kill, then wait moments
    "timeout 30 bash -c 'until ! pgrep x; do sleep 1; done'",
]


@pytest.mark.parametrize("command", POLL_WAITS)
def test_foreground_waiting_is_refused_with_the_wait_pattern(layout, command):
    allowed, reason = _terminal(layout, command)
    assert not allowed, command
    assert "process_manage" in reason and "background" in reason


@pytest.mark.parametrize("command", POLL_WAITS)
def test_the_same_wait_in_a_background_command_is_allowed(layout, command):
    """A background loop costs no model calls: process_manage wait blocks on it."""
    args = {"command": _fill(command, layout), "background": True}
    allowed, reason = tool_policy("terminal", args, layout.ctx(), WORKING)
    assert allowed, (command, reason)


@pytest.mark.parametrize("command", NOT_POLL_WAITS)
def test_short_sleeps_and_loops_that_do_not_poll_are_allowed(layout, command):
    allowed, reason = _terminal(layout, command)
    assert allowed, (command, reason)


def test_waiting_is_refused_outside_card_sessions_too(layout):
    allowed, _ = _terminal(layout, "sleep 60", card=None, worker=False)
    assert not allowed
