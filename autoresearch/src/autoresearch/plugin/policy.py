"""Tool policy: the precise guard in front of every model tool call. STDLIB ONLY.

`tool_policy` decides one call. It blocks:

- player switching: `coworld player use`, `softmax login|logout`, any write under `~/.softmax`;
- raw platform mutations: `coworld submit|upload-policy|xp-request create` (C6: only the trusted
  softmax tool behind `"$AUTORESEARCH_CLI"` may mutate the platform);
- force pushes, ref deletions and pushes to `main`/`master` from model commands;
- model writes outside the worker worktree (C2), every model write after `card finish`
  (C3), and `memory` writes outside planning cards.

Tool names and argument keys are Hermes v0.21.5's (`tools/terminal_tool.py`
`terminal{command, workdir}`, `tools/file_tools.py` `write_file{path}` and
`patch{path | mode="patch", patch}`, `tools/memory_tool.py` `memory`,
`tools/skill_manager_tool.py` `skill_manage`, `tools/code_execution_tool.py`
`execute_code{code}`). Any other tool is allowed.

Shell commands are tokenized with `shlex` and every simple command is inspected, including
ones behind `&&`, `;`, `|`, subshells, `$(...)`, backticks, `bash -c`, `eval`, `xargs`,
`find -exec`, env prefixes and launcher wrappers (`uv run`, `python -m`, `sudo`, ...).
This is a best-effort parser, not a shell: it guards against a model reaching for a
blocked command, not against an adversary. The approvals.deny globs in config.yaml are
the coarse second layer.

Allowed calls return `(True, "")`, or `(True, "unparsed ...")` when a shell command could
not be tokenized and was allowed anyway; the adapter logs that note.
"""

import itertools
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
MAX_NESTING = 5

# Text that, found anywhere in a command we could not parse (or in code), means a raw
# platform mutation or a player switch. Separators allow Python list syntax too.
_SEP = r"[\s'\",\[\]]+"
BLOCKED_TEXT = re.compile(
    rf"\bcoworld{_SEP}(submit|upload-policy|player{_SEP}use|xp-request{_SEP}create)\b"
    rf"|\bsoftmax{_SEP}log(in|out)\b"
)

