"""Tool policy: the precise guard in front of every model tool call. STDLIB ONLY.

`tool_policy` decides one call. It blocks:

- player switching: `coworld player use`, `softmax login|logout`, any write under `~/.softmax`,
  and `python -c`/`execute_code` text that names the profile `.env` or `.softmax`;
- raw platform mutations: `coworld submit|upload-policy|xp-request create` (C6: only the trusted
  softmax tool behind `"$AUTORESEARCH_CLI"` may mutate the platform);
- force pushes, ref deletions and pushes to `main`/`master` from model commands;
- model writes outside the worker worktree (C2; scratch files go to `<worktree>/.scratch/`),
  every model write after `card finish` (C3), and `memory` writes outside planning cards;
- the native `kanban_complete` on a card before `card finish` ran (C3): a completed run with no
  handoff record leaves its work unmerged;
- Kanban board writes in every agent session (C3, C7): the native `kanban_create`,
  `kanban_link` and `kanban_schedule` (Hermes v0.21.6+), and `hermes kanban <action>` from a shell or code for every action except a
  short allowlist of reads (``KANBAN_READ_ACTIONS``), so new Hermes actions are refused by
  default. A task a model
  creates with an assignee is dispatched with no admission, no receipt and no pause control.
  Cards come only from `queue request create` requests that queue-keeper admits. Hermes'
  delegate fence (``HERMES_DELEGATED_CHILD_CONTEXT`` on every worker subprocess) is a second
  layer in card workers; it is cooperative (``env -u`` removes it) and absent in non-card
  sessions, so the policy does not rely on it;
- waiting with the model in a foreground `terminal` call: `sleep` of 30 seconds or more, and
  a `while`/`until`/`for` loop that sleeps 5 seconds or more at a time and checks a process
  or a file (`pgrep`, `pidof`, `ps`, `kill -0`, `test`, `[`). Shorter sleeps (`kill $p; while
  kill -0 $p; do sleep 0.2; done`) end in moments and pass. Each such check costs one more model call on the whole conversation
  (the box spend report of 2026-10-08: 15+ polling loops in one $5 build run). The refusal
  names the pattern: start the work with `background: true`, then one `process_manage`
  `wait`. The same command with `background: true` is allowed: it costs no model calls;
- forging inbound messages (C11: inbound carries the human's full authority): the trusted
  `comms inbound` command in any agent session, any write into the canonical workspace's
  `comms/inbox/` (every session, cards or not), and `python -c`/`execute_code` text that names
  `comms/inbox` and looks like a write (``CODE_WRITE_HINT``; reads pass). Only external
  agents write the inbox (Runtime 9.2). The real boundary is the
  OS user: run external writers as their own user and set `AUTORESEARCH_INBOX_WRITERS`, so
  queue-keeper adopts only files they own (`comms.inbound.adopt_direct_writes`);
- resolving a champion conflict (A9: the pause holds until a human resolves it): the
  operator command `promotion accept-champion` in any agent session (also the approvals deny
  globs `*promotion accept-champion[ *]`; the command itself refuses in a card context);
- clearing a commit provenance pause (C1, ``runtime.provenance``): any terminal command or
  code that contains `accept-commits` (approvals deny globs `*runtime accept-commits[ *]`
  too). The command itself refuses in a card context and in any agent session: the plugin
  sets ``AUTORESEARCH_AGENT_SESSION`` in the agent process on load
  (``adapters.register``), its terminals and code inherit it, and the CLI checks it, so a
  command word assembled from variables is refused at run time;
- the provenance journal and key (``runtime.provenance``): any tool call (terminal, file
  tools, code, ...) whose arguments name `.git/autoresearch`, `provenance.key`,
  `provenance.jsonl`, the module (`runtime.provenance`, `runtime import ... provenance`) or
  ``AUTORESEARCH_AGENT_SESSION`` (``PROVENANCE_TEXT``). The OS cannot hide the key from
  workers (same user, and `card finish` writes the journal); this refusal is policy;
- canonical writes from interpreter code (C1, D-27; best effort): `python -c`, a Python
  script read from stdin (`python3 - <<EOF`, a here-string, a pipe) and `execute_code` text
  that names the canonical workspace (its path, not under `.worktrees/`, or
  `AUTORESEARCH_WORKSPACE` / `HERMES_WRITE_SAFE_ROOT`), `.supervisor/ledger` or
  `canonical.lock`, and writes it by a line-based heuristic (``_Guard.writes_canonical``): a
  line with a write-shaped call (``CANONICAL_WRITE_SHAPES``: `open` with a write mode,
  `write_text`/`write_bytes`, `os.replace/rename/remove/unlink`, `shutil` copy/move/rmtree)
  that names canonical, a git write subcommand aimed elsewhere than the worktree
  (``GIT_WRITE_SHAPES``, ``GIT_ELSEWHERE``), or `flock`/`lockf` with `canonical.lock`.
  Reads, and writes to the worktree, pass; the refusal says to read the worktree copy. On 2026-10-09 a card worker committed on canonical `main` from a
  `python3 - <<'PYEOF'` script after reasoning that `git commit` would be refused.
  queue-keeper's provenance check detects such a commit after the fact, whatever wrote it.

Tool names and argument keys are those of Hermes v0.21.5 (source tag v2026.9.24):
(`tools/terminal_tool.py`
`terminal{command, workdir}`, `tools/file_tools.py` `write_file{path}` and
`patch{path | mode="patch", patch}`, `tools/memory_tool.py` `memory`,
`tools/code_execution_tool.py` `execute_code{code}`). Any other tool is allowed, including
`skill_manage` (it writes to `skills.create_dir`, the staging area queue-keeper sweeps, C9).

Shell commands are tokenized with `shlex` and every simple command is inspected, including
ones behind `&&`, `;`, `|`, subshells, `bash -c`, `eval`, `xargs`, `find -exec`, `watch`,
`function` bodies, env prefixes and launcher wrappers (`uv run`, `uvx`, `python -m`, `sudo`,
`strace`, ...). The bodies of `$(...)`, backticks (also inside double quotes and inside
unquoted here-documents) and `<(...)`/`>(...)` are walked as commands. When a substitution's
output becomes a script (`sh -c "$(...)"`, `eval $(...)`, `bash <(...)`, `source <(...)`) or
a command word (`$(which coworld) submit`), its body text is checked for blocked phrases. A
shell reading its script from stdin (`... | sh`, `sh <<EOF`, `sh <<< "..."`, `bash -s`) has
its here-document and here-string text walked, and when piped the raw text is checked.
`ssh host <cmd>` is checked for blocked phrases. Writes are found in redirections, file tools
and the targets of common writers (`tee`, `sed -i`, `cp`/`mv`/`rm`/..., `curl -o`,
`wget -O`, `tar -C`/`-f`, `unzip -d`, `scp`/`rsync` destinations, git write subcommands).

Residual risk. This is a best-effort parser, not a shell: it stops a model reaching for a
blocked command, not an adversary. Known unhandled forms:
- a script file written (inside the worktree, which is allowed) and then executed;
- positional-parameter and array tricks (`set -- coworld submit; "$@"`), indirect expansion,
  variables set in an earlier tool call or exported from a sourced file;
- aliases, shell functions defined earlier, renamed or copied binaries;
- Python (or any language) code that calls coworld through a library or a constructed
  string, beyond the literal-phrase check on `python -c` and `execute_code`;
- Kanban board writes that never name `hermes kanban`: code importing
  `hermes_cli.kanban_db`, or opening the board DB with `sqlite3` (in card workers Hermes'
  DB-layer fence still stops the import route unless the code first removes
  `HERMES_DELEGATED_CHILD_CONTEXT`; nothing stops raw sqlite), and a command word taken from a
  variable set by a substitution (`h=$(which hermes); "$h" kanban create ...`);
- interpreter writes in general: a `python -c`, `execute_code`, `node -e`, `perl -e`, ... body
  can write any file the user can. Only a literal check stops `python -c`, stdin Python and
  `execute_code` text that names the identity files (the profile `.env` with `HERMES_HOME` or
  its path, or `.softmax`) or the canonical workspace with a write; a constructed path, a
  script file run by name, and other interpreters pass. The softmax tool therefore never takes identity
  from the `.env` (it only refuses when the `.env` differs from its spawn-time identity).
  The same holds for `comms/inbox`: a constructed path passes the literal check, which is why
  inbox adoption also checks the file owner when `AUTORESEARCH_INBOX_WRITERS` is set.
The other layers are the approvals.deny globs in config.yaml (coarse, matched by Hermes
before execution) and the softmax tool's `whoami` identity check immediately before every
platform mutation (C6), which a raw `coworld` call cannot pass through.

Allowed calls return `(True, "")`, or `(True, "unparsed ...")` when a shell command could
not be tokenized and was allowed anyway; the adapter logs that note.
"""