# Words skipped at the start of a simple command (shell keywords and no-option prefixes).
PREFIX_WORDS = {
    "!", "{", "}", "then", "do", "else", "elif", "if", "while", "until", "time", "exec",
    "command", "builtin", "nohup", "setsid", "doas",
}
# Wrappers: name -> options that take a value. The wrapped command follows the options.
OPTION_WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-U", "-D", "-r", "-t"},
    "nice": {"-n"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a"},
    "env": {"-u", "-C", "-S"},
    "uvx": {"--from", "--with", "--python", "-p", "--index", "--index-url"},
    "pipx": {"--spec", "--python"},
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
DESTINATION_WRITERS = {"cp", "install", "ln", "rsync"}
SECRET_WORD = re.compile(
    r"(?i)(token|key|secret|passw|bearer|authorization|credential)"
)
SIGNATURE_WORDS = 5
SIGNATURE_MAX = 120
V4A_PATH = re.compile(r"^\*\*\*\s*(?:Update|Add|Delete|Move) File:\s*(.+?)\s*$", re.MULTILINE)
HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


class _Blocked(Exception):
    """Raised inside the shell walk to stop at the first blocked simple command."""


def _block(reason: str) -> tuple[bool, str]:
    return False, PREFIX + reason


def tool_policy(tool_name: str, args: dict, ctx: RunContext, card: dict | None) -> tuple[bool, str]:
    """Decide one tool call: `(allowed, reason)`. `card` is `runtime/cards/<task>.json` or None."""
    args = args if isinstance(args, dict) else {}
    guard = _Guard(ctx, card)
    try:
        if tool_name == "terminal":
            command = args.get("command")
            if not isinstance(command, str):
                return ALLOW
            workdir = args.get("workdir")
            base = Path(workdir) if isinstance(workdir, str) and workdir else guard.base
            return guard.shell(command, base)
        if tool_name in FILE_WRITE_TOOLS:
            for target in _file_tool_targets(args):
                guard.check_write(target, guard.base)
            return ALLOW
        if tool_name == "memory":
            return guard.memory()
        if tool_name == "skill_manage":
            return guard.skill_manage()
        if tool_name == "execute_code":
            code = args.get("code")
            if isinstance(code, str) and BLOCKED_TEXT.search(code):
                return _block(
                    "code that runs a raw platform mutation or player switch is blocked; "
                    'use the "$AUTORESEARCH_CLI" softmax commands.'
                )
            return ALLOW
    except _Blocked as blocked:
        return _block(str(blocked))
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
        self.trusted = _trusted_cli_words(ctx)

    # --- non-shell tools ---

    def memory(self) -> tuple[bool, str]:
        if self.card is None:
            return ALLOW
        if self.card.get("mode") != "planning":
            return _block("memory writes are allowed only on planning cards.")
        if self.finished:
            return _block("this card is finished (`card finish` ran); no further writes.")
        return ALLOW

    def skill_manage(self) -> tuple[bool, str]:
        if self.finished or self.worker is not None:
            return _block(
                "skill_manage edits the profile's live skills, outside your worktree; "
                "stage skill changes as files in your worktree instead."
            )
        return ALLOW

    # --- write targets ---

    def check_write(self, target: str, cwd: Path) -> str | None:
        """Raise `_Blocked` for a forbidden write; return a note when the target is unresolved."""
        expanded = _expand(target, self.home)
        if expanded in DEVICE_TARGETS or expanded.startswith("/dev/fd/"):
            return None
        if "$" in expanded:
            if ".softmax" in expanded:
                raise _Blocked("writes under ~/.softmax switch the player identity.")
            if self.finished:
                raise _Blocked("this card is finished (`card finish` ran); no further writes.")
            return f"unresolved write target {target!r}"
        resolved = _real(cwd / expanded)
        if _within(resolved, self.softmax_dir):
            raise _Blocked("writes under ~/.softmax switch the player identity.")
        if self.finished:
            raise _Blocked("this card is finished (`card finish` ran); no further writes.")
        if self.worker is not None and not _within(resolved, self.worker):
            raise _Blocked(
                f"model writes must stay inside your worktree {self.ctx.worker}; "
                f"{target!r} resolves to {resolved}. Canonical bookkeeping goes through "
                '"$AUTORESEARCH_CLI".'
            )
        return None

    # --- shell ---

    def shell(self, command: str, cwd: Path) -> tuple[bool, str]:
        notes: list[str] = []
        try:
            self._walk_shell(command, cwd, 0, notes)
        except _Blocked as blocked:
            return _block(str(blocked))
        return (True, "; ".join(notes)) if notes else ALLOW

    def _walk_shell(self, command: str, cwd: Path, depth: int, notes: list[str]) -> None:
        if depth > MAX_NESTING:
            self._unparsed(command, "nested too deeply", notes)
            return
        try:
            tokens = _tokenize(_strip_heredoc_bodies(command).replace("`", " ( "))
        except ValueError as error:
            self._unparsed(command, str(error), notes)
            return
        for words, targets in _simple_commands(tokens):
            if words and words[0] == "cd":
                cwd = self._cd(words, cwd)
                continue
            for target in targets + self._inspect(words, cwd, depth, notes):
                note = self.check_write(target, cwd)
                if note:
                    notes.append(note)

    def _unparsed(self, command: str, why: str, notes: list[str]) -> None:
        if BLOCKED_TEXT.search(command):
            raise _Blocked(
                "this command contains a raw platform mutation or player switch; "
                'use the "$AUTORESEARCH_CLI" softmax commands.'
            )
        notes.append(f"unparsed shell allowed ({why})")

    def _cd(self, words: list[str], cwd: Path) -> Path:
        if len(words) < 2:
            return self.home
        target = _expand(words[1], self.home)
        return cwd if "$" in target or target == "-" else cwd / target

    def _inspect(self, words: list[str], cwd: Path, depth: int, notes: list[str]) -> list[str]:
        """Check one simple command; return the write targets it names."""
        core = self._unwrap(words, cwd, depth, notes)
        if not core:
            return []
        first = core[0]
        if first in self.trusted or (os.path.isabs(first) and _real(Path(first)) in self.trusted):
            return []
        name = os.path.basename(first)
        operands = [word for word in core[1:] if not word.startswith("-")]
        if name == "coworld":
            self._check_coworld(operands)
        elif name == "softmax" and {"login", "logout"} & set(operands[:2]):
            raise _Blocked("`softmax login/logout` switches the player identity.")
        elif name == "git":
            return self._check_git(core[1:], cwd)
        elif name == "find":
            return self._find_exec(core, cwd, depth, notes)
        return _writer_targets(name, core[1:])

    def _unwrap(self, words: list[str], cwd: Path, depth: int, notes: list[str]) -> list[str]:
        """Strip env assignments and wrappers. Nested scripts (`bash -c`, `eval`) are walked
        here and yield an empty core."""
        index = 0
        while index < len(words):
            word = words[index]
            name = os.path.basename(word)
            if ASSIGNMENT.match(word) or word in PREFIX_WORDS:
                index += 1
            elif name == "timeout":
                index = _skip_options(words, index + 1, {"-s", "-k", "--signal", "--kill-after"})
                index += 1  # the duration
            elif name in OPTION_WRAPPERS:
                if name == "env" and "-S" in words[index + 1:]:
                    split = words.index("-S", index + 1)
                    if split + 1 < len(words):
                        self._walk_shell(" ".join(words[split + 1:]), cwd, depth + 1, notes)
                    return []
                index = _skip_options(words, index + 1, OPTION_WRAPPERS[name])
            elif name == "uv" and words[index + 1:index + 2] == ["run"]:
                index = _skip_options(words, index + 2, UV_RUN_VALUE_OPTIONS)
            elif name == "uv" and words[index + 1:index + 3] == ["tool", "run"]:
                index = _skip_options(words, index + 3, OPTION_WRAPPERS["uvx"])
            elif name in {"poetry", "pdm", "hatch", "pipx"} and words[index + 1:index + 2] == [
                "run"
            ]:
                index = _skip_options(words, index + 2, OPTION_WRAPPERS["pipx"])
            elif re.fullmatch(r"python[0-9.]*", name):
                rest = words[index + 1:]
                if "-m" in rest:
                    module_at = index + 1 + rest.index("-m") + 1
                    return words[module_at:]
                if "-c" in rest:
                    code_at = index + 1 + rest.index("-c") + 1
                    code = words[code_at] if code_at < len(words) else ""
                    if BLOCKED_TEXT.search(code):
                        raise _Blocked(
                            "code that runs a raw platform mutation or player switch is blocked; "
                            'use the "$AUTORESEARCH_CLI" softmax commands.'
                        )
                return words[index:]
            elif name in SHELLS:
                script_at = _shell_script_index(words, index + 1)
                if script_at is None:
                    return words[index:]
                if script_at < len(words):
                    self._walk_shell(words[script_at], cwd, depth + 1, notes)
                return []
            elif name == "eval":
                self._walk_shell(" ".join(words[index + 1:]), cwd, depth + 1, notes)
                return []
            else:
                return words[index:]
        return []

    def _check_coworld(self, operands: list[str]) -> None:
        head = operands[:3]
        if "submit" in head or "upload-policy" in head or _adjacent(head, "xp-request", "create"):
            raise _Blocked(
                "raw `coworld submit/upload-policy/xp-request create` is blocked; use the "
                '"$AUTORESEARCH_CLI" softmax commands, which check the player identity first.'
            )
        if _adjacent(head, "player", "use"):
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

    def _find_exec(self, core: list[str], cwd: Path, depth: int, notes: list[str]) -> list[str]:
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in core:
                start = core.index(flag) + 1
                inner = [w for w in core[start:] if w not in {";", "+", "{}"}]
                return self._inspect(inner, cwd, depth, notes)
        return []


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
        if len(operands) >= 2 and not (name == "rsync" and ":" in operands[-1]):
            return [operands[-1]]
        return []
    if name == "sed" and any(w == "--in-place" or re.match(r"-i|--in-place=", w) for w in args):
        files = _operands_skipping(args, {"-e", "-f", "--expression", "--file"})
        has_script_option = any(w in {"-e", "-f", "--expression", "--file"} for w in args)
        return files if has_script_option else files[1:]
    if name == "dd":
        return [word[3:] for word in args if word.startswith("of=")]
    return []


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


def _shell_script_index(words: list[str], index: int) -> int | None:
    """Index of the `-c` script for `bash -c 'x'` / `bash -lc 'x'`; None for `bash script.sh`."""
    while index < len(words) and words[index].startswith("-"):
        flag = words[index]
        if not flag.startswith("--") and "c" in flag:
            return index + 1
        index += 1
    return None


def _adjacent(words: list[str], first: str, second: str) -> bool:
    return any(a == first and b == second for a, b in itertools.pairwise(words))


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
    """Yield `(words, redirect_write_targets)` for each simple command."""
    words: list[str] = []
    targets: list[str] = []
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
            if ">" in token and following is not None and not _is_operator(following):
                duplicating = token.endswith("&") and (following.isdigit() or following == "-")
                if not duplicating:
                    targets.append(following)
            index += 2 if following is not None and not _is_operator(following) else 1
            continue
        yield words, targets
        words, targets = [], []
        index += 1
    yield words, targets


def _strip_heredoc_bodies(command: str) -> str:
    """Drop here-document bodies: they are data, not commands."""
    kept: list[str] = []
    pending: list[tuple[str, bool]] = []
    for line in command.split("\n"):
        if pending:
            delimiter, strip_tabs = pending[0]
            if (line.lstrip("\t") if strip_tabs else line).strip() == delimiter:
                pending.pop(0)
            continue
        kept.append(line)
        for match in HEREDOC.finditer(line):
            if line[match.start():match.start() + 3] != "<<<":
                pending.append((match.group(3), match.group(1) == "-"))
    return "\n".join(kept)


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


def command_signature(tool_name: str, args: dict) -> str:
    """Short, secret-free form of a call for the repeated-tool-error signal."""
    args = args if isinstance(args, dict) else {}
    if tool_name == "terminal" and isinstance(args.get("command"), str):
        command = args["command"]
        try:
            first = next(_simple_commands(_tokenize(_strip_heredoc_bodies(command))))[0]
        except (ValueError, StopIteration):
            first = command.split()
        words = []
        for word in first:
            if ASSIGNMENT.match(word) and not words:
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