import os
import re
import shlex
from pathlib import Path

from autoresearch.runctx import RunContext

ALLOW = (True, "")
PREFIX = "autoresearch policy: "

# Hermes write tools whose targets are file paths.
FILE_WRITE_TOOLS = {"write_file", "patch"}

TRUSTED_CLI_WORDS = {"$AUTORESEARCH_CLI", "${AUTORESEARCH_CLI}"}
DEVICE_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
PROTECTED_REFS = {"main", "master", "refs/heads/main", "refs/heads/master"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
COMMAND_STARTERS = ";&|(\n{"
# Placeholders that replace substitutions before tokenizing (their bodies are walked apart).
SUBSTITUTION_WORD = "__AR_SUBSTITUTION__"
PROCESS_WORD = "__AR_PROCESS_SUBSTITUTION__"
# Text before a substitution whose output is then run as a script.
EXECUTES_OUTPUT = re.compile(r"(?:\beval|\b(?:sh|bash|zsh|dash|ksh)\s+-\w*c)\s*[\"']?$")
SOURCE_COMMANDS = {"source", "."}
MAX_NESTING = 5

# Text that, found anywhere in a command we could not parse (or in code), means a raw
# platform mutation or a player switch. Separators allow Python list syntax too.
_SEP = r"[\s'\",\[\]]+"
BLOCKED_TEXT = re.compile(
    rf"\bcoworld{_SEP}(submit|upload-policy|player{_SEP}use|xp-request{_SEP}create)\b"
    rf"|\bsoftmax{_SEP}log(in|out)\b"
)
# `hermes kanban <action>` from a shell or code: only these read-only actions pass, so any
# write action (including ones Hermes' own fence list misses, such as `set-model`) and any
# action a newer Hermes adds is refused by default. Hermes v0.21.5 hermes_cli/kanban_parser.py.
# Board `export` is left out: it writes an archive file.
KANBAN_READ_ACTIONS = frozenset({
    "list", "ls", "show", "tail", "watch", "stats", "log", "runs", "context", "assignees",
    "diagnostics", "diag", "attachments", "notify-list",
})
KANBAN_BOARD_READ_ACTIONS = frozenset({"list", "ls", "show", "current"})
KANBAN_OPTIONS_WITH_VALUE = {"--board"}  # the kanban parser's only option before the action
HERMES_PROGRAMS = {"hermes", "hermes_cli"}  # `python -m hermes_cli.main` unwraps to hermes_cli
# Text form, for code and unparsed commands (best effort, like BLOCKED_TEXT): `hermes`, up to
# three words, `kanban`; the rest of the text is split into words and judged like a command.
KANBAN_TEXT = re.compile(rf"\bhermes(?:_cli\S*)?{_SEP}(?:[^\s'\",\[\]]+{_SEP}){{0,3}}?kanban\b")

# Words skipped at the start of a simple command (shell keywords and no-option prefixes).
PREFIX_WORDS = {
    "!", "{", "}", "then", "do", "else", "elif", "if", "while", "until", "exec",
    "command", "builtin", "nohup", "setsid", "doas", "busybox",
}
# Wrappers: name -> options that take a value. The wrapped command follows the options.
OPTION_WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-U", "-D", "-r", "-t"},
    "nice": {"-n"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a"},
    "env": {"-u", "-C", "-S"},
    "time": {"-o", "-f", "--output", "--format"},
    "strace": {"-o", "-e", "-p", "-s", "-u", "-E", "-P", "-a", "-b", "-I", "-O", "-S", "-X"},
    "ltrace": {"-o", "-e", "-p", "-s", "-u", "-E", "-a", "-n"},
    "uvx": {"--from", "--with", "--python", "-p", "--index", "--index-url"},
}
RUN_WRAPPER_VALUE_OPTIONS = {"--spec", "--python", "-e", "--env"}
WATCH_VALUE_OPTIONS = {"-n", "--interval", "-d", "--differences"}
SSH_VALUE_OPTIONS = {
    "-p", "-i", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-E", "-b", "-c", "-e", "-m", "-O",
    "-Q", "-S", "-W", "-w", "-B",
}
UV_GLOBAL_VALUE_OPTIONS = {
    "--directory", "--project", "--config-file", "--cache-dir", "--python", "-p", "--color",
    "--python-preference", "--index-url",
}
UV_RUN_VALUE_OPTIONS = {
    "--with", "--with-requirements", "--python", "-p", "--project", "--directory", "--package",
    "--extra", "--group", "--env-file", "--index", "--index-url", "--extra-index-url", "--only-group",
}
PYTHON_VALUE_OPTIONS = {"-X", "-W", "-Q"}
GIT_VALUE_OPTIONS = {"-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
GIT_WRITE_SUBCOMMANDS = {
    "commit", "add", "rm", "mv", "reset", "checkout", "switch", "restore", "merge", "rebase",
    "cherry-pick", "revert", "stash", "apply", "am", "clean", "pull",
}
# File writers whose every operand is a write target.
ALL_OPERAND_WRITERS = {"rm", "rmdir", "touch", "mkdir", "truncate", "shred", "unlink", "tee"}
DESTINATION_WRITERS = {"cp", "install", "ln", "rsync", "scp"}
SCP_VALUE_OPTIONS = {"-P", "-i", "-o", "-F", "-l", "-S", "-c", "-J"}
SECRET_WORD = re.compile(
    r"(?i)(token|key|secret|passw|bearer|authorization|credential)"
)
SIGNATURE_WORDS = 5
SIGNATURE_MAX = 120
# Setup commands skipped when choosing the command that names a terminal call.
SIGNATURE_SETUP_PROGRAMS = {
    "cd", "pushd", "popd", "mkdir", "export", "set", "source", ".", "true", "umask",
}
# Wrapper words dropped from the front of the signature.
SIGNATURE_PREFIX_WORDS = {"time"}
# Hermes tools/patch_parser.py _OP_MARKERS (matched at line start).
V4A_PATH = re.compile(
    r"^\*\*\*\s*(?:Update|Add|Delete|Move)\s+File:\s*(.+?)\s*$", re.MULTILINE
)
HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(?!=)")
VERSION_SUFFIX = re.compile(r"[@=<>!~\[].*$")
RAW_MUTATION = (
    "this command contains a raw platform mutation or player switch; "
    'use the "$AUTORESEARCH_CLI" softmax commands.'
)
IDENTITY_FILES = (
    "code that names the profile .env or ~/.softmax is blocked: they hold the agent's "
    "identity (C6)."
)
NOT_FINISHED = (
    'run "$AUTORESEARCH_CLI" card finish --outcome <o> first: it records the handoff; '
    "kanban_complete without it leaves this card's work unmerged."
)
CARD_REQUEST = (
    "Kanban cards are created and linked only by the runtime (queue-keeper admits each card "
    "request; C3, C7). To ask for a card, write a request: "
    '"$AUTORESEARCH_CLI" queue request create --title "<title>" --mode <mode> '
    '--why "<reason>" --ref <ar://ref>. It is admitted after this card\'s branch merges.'
)
POLL_WAIT = (
    "do not wait with sleep or a polling loop in a foreground command: every check is one "
    "more model call on the whole conversation. Start long work with terminal "
    '(background: true) and then call process_manage once (action "wait", its session_id, '
    'timeout 1800); call wait again only when it returns status "timeout". To wait for a '
    "process you did not start, run the same loop with background: true and wait on that. "
    'See the queue skill, "Long commands".'
)
SLEEP_LIMIT_SECONDS = 30
LOOP_WORDS = {"while", "until", "for"}
LOOP_SLEEP_SECONDS = 5  # a loop sleeping less than this per round finishes soon
WAIT_PROBES = {"pgrep", "pidof", "ps", "kill -0", "test", "[", "[["}
SLEEP_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
SLEEP_OPERAND = re.compile(r"(\d+(?:\.\d*)?|\.\d+)([smhd]?)")
# kanban_schedule (new in Hermes v0.21.6) ends the run and parks the card in `scheduled`; nothing in
# our runtime unblocks it, so the card would sit with its work unmerged. Refusing a tool Hermes
# v0.21.5 lacks is harmless.
KANBAN_CARD_TOOLS = {"kanban_create", "kanban_link", "kanban_schedule"}
FINISHED = "this card is finished (`card finish` ran); no further writes."
SOFTMAX_WRITE = "writes under ~/.softmax switch the player identity."
INBOX_WRITE = (
    "the inbox (comms/inbox/) is written only by external agents: an inbound message carries "
    "the human's authority (C11). To tell the human something, stage an outbound message with "
    '"$AUTORESEARCH_CLI" comms stage; to read an inbound message use comms get <id>.'
)
ACCEPT_CHAMPION = (
    "`promotion accept-champion` is for a human operator: a champion conflict pauses the agent "
    "until a person resolves it (A9). Record the conflict in the journal; the runtime already "
    "told the user."
)
ACCEPT_COMMITS = (
    "`runtime accept-commits` is for a human operator: a commit the runtime did not make "
    "pauses the agent until a person checks it (C1). Do not work around this (D-27)."
)
_D27 = (
    "Do not work around a refusal to get the refused effect (D-27): no other interpreter, "
    "script, path or identity. Record it in your journal and end the card the way your skill "
    'says; if nothing else applies, stage a help_request and finish blocked.'
)
CANONICAL_WRITE = (
    "code that writes the canonical workspace, its ledger or canonical.lock is blocked: only "
    "the runtime writes canonical (C1). If you only meant to read, read the same file in your "
    "worktree copy. Your own changes go in your worktree; canonical bookkeeping goes through "
    f'"$AUTORESEARCH_CLI". {_D27}'
)
PROVENANCE_FILES = (
    "the runtime's commit provenance journal, its key and the provenance module are for the "
    f"runtime only (C1). {_D27}"
)
INBOX_DIR_TEXT = "comms/inbox"
# Interpreter code naming the inbox is refused only when it also looks like a write; a read
# (`print(open(".../comms/inbox/x.json").read())`) passes. Literal and best effort, like the
# identity-file check.
CODE_WRITE_HINT = re.compile(
    r"""['"][rwax]?[wax+][bt]?['"]|write|rename|replace|symlink|link|copy|move|touch|mkdir"""
    r"""|unlink|remove|rmtree|chmod|truncate|dump"""
)


# Canonical names in code (besides its path): the ledger, the canonical lock, and the env
# vars that hold the canonical path. Interpreter code naming one of them is refused when it
# also looks like a write, a lock or a git write. Literal and best effort.
CANONICAL_TEXTS = (
    ".supervisor/ledger", "canonical.lock", "AUTORESEARCH_WORKSPACE", "HERMES_WRITE_SAFE_ROOT",
)
_GIT_WRITE_WORDS = (
    r"commit|commit-tree|update-ref|push|add|rm|mv|reset|merge|rebase|cherry-pick|revert"
    r"|checkout|switch|restore|stash|apply|am|clean|pull|tag|branch"
)
CANONICAL_WRITE_SHAPES = re.compile(
    r"""\bopen\([^)]*?['"][rbt]*[wax+][rwabxt+]*['"]"""  # open(path, "w" / "a" / "x" / "r+")
    r"|\bwrite_(?:text|bytes)\s*\("
    r"|\bos\.(?:replace|rename|renames|remove|unlink|rmdir|removedirs|makedirs|mkdir|symlink"
    r"|link|truncate|chmod)\s*\("
    r"|\bshutil\.(?:copy\w*|move|rmtree)\s*\("
    r"|\.(?:unlink|rename|touch|mkdir|rmdir|symlink_to|hardlink_to)\s*\("
    r"|\b(?:flock|lockf)\b"
)
GIT_WRITE_SHAPES = re.compile(
    rf"""['"](?:{_GIT_WRITE_WORDS})['"]"""  # a git write subcommand as an argv element
    rf"|\bgit\b[^\n'\"]*?\s(?:{_GIT_WRITE_WORDS})\b"  # "git -C x commit ..." in a string
)
# Code that points git somewhere other than the current directory (the worktree).
GIT_ELSEWHERE = re.compile(r"""['"]-C['"]|\s-C\s|\bcwd\s*=|\bchdir\b|--git-dir|--work-tree""")
LOCK_CALLS = re.compile(r"\b(?:flock|lockf)\b")
ACCEPT_COMMITS_TEXT = re.compile(r"accept-commits")
PROVENANCE_TEXT = re.compile(
    r"\.git/autoresearch|provenance\.(?:key|jsonl)|runtime\.provenance\b"
    r"|runtime\s+import\s[^\n]*\bprovenance\b|AUTORESEARCH_AGENT_SESSION"
)


def _strings(value) -> list[str]:
    """Every string inside a tool call's arguments."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in _strings(item)]
    return []


def _code_writes_inbox(code: str) -> bool:
    return INBOX_DIR_TEXT in code and bool(CODE_WRITE_HINT.search(code))


class _Blocked(Exception):
    """Raised inside the shell walk to stop at the first blocked simple command."""


def _block(reason: str) -> tuple[bool, str]:
    return False, PREFIX + reason


def _refuse_text(text: str) -> None:
    """Raise `_Blocked` when text we cannot parse as commands (a script, code, an unparsed
    line) names a raw platform mutation, a player switch or a Kanban board write."""
    if _text_writes_kanban(text):
        raise _Blocked(CARD_REQUEST)
    if BLOCKED_TEXT.search(text):
        raise _Blocked(RAW_MUTATION)


def tool_policy(tool_name: str, args: dict, ctx: RunContext, card: dict | None) -> tuple[bool, str]:
    """Decide one tool call: `(allowed, reason)`. `card` is `runtime/cards/<task>.json` or None."""
    args = args if isinstance(args, dict) else {}
    guard = _Guard(ctx, card)
    texts = _strings(args)
    if any(PROVENANCE_TEXT.search(text) for text in texts):
        return _block(PROVENANCE_FILES)
    if tool_name in ("terminal", "execute_code") and any(
        ACCEPT_COMMITS_TEXT.search(text) for text in texts
    ):
        return _block(ACCEPT_COMMITS)
    try:
        if tool_name == "terminal":
            command = args.get("command")
            if not isinstance(command, str):
                return ALLOW
            workdir = args.get("workdir")
            base = Path(workdir) if isinstance(workdir, str) and workdir else guard.base
            return guard.shell(command, base, foreground=args.get("background") is not True)
        if tool_name in FILE_WRITE_TOOLS:
            for target in _file_tool_targets(args):
                guard.check_write(target, guard.base)
            return ALLOW
        if tool_name == "memory":
            return guard.memory()
        if tool_name in KANBAN_CARD_TOOLS:
            return _block(CARD_REQUEST)
        if tool_name == "kanban_complete" and card is not None and not guard.finished:
            return _block(NOT_FINISHED)
        if tool_name == "execute_code":
            code = args.get("code")
            if isinstance(code, str) and _text_writes_kanban(code):
                return _block(CARD_REQUEST)
            if isinstance(code, str) and BLOCKED_TEXT.search(code):
                return _block(
                    "code that runs a raw platform mutation or player switch is blocked; "
                    'use the "$AUTORESEARCH_CLI" softmax commands.'
                )
            if isinstance(code, str) and guard.names_identity_files(code):
                return _block(IDENTITY_FILES)
            if isinstance(code, str):
                guard.check_code(code)
            return ALLOW
    except _Blocked as blocked:
        return _block(str(blocked))
    except (ValueError, OSError) as error:  # e.g. a NUL byte in an executable path
        return True, f"unparsed call allowed ({error})"
    return ALLOW


def _file_tool_targets(args: dict) -> list[str]:
    targets = []
    path = args.get("path")
    if isinstance(path, str) and path:
        targets.append(path)
    patch = args.get("patch")
    if isinstance(patch, str):
        for header in V4A_PATH.findall(patch):
            targets.extend(part.strip() for part in header.split("->"))
    return targets


class _Guard:
    def __init__(self, ctx: RunContext, card: dict | None):
        self.ctx = ctx
        self.card = card if isinstance(card, dict) else None
        self.finished = bool(self.card and self.card.get("finished"))
        self.worker = _real(ctx.worker) if ctx.worker else None
        self.base = ctx.worker or Path.cwd()
        self.home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
        self.softmax_dir = _real(self.home / ".softmax")
        self.inbox_dir = _real(ctx.canonical / INBOX_DIR_TEXT)
        self.trusted = _trusted_cli_words(ctx)
        self.foreground = False  # set per terminal call: waiting rules apply only then

    def names_canonical(self, text: str) -> bool:
        """Literal check: ``text`` names a canonical text (``CANONICAL_TEXTS``), or the
        canonical path anywhere but at the start of a path under its ``.worktrees/`` (where
        card worktrees live, so a worker naming its own worktree passes)."""
        if any(name in text for name in CANONICAL_TEXTS):
            return True
        for root in {str(self.ctx.canonical), str(_real(self.ctx.canonical))}:
            for match in re.finditer(re.escape(root.rstrip("/")) + r"(?![\w.-])", text):
                if not text.startswith("/.worktrees/", match.end()):
                    return True
        return False

    def check_code(self, code: str) -> None:
        """Raise `_Blocked` for interpreter code (`python -c`, stdin Python, `execute_code`)
        that names a blocked command or a protected file and looks like it acts on it."""
        _refuse_text(code)
        if ACCEPT_COMMITS_TEXT.search(code):
            raise _Blocked(ACCEPT_COMMITS)
        if PROVENANCE_TEXT.search(code):
            raise _Blocked(PROVENANCE_FILES)
        if self.names_identity_files(code):
            raise _Blocked(IDENTITY_FILES)
        if _code_writes_inbox(code):
            raise _Blocked(INBOX_WRITE)
        if self.writes_canonical(code):
            raise _Blocked(CANONICAL_WRITE)

    def writes_canonical(self, code: str) -> bool:
        """Literal, line-based heuristic (module docstring), for code that names canonical: a
        line with a write-shaped call that names canonical; a git write on a line that does
        not name the worker's worktree, when the code points git elsewhere (``-C``, ``cwd=``,
        ...) or the line names canonical; a lock call when the code names canonical.lock. A
        write through a variable that holds a canonical path, on a line that does not name
        it, passes (and the provenance check catches a commit)."""
        if not self.names_canonical(code):
            return False
        if "canonical.lock" in code and LOCK_CALLS.search(code):
            return True
        elsewhere = bool(GIT_ELSEWHERE.search(code))
        for line in code.splitlines():
            if CANONICAL_WRITE_SHAPES.search(line) and self.names_canonical(line):
                return True
            git_write = GIT_WRITE_SHAPES.search(line) and not self._names_worker(line)
            if git_write and (elsewhere or self.names_canonical(line)):
                return True
        return False

    def _names_worker(self, text: str) -> bool:
        return self.worker is not None and (
            str(self.ctx.worker) in text or str(self.worker) in text
        )

    def names_identity_files(self, text: str) -> bool:
        """Literal check (module docstring): ``text`` names ``.softmax``, or ``.env`` together
        with ``HERMES_HOME`` or the profile home's path."""
        if ".softmax" in text:
            return True
        if ".env" not in text:
            return False
        homes = {str(self.ctx.hermes_home), str(_real(self.ctx.hermes_home)), "HERMES_HOME"}
        return any(home in text for home in homes)

    # --- non-shell tools ---

    def memory(self) -> tuple[bool, str]:
        if self.card is None:
            return ALLOW
        if self.card.get("mode") != "planning":
            return _block("memory writes are allowed only on planning cards.")
        if self.finished:
            return _block("this card is finished (`card finish` ran); no further writes.")
        return ALLOW

    # --- write targets ---

    def check_write(self, target: str, cwd: Path) -> str | None:
        """Raise `_Blocked` for a forbidden write; return a note when the target is unresolved."""
        expanded = _expand(target, self.home)
        if expanded in DEVICE_TARGETS or expanded.startswith("/dev/fd/"):
            return None
        resolved = None
        if "$" not in expanded and "\x00" not in expanded:
            try:
                resolved = _real(cwd / expanded)
            except (ValueError, OSError):
                resolved = None
        if resolved is None:
            if ".softmax" in expanded:
                raise _Blocked(SOFTMAX_WRITE)
            if INBOX_DIR_TEXT in expanded:
                raise _Blocked(INBOX_WRITE)
            if self.finished:
                raise _Blocked(FINISHED)
            return f"unresolved write target {target!r}"
        if _within(resolved, self.softmax_dir):
            raise _Blocked(SOFTMAX_WRITE)
        if _within(resolved, self.inbox_dir):
            raise _Blocked(INBOX_WRITE)
        if self.finished:
            raise _Blocked(FINISHED)
        if self.worker is not None and not _within(resolved, self.worker):
            raise _Blocked(
                f"model writes must stay inside your worktree {self.ctx.worker}; "
                f"{target!r} resolves to {resolved}; use {self.ctx.worker}/.scratch/ for "
                'scratch files. Canonical bookkeeping goes through "$AUTORESEARCH_CLI".'
            )
        return None

    # --- shell ---

    def shell(self, command: str, cwd: Path, foreground: bool = False) -> tuple[bool, str]:
        notes: list[str] = []
        self.foreground = foreground
        try:
            self._walk_shell(command, cwd, 0, notes)
        except _Blocked as blocked:
            return _block(str(blocked))
        return (True, "; ".join(notes)) if notes else ALLOW

    def _walk_shell(self, command: str, cwd: Path, depth: int, notes: list[str]) -> set[str]:
        """Check one command line; return the programs it runs (``WAIT_PROBES`` names
        included), substitutions too, for the waiting rule."""
        if depth > MAX_NESTING:
            self._unparsed(command, "nested too deeply", notes)
            return set()
        text, bodies = _split_heredocs(command)
        for body, quoted in bodies:
            if not quoted:  # an unquoted here-document expands its substitutions
                for substitution in _substitutions(body):
                    self._walk_shell(substitution.body, cwd, depth + 1, notes)
        substitutions = _substitutions(text)
        process_bodies = []
        line_programs: set[str] = set()
        for substitution in substitutions:
            line_programs |= self._walk_shell(substitution.body, cwd, depth + 1, notes)
            if substitution.process:
                process_bodies.append(substitution.body)
                continue
            before = text[:substitution.start].rstrip()
            # `sh -c "$(...)"`, `eval $(...)`: the output is a script we cannot see.
            if EXECUTES_OUTPUT.search(before):
                _refuse_text(substitution.body)
            # `$(which coworld) submit`: the output is the command word.
            if not substitution.quoted and before[-1:] in ("", *COMMAND_STARTERS):
                words = substitution.body.split()
                head = words[-1].strip("'\"") if words else ""
                _refuse_text(f"{head} {text[substitution.end:]}")
        try:
            tokens = _tokenize(_replace_substitutions(text, substitutions))
        except ValueError as error:
            self._unparsed(command, str(error), notes)
            return line_programs
        line = _Line(raw=command, bodies=[body for body, _ in bodies])
        line.process_bodies = process_bodies
        line.programs = line_programs
        looped = False
        for words, targets, piped, herestrings in _simple_commands(tokens):
            looped = looped or bool(words and words[0] in LOOP_WORDS)
            if words and words[0] in ("cd", "pushd"):
                cwd = self._cd(words, cwd)
                continue
            if words and all(ASSIGNMENT.match(word) for word in words):
                for word in words:
                    name, value = word.split("=", 1)
                    line.variables[name] = value
                continue
            line.piped, line.herestrings, line.via_xargs = piped, herestrings, False
            for target in targets + self._inspect(words, cwd, depth, notes, line):
                note = self.check_write(target, cwd)
                if note:
                    notes.append(note)
        if (
            self.foreground
            and looped
            and line.loop_sleep >= LOOP_SLEEP_SECONDS
            and line.programs & WAIT_PROBES
        ):
            raise _Blocked(POLL_WAIT)
        return line.programs

    def _unparsed(self, command: str, why: str, notes: list[str]) -> None:
        _refuse_text(command)
        notes.append(f"unparsed shell allowed ({why})")

    def _cd(self, words: list[str], cwd: Path) -> Path:
        operands = [word for word in words[1:] if not word.startswith("-")]
        if not operands:
            return self.home if words[0] == "cd" else cwd
        target = _expand(operands[0], self.home)
        return cwd if "$" in target or target == "-" else cwd / target

    def _inspect(
        self, words: list[str], cwd: Path, depth: int, notes: list[str], line: "_Line",
    ) -> list[str]:
        """Check one simple command; return the write targets it names."""
        core = self._unwrap(words, cwd, depth, notes, line)
        if not core:
            return []
        first = core[0]
        trusted = first in self.trusted or (
            os.path.isabs(first) and _real(Path(first)) in self.trusted
        )
        if trusted or os.path.basename(first) == "autoresearch":
            # `python -m autoresearch ...` unwraps to `autoresearch ...` too.
            subcommand = [word for word in core[1:] if not word.startswith("-")][:2]
            if subcommand == ["comms", "inbound"]:
                raise _Blocked(INBOX_WRITE)
            if subcommand == ["promotion", "accept-champion"]:
                raise _Blocked(ACCEPT_CHAMPION)
            if subcommand == ["runtime", "accept-commits"]:
                raise _Blocked(ACCEPT_COMMITS)
            if trusted:
                return []
        if first.startswith("$"):  # `c=coworld; $c submit`
            first = _expand_variables(first, line.variables)
        # `coworld@latest`, `coworld==1.0` (uvx / uv tool run package specs)
        name = VERSION_SUFFIX.sub("", os.path.basename(first))
        operands = [word for word in core[1:] if not word.startswith("-")]
        # `[`/`[[` before VERSION_SUFFIX (which strips a leading `[`)
        line.programs.add(
            first if first in ("[", "[[")
            else "kill -0" if name == "kill" and "-0" in core[1:] else name
        )
        if name == "sleep":
            seconds = _sleep_seconds(operands)
            line.loop_sleep = max(line.loop_sleep, seconds)
            if self.foreground and seconds >= SLEEP_LIMIT_SECONDS:
                raise _Blocked(POLL_WAIT)
        if line.via_xargs and name in {"coworld", "softmax"}:
            raise _Blocked(
                f"running {name} through xargs is blocked: its subcommand would come from input."
            )
        if name in SOURCE_COMMANDS:
            self._check_stdin_script(line, cwd, depth, notes)
            return []
        if name == "coworld":
            self._check_coworld(operands)
        elif name in HERMES_PROGRAMS:
            _check_hermes(core[1:])
        elif name == "softmax" and {"login", "logout"} & set(operands[:2]):
            raise _Blocked("`softmax login/logout` switches the player identity.")
        elif name == "git":
            return self._check_git(core[1:], cwd)
        elif name == "find":
            return self._find_exec(core, cwd, depth, notes, line)
        return _writer_targets(name, core[1:])

    def _unwrap(
        self, words: list[str], cwd: Path, depth: int, notes: list[str], line: "_Line",
    ) -> list[str]:
        """Strip env assignments and wrappers. Nested scripts (`bash -c`, `eval`, `watch`,
        stdin shells) are walked here and yield an empty core."""
        index = 0
        while index < len(words):
            word = words[index]
            name = os.path.basename(word)
            if ASSIGNMENT.match(word) or word in PREFIX_WORDS:
                index += 1
            elif word == "function":  # `function f { ...; }`: skip the keyword and the name
                index += 2
            elif name == "timeout":
                index = _skip_options(words, index + 1, {"-s", "-k", "--signal", "--kill-after"})
                index += 1  # the duration
            elif name in OPTION_WRAPPERS:
                if name == "env" and "-S" in words[index + 1:]:
                    split = words.index("-S", index + 1)
                    if split + 1 < len(words):
                        self._walk_shell(" ".join(words[split + 1:]), cwd, depth + 1, notes)
                    return []
                line.via_xargs = line.via_xargs or name == "xargs"
                index = _skip_options(words, index + 1, OPTION_WRAPPERS[name])
            elif name == "uv":
                verb = _skip_options(words, index + 1, UV_GLOBAL_VALUE_OPTIONS)
                if words[verb:verb + 1] == ["run"]:
                    index = _skip_options(words, verb + 1, UV_RUN_VALUE_OPTIONS)
                elif words[verb:verb + 2] == ["tool", "run"]:
                    index = _skip_options(words, verb + 2, OPTION_WRAPPERS["uvx"])
                else:
                    return words[index:]
            elif name in {"poetry", "pdm", "hatch", "pipx"} and words[index + 1:index + 2] == [
                "run"
            ]:
                index = _skip_options(words, index + 2, RUN_WRAPPER_VALUE_OPTIONS)
            elif name == "watch":
                start = _skip_options(words, index + 1, WATCH_VALUE_OPTIONS)
                self._walk_shell(" ".join(words[start:]), cwd, depth + 1, notes)
                return []
            elif name == "ssh":
                host = _skip_options(words, index + 1, SSH_VALUE_OPTIONS)
                _refuse_text(" ".join(words[host + 1:]))
                return []
            elif re.fullmatch(r"python[0-9.]*", name):
                rest = words[index + 1:]
                if "-m" in rest:
                    module_at = index + 1 + rest.index("-m") + 1
                    if module_at >= len(words):
                        return []
                    # `python -m coworld.cli submit` runs the coworld package.
                    return [words[module_at].split(".")[0], *words[module_at + 1:]]
                if "-c" in rest:
                    code_at = index + 1 + rest.index("-c") + 1
                    self.check_code(words[code_at] if code_at < len(words) else "")
                elif _reads_script_from_stdin(rest):
                    # `python3 - <<'EOF'`, `python3 <<< "..."`, `... | python3`: the script
                    # is the here-document, here-string or (piped) the whole line.
                    for text in line.bodies + line.herestrings:
                        self.check_code(text)
                    if line.piped:
                        self.check_code(line.raw)
                return words[index:]
            elif name in SHELLS:
                mode, script_at = _shell_mode(words, index + 1)
                if mode == "c" and script_at < len(words):
                    self._walk_script(words[script_at], cwd, depth, notes)
                elif mode == "stdin" or (mode == "file" and PROCESS_WORD in words[index + 1:]):
                    self._check_stdin_script(line, cwd, depth, notes)
                return []
            elif name == "eval":
                self._walk_script(" ".join(words[index + 1:]), cwd, depth, notes)
                return []
            else:
                return words[index:]
        return []

    def _walk_script(self, script: str, cwd: Path, depth: int, notes: list[str]) -> None:
        """A `-c` / `eval` script. A substitution anywhere in it produces script text we
        cannot see, so its body is checked for blocked phrases."""
        for substitution in _substitutions(script):
            _refuse_text(substitution.body)
        self._walk_shell(script, cwd, depth + 1, notes)

    def _check_stdin_script(self, line: "_Line", cwd: Path, depth: int, notes: list[str]):
        """A shell (or `source`) whose script comes from stdin, a here-document, a
        here-string, a pipe or a process substitution."""
        for text in line.bodies + line.herestrings:
            self._walk_shell(text, cwd, depth + 1, notes)
        for body in line.process_bodies:
            _refuse_text(body)
        if line.piped:
            _refuse_text(line.raw)

    def _check_coworld(self, operands: list[str]) -> None:
        # Every operand is scanned: global options with values (`--server x`, `--env prod`)
        # can push the subcommand anywhere. This can over-block an option value that happens
        # to be "submit"; that is the safe direction.
        if (
            "submit" in operands
            or "upload-policy" in operands
            or _in_order(operands, "xp-request", "create")
        ):
            raise _Blocked(
                "raw `coworld submit/upload-policy/xp-request create` is blocked; use the "
                '"$AUTORESEARCH_CLI" softmax commands, which check the player identity first.'
            )
        if _in_order(operands, "player", "use"):
            raise _Blocked("`coworld player use` switches the player identity.")

    def _check_git(self, args: list[str], cwd: Path) -> list[str]:
        index = 0
        directory = cwd
        while index < len(args) and args[index].startswith("-"):
            option = args[index]
            if option == "-C" and index + 1 < len(args):
                directory = cwd / _expand(args[index + 1], self.home)
                index += 2
            elif option in GIT_VALUE_OPTIONS:
                index += 2
            else:
                index += 1
        if index >= len(args):
            return []
        subcommand, rest = args[index], args[index + 1:]
        if subcommand == "push":
            _check_push(rest)
            return []
        if subcommand in GIT_WRITE_SUBCOMMANDS:
            return [str(directory)]
        return []

    def _find_exec(
        self, core: list[str], cwd: Path, depth: int, notes: list[str], line: "_Line",
    ) -> list[str]:
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in core:
                start = core.index(flag) + 1
                inner = [w for w in core[start:] if w not in {";", "+", "{}"}]
                return self._inspect(inner, cwd, depth, notes, line)
        return []


def _reads_script_from_stdin(args: list[str]) -> bool:
    """Python's arguments after the interpreter name: no script file (only options), or the
    script ``-`` (stdin)."""
    index = 0
    while index < len(args):
        word = args[index]
        if word == "-":
            return True
        if not word.startswith("-"):
            return False  # a script file: run by name, not inspected (module docstring)
        index += 2 if word in PYTHON_VALUE_OPTIONS else 1
    return True


def _check_hermes(args: list[str]) -> None:
    """`hermes ... kanban <action>` that is not a known read (``KANBAN_READ_ACTIONS``)."""
    if "kanban" in args and _kanban_writes(args[args.index("kanban") + 1:]):
        raise _Blocked(CARD_REQUEST)


def _kanban_writes(words: list[str]) -> bool:
    """Judge the words after `kanban`: the action (and after `boards` the board action) must
    be a read. No action at all (`hermes kanban`, `--help`) prints help and passes."""
    actions, index = [], 0
    while index < len(words) and len(actions) < 2:
        word = words[index]
        if word in KANBAN_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if word and not word.startswith("-"):
            actions.append(word)
        index += 1
    if not actions:
        return False
    if actions[0] == "boards":
        return len(actions) > 1 and actions[1] not in KANBAN_BOARD_READ_ACTIONS
    return actions[0] not in KANBAN_READ_ACTIONS


def _text_writes_kanban(text: str) -> bool:
    for match in KANBAN_TEXT.finditer(text):
        rest = text[match.end():].split("\n", 1)[0]
        if _kanban_writes([word for word in re.split(_SEP + r"|[();|&]", rest) if word]):
            return True
    return False


class _Line:
    """Per command line state for the shell walk."""

    def __init__(self, raw: str, bodies: list[str]):
        self.raw = raw
        self.bodies = bodies
        self.piped = False  # the current simple command reads from a pipe
        self.herestrings: list[str] = []  # `<<< text` of the current simple command
        self.via_xargs = False  # the current simple command runs under xargs
        self.process_bodies: list[str] = []  # bodies of `<(...)` / `>(...)` on the line
        self.variables: dict[str, str] = {}
        self.programs: set[str] = set()  # programs run on the line, for the waiting rule
        self.loop_sleep = 0.0  # the longest single sleep on the line, for the waiting rule


def _sleep_seconds(operands: list[str]) -> float:
    """Seconds a `sleep` waits: the sum of its operands (`30`, `1m`, `0.5h`). `infinity` is
    unbounded; an operand we cannot read (a variable) counts as zero."""
    total = 0.0
    for operand in operands:
        if operand == "infinity":
            return float("inf")
        match = SLEEP_OPERAND.fullmatch(operand)
        if match:
            total += float(match.group(1)) * SLEEP_UNITS[match.group(2)]
    return total


def _check_push(rest: list[str]) -> None:
    operands = []
    index = 0
    while index < len(rest):
        word = rest[index]
        if word in {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}:
            index += 2
            continue
        if word.startswith("--"):
            if word.split("=")[0] in {
                "--force", "--force-with-lease", "--force-if-includes", "--mirror", "--delete",
                "--all", "--prune",
            }:
                raise _Blocked(f"`git push {word}` is blocked: no force pushes or ref deletions.")
        elif word.startswith("-"):
            if "f" in word or "d" in word:
                raise _Blocked(f"`git push {word}` is blocked: no force pushes or ref deletions.")
        else:
            operands.append(word)
        index += 1
    for refspec in operands[1:]:
        if refspec.startswith(("+", ":")):
            raise _Blocked(f"`git push {refspec}` is blocked: no force pushes or ref deletions.")
        destination = refspec.split(":")[-1]
        if destination in PROTECTED_REFS:
            raise _Blocked(
                f"pushing to {destination} is blocked; the runtime merges and pushes main."
            )


def _writer_targets(name: str, args: list[str]) -> list[str]:
    operands = [word for word in args if not word.startswith("-") and word]
    if name in ALL_OPERAND_WRITERS:
        if name == "truncate":
            operands = _operands_skipping(args, {"-s", "--size", "-r", "--reference"})
        return operands
    if name == "mv":
        return operands
    if name in DESTINATION_WRITERS:
        for index, word in enumerate(args):
            if word in {"-t", "--target-directory"} and index + 1 < len(args):
                return [args[index + 1]]
            if word.startswith("--target-directory="):
                return [word.split("=", 1)[1]]
        if name == "scp":
            operands = _operands_skipping(args, SCP_VALUE_OPTIONS)
        remote = name in {"rsync", "scp"} and ":" in operands[-1:][0] if operands else False
        if len(operands) >= 2 and not remote:
            return [operands[-1]]
        return []
    if name == "sed" and any(w == "--in-place" or re.match(r"-i|--in-place=", w) for w in args):
        files = _operands_skipping(args, {"-e", "-f", "--expression", "--file"})
        has_script_option = any(w in {"-e", "-f", "--expression", "--file"} for w in args)
        return files if has_script_option else files[1:]
    if name == "dd":
        return [word[3:] for word in args if word.startswith("of=")]
    if name == "curl":
        return _option_values(args, "o", {"--output"})
    if name == "wget":
        values = _option_values(args, "OP", {"--output-document", "--directory-prefix"})
        return [value for value in values if value != "-"]
    if name == "unzip":
        return _option_values(args, "d", set())
    if name == "tar":
        return _tar_targets(args)
    return []


def _option_values(args: list[str], short: str, long: set[str]) -> list[str]:
    """Values of `-o X`, `-sSo X` (a short cluster ending in the letter), `--output X` and
    `--output=X`."""
    values = []
    for index, word in enumerate(args):
        following = args[index + 1] if index + 1 < len(args) else None
        if word.startswith("--"):
            option, _, value = word.partition("=")
            if option in long:
                values.extend([value] if value else [following] if following else [])
        elif word.startswith("-") and len(word) > 1 and word[-1] in short and following:
            values.append(following)
    return values


def _tar_targets(args: list[str]) -> list[str]:
    """`-C DIR` when extracting; `-f FILE` when creating. Short clusters may omit the dash
    in the first argument (`tar xzf a.tgz`)."""
    if not args:
        return []
    cluster = args[0] if re.fullmatch(r"[A-Za-z]+", args[0]) else ""
    normalized = [f"-{cluster}", *args[1:]] if cluster else args
    letters = "".join(w[1:] for w in normalized if w.startswith("-") and not w.startswith("--"))
    extracting = "x" in letters or "--extract" in normalized or "--get" in normalized
    creating = "c" in letters or "r" in letters or "u" in letters or "--create" in normalized
    targets = []
    if extracting:
        targets += _option_values(normalized, "C", {"--directory"})
    if creating:
        targets += _option_values(normalized, "f", {"--file"})
    return targets


def _operands_skipping(args: list[str], value_options: set[str]) -> list[str]:
    operands, index = [], 0
    while index < len(args):
        word = args[index]
        if word in value_options:
            index += 2
            continue
        if not word.startswith("-") and word:
            operands.append(word)
        index += 1
    return operands


def _skip_options(words: list[str], index: int, value_options: set[str]) -> int:
    while index < len(words) and (words[index].startswith("-") or ASSIGNMENT.match(words[index])):
        index += 2 if words[index] in value_options else 1
    return index


def _shell_mode(words: list[str], index: int) -> tuple[str, int]:
    """`("c", script_index)` for `bash -c 'x'` / `-lc`; `("stdin", 0)` for `sh`, `bash -s`,
    `sh < f`; `("file", 0)` for `bash script.sh`."""
    stdin_flag = False
    while index < len(words) and words[index].startswith("-"):
        flag = words[index]
        if not flag.startswith("--") and "c" in flag:
            return "c", index + 1
        stdin_flag = stdin_flag or (not flag.startswith("--") and "s" in flag)
        index += 1
    if stdin_flag or index >= len(words):
        return "stdin", 0
    return "file", 0


def _in_order(words: list[str], first: str, second: str) -> bool:
    return first in words and second in words[words.index(first) + 1:]


def _expand_variables(word: str, variables: dict[str, str]) -> str:
    for name, value in variables.items():
        word = word.replace("${" + name + "}", value).replace("$" + name, value)
    return os.path.expandvars(word)


# --- tokenizing ---


def _tokenize(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _is_operator(token: str) -> bool:
    return bool(token) and all(char in ";&|<>()\n" for char in token)


def _simple_commands(tokens: list[str]):
    """Yield `(words, redirect_write_targets, piped, herestrings)` for each simple command;
    `piped` is True when the command reads from a `|`."""
    words: list[str] = []
    targets: list[str] = []
    herestrings: list[str] = []
    piped = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not _is_operator(token):
            words.append(token)
            index += 1
            continue
        if ">" in token or "<" in token:
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if words and words[-1].isdigit():
                words.pop()  # a file-descriptor number such as the 2 of 2>
            if token == "<<<" and following is not None and not _is_operator(following):
                herestrings.append(following)
            if ">" in token and following is not None and not _is_operator(following):
                duplicating = token.endswith("&") and (following.isdigit() or following == "-")
                if not duplicating:
                    targets.append(following)
            index += 2 if following is not None and not _is_operator(following) else 1
            continue
        yield words, targets, piped, herestrings
        piped = token in {"|", "|&"}
        words, targets, herestrings = [], [], []
        index += 1
    yield words, targets, piped, herestrings


def _split_heredocs(command: str) -> tuple[str, list[tuple[str, bool]]]:
    """`(command without here-document bodies, [(body, delimiter_quoted)])`. Bodies are data
    unless a shell reads its script from stdin; an unquoted delimiter means the body's
    substitutions run. The walk decides."""
    kept: list[str] = []
    bodies: list[tuple[str, bool]] = []
    pending: list[tuple[str, bool, bool]] = []
    body: list[str] = []
    for line in command.split("\n"):
        if pending:
            delimiter, strip_tabs, quoted = pending[0]
            if (line.lstrip("\t") if strip_tabs else line).strip() == delimiter:
                pending.pop(0)
                bodies.append(("\n".join(body), quoted))
                body = []
            else:
                body.append(line)
            continue
        kept.append(line)
        for match in HEREDOC.finditer(line):
            start = match.start()
            here_string = line[start:start + 3] == "<<<" or line[start - 1:start] == "<"
            if not here_string:
                pending.append((match.group(3), match.group(1) == "-", bool(match.group(2))))
    if body:
        bodies.append(("\n".join(body), pending[0][2]))
    return "\n".join(kept), bodies


class _Substitution:
    def __init__(self, start: int, end: int, body: str, quoted: bool, process: bool):
        self.start, self.end, self.body = start, end, body
        self.quoted = quoted  # inside double quotes: its output is one word of data
        self.process = process  # `<(...)` / `>(...)`: its output is a file name


def _substitutions(text: str) -> list[_Substitution]:
    """Top-level `$(...)`, backtick and `<(...)`/`>(...)` substitutions that the shell would
    run: outside single quotes (process substitutions also outside double quotes)."""
    found = []
    single = double = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and not single:
            index += 2
            continue
        if char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
        elif not single and text.startswith("$(", index):
            end = _closing_paren(text, index + 2)
            found.append(_Substitution(index, end, text[index + 2:end - 1], double, False))
            index = end
            continue
        elif not single and not double and text[index:index + 2] in ("<(", ">("):
            end = _closing_paren(text, index + 2)
            found.append(_Substitution(index, end, text[index + 2:end - 1], False, True))
            index = end
            continue
        elif not single and char == "`":
            close = text.find("`", index + 1)
            end = len(text) if close < 0 else close + 1
            body = text[index + 1:close if close >= 0 else len(text)]
            found.append(_Substitution(index, end, body, double, False))
            index = end
            continue
        index += 1
    return found


def _replace_substitutions(text: str, substitutions: list[_Substitution]) -> str:
    """The text with each unquoted substitution replaced by a placeholder word. Quoted ones
    stay: they sit inside one shell word, and a `-c` / `eval` script needs to keep them."""
    parts, cursor = [], 0
    for substitution in substitutions:
        if substitution.quoted:
            continue
        word = PROCESS_WORD if substitution.process else SUBSTITUTION_WORD
        parts += [text[cursor:substitution.start], word]
        cursor = substitution.end
    parts.append(text[cursor:])
    return "".join(parts)


def _closing_paren(text: str, index: int) -> int:
    """Index just past the `)` closing a `$(` whose body starts at `index`."""
    depth = 1
    quote = ""
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text) + 1


# --- paths ---


def _expand(word: str, home: Path) -> str:
    if word == "~" or word.startswith("~/"):
        word = str(home) + word[1:]
    word = word.replace("${HOME}", str(home)).replace("$HOME", str(home))
    return os.path.expandvars(word)


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _trusted_cli_words(ctx: RunContext) -> set:
    trusted: set = set(TRUSTED_CLI_WORDS)
    for candidate in (os.environ.get("AUTORESEARCH_CLI"), ctx.hermes_home / "bin" / "autoresearch"):
        if candidate:
            trusted.add(str(candidate))
            trusted.add(_real(Path(candidate)))
            trusted.add(str(_real(Path(candidate))))
    return trusted


# --- signatures ---


def _signature_command(commands: list[list[str]]) -> list[str]:
    """The simple command that names a call: the first one that is not setup. Workers prefix
    nearly every command with ``cd "$HERMES_KANBAN_WORKSPACE" &&`` (and often ``mkdir -p``),
    so the first simple command alone would give every call the same signature."""
    for words in commands:
        program = next((w for w in words if not ASSIGNMENT.match(w)), None)
        if program is not None and program not in SIGNATURE_SETUP_PROGRAMS:
            return words
    return commands[0] if commands else []


def _shell_commands(command: str) -> list[list[str]]:
    try:
        return [words for words, *_ in _simple_commands(_tokenize(_split_heredocs(command)[0]))]
    except ValueError:
        return [command.split()]


def exit_program(command: str) -> str | None:
    """The program whose status a shell command line exits with: the last simple command (of
    the last pipeline or list), without env assignments or prefixes such as ``time``. The
    repeated-tool-error signal judges an exit code by it (``grep`` exit 1 is "no match").

    Known limit (accepted for v0): a short-circuiting ``&&``/``||`` list can exit with an
    earlier command's status (``grep -q x f && echo yes`` exits 1 from ``grep``), but this
    returns the last program (``echo``), so such a "no match" counts as an error. Lines with
    ``|`` or ``;`` are right."""
    for words in reversed(_shell_commands(command)):
        program = next(
            (
                w for w in words
                if not ASSIGNMENT.match(w) and w not in PREFIX_WORDS
                and w not in SIGNATURE_PREFIX_WORDS
            ),
            None,
        )
        if program is not None:
            return program if program in TRUSTED_CLI_WORDS else os.path.basename(program)
    return None


def command_signature(tool_name: str, args: dict) -> str:
    """Short, secret-free form of a call for the repeated-tool-error signal."""
    args = args if isinstance(args, dict) else {}
    if tool_name == "terminal" and isinstance(args.get("command"), str):
        first = _signature_command(_shell_commands(args["command"]))
        words = []
        for word in first:
            if not words and (ASSIGNMENT.match(word) or word in SIGNATURE_PREFIX_WORDS):
                continue
            if SECRET_WORD.search(word):
                break
            words.append(word)
            if len(words) == SIGNATURE_WORDS:
                break
        return f"{tool_name}: {' '.join(words)}"[:SIGNATURE_MAX]
    path = args.get("path")
    if tool_name in FILE_WRITE_TOOLS | {"read_file"} and isinstance(path, str):
        return f"{tool_name}: {path}"[:SIGNATURE_MAX]
    return tool_name
