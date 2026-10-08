"""Tool policy: the precise guard in front of every model tool call. STDLIB ONLY.

`tool_policy` decides one call. It blocks:

- player switching: `coworld player use`, `softmax login|logout`, any write under `~/.softmax`;
- raw platform mutations: `coworld submit|upload-policy|xp-request create` (C6: only the trusted
  softmax tool behind `"$AUTORESEARCH_CLI"` may mutate the platform);
- force pushes, ref deletions and pushes to `main`/`master` from model commands;
- model writes outside the worker worktree (C2; scratch files go to `<worktree>/.scratch/`),
  every model write after `card finish` (C3), and `memory` writes outside planning cards.

Tool names and argument keys are those of Hermes v0.21.5 (source tag v2026.9.24):
(`tools/terminal_tool.py`
`terminal{command, workdir}`, `tools/file_tools.py` `write_file{path}` and
`patch{path | mode="patch", patch}`, `tools/memory_tool.py` `memory`,
`tools/code_execution_tool.py` `execute_code{code}`). Any other tool is allowed, including
`skill_manage` (it writes to `skills.create_dir`, the staging area queue-keeper sweeps, C9).

Shell commands are tokenized with `shlex` and every simple command is inspected, including
ones behind `&&`, `;`, `|`, subshells, `$(...)` and backticks (also inside double quotes),
`bash -c`, `eval`, `xargs`, `find -exec`, `watch`, env prefixes and launcher wrappers
(`uv run`, `uvx`, `python -m`, `sudo`, `strace`, ...). A shell reading its script from stdin
(`... | sh`, `sh <<EOF`, `bash -s`) has its here-document bodies walked, and when piped the
raw text is checked for blocked phrases. `ssh host <cmd>` is checked for blocked phrases.
This is a best-effort parser, not a shell: it guards against a model reaching for a
blocked command, not against an adversary. The approvals.deny globs in config.yaml are
the coarse second layer.

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
DESTINATION_WRITERS = {"cp", "install", "ln", "rsync"}
SECRET_WORD = re.compile(
    r"(?i)(token|key|secret|passw|bearer|authorization|credential)"
)
SIGNATURE_WORDS = 5
SIGNATURE_MAX = 120
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
FINISHED = "this card is finished (`card finish` ran); no further writes."
SOFTMAX_WRITE = "writes under ~/.softmax switch the player identity."


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
            if self.finished:
                raise _Blocked(FINISHED)
            return f"unresolved write target {target!r}"
        if _within(resolved, self.softmax_dir):
            raise _Blocked(SOFTMAX_WRITE)
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
        text, bodies = _split_heredocs(command)
        for start, end, body in _substitutions(text):
            self._walk_shell(body, cwd, depth + 1, notes)
            # `$(which coworld) submit`: the substitution's output is the command word.
            if text[:start].rstrip()[-1:] in ("", *COMMAND_STARTERS):
                words = body.split()
                head = words[-1].strip("'\"") if words else ""
                if BLOCKED_TEXT.search(f"{head} {text[end:]}"):
                    raise _Blocked(RAW_MUTATION)
        try:
            tokens = _tokenize(text.replace("`", " ( "))
        except ValueError as error:
            self._unparsed(command, str(error), notes)
            return
        line = _Line(raw=command, bodies=bodies)
        for words, targets, piped in _simple_commands(tokens):
            if words and words[0] in ("cd", "pushd"):
                cwd = self._cd(words, cwd)
                continue
            if words and all(ASSIGNMENT.match(word) for word in words):
                for word in words:
                    name, value = word.split("=", 1)
                    line.variables[name] = value
                continue
            line.piped = piped
            for target in targets + self._inspect(words, cwd, depth, notes, line):
                note = self.check_write(target, cwd)
                if note:
                    notes.append(note)

    def _unparsed(self, command: str, why: str, notes: list[str]) -> None:
        if BLOCKED_TEXT.search(command):
            raise _Blocked(RAW_MUTATION)
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
        if first in self.trusted or (os.path.isabs(first) and _real(Path(first)) in self.trusted):
            return []
        if first.startswith("$"):  # `c=coworld; $c submit`
            first = _expand_variables(first, line.variables)
        # `coworld@latest`, `coworld==1.0` (uvx / uv tool run package specs)
        name = VERSION_SUFFIX.sub("", os.path.basename(first))
        operands = [word for word in core[1:] if not word.startswith("-")]
        if name == "coworld":
            self._check_coworld(operands)
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
                if BLOCKED_TEXT.search(" ".join(words[host + 1:])):
                    raise _Blocked(RAW_MUTATION)
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
                    code = words[code_at] if code_at < len(words) else ""
                    if BLOCKED_TEXT.search(code):
                        raise _Blocked(RAW_MUTATION)
                return words[index:]
            elif name in SHELLS:
                mode, script_at = _shell_mode(words, index + 1)
                if mode == "c" and script_at < len(words):
                    self._walk_shell(words[script_at], cwd, depth + 1, notes)
                elif mode == "stdin":
                    # The script arrives on stdin: here-document bodies, or a pipe.
                    for body in line.bodies:
                        self._walk_shell(body, cwd, depth + 1, notes)
                    if line.piped and BLOCKED_TEXT.search(line.raw):
                        raise _Blocked(RAW_MUTATION)
                return []
            elif name == "eval":
                self._walk_shell(" ".join(words[index + 1:]), cwd, depth + 1, notes)
                return []
            else:
                return words[index:]
        return []

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


class _Line:
    """Per command line state for the shell walk."""

    def __init__(self, raw: str, bodies: list[str]):
        self.raw = raw
        self.bodies = bodies
        self.piped = False  # the current simple command reads from a pipe
        self.variables: dict[str, str] = {}


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
    """Yield `(words, redirect_write_targets, piped)` for each simple command; `piped` is
    True when the command reads from a `|`."""
    words: list[str] = []
    targets: list[str] = []
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
            if ">" in token and following is not None and not _is_operator(following):
                duplicating = token.endswith("&") and (following.isdigit() or following == "-")
                if not duplicating:
                    targets.append(following)
            index += 2 if following is not None and not _is_operator(following) else 1
            continue
        yield words, targets, piped
        piped = token in {"|", "|&"}
        words, targets = [], []
        index += 1
    yield words, targets, piped


def _split_heredocs(command: str) -> tuple[str, list[str]]:
    """`(command without here-document bodies, the bodies)`. Bodies are data unless a shell
    reads its script from stdin; the walk decides."""
    kept: list[str] = []
    bodies: list[str] = []
    pending: list[tuple[str, bool]] = []
    body: list[str] = []
    for line in command.split("\n"):
        if pending:
            delimiter, strip_tabs = pending[0]
            if (line.lstrip("\t") if strip_tabs else line).strip() == delimiter:
                pending.pop(0)
                bodies.append("\n".join(body))
                body = []
            else:
                body.append(line)
            continue
        kept.append(line)
        for match in HEREDOC.finditer(line):
            if line[match.start():match.start() + 3] != "<<<":
                pending.append((match.group(3), match.group(1) == "-"))
    if body:
        bodies.append("\n".join(body))
    return "\n".join(kept), bodies


def _substitutions(text: str) -> list[tuple[int, int, str]]:
    """`(start, end, body)` of each top-level `$(...)` and backtick substitution that the
    shell would run: outside single quotes, inside double quotes or bare."""
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
            found.append((index, end, text[index + 2:end - 1]))
            index = end
            continue
        elif not single and char == "`":
            close = text.find("`", index + 1)
            end = len(text) if close < 0 else close + 1
            found.append((index, end, text[index + 1:close if close >= 0 else len(text)]))
            index = end
            continue
        index += 1
    return found


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


def command_signature(tool_name: str, args: dict) -> str:
    """Short, secret-free form of a call for the repeated-tool-error signal."""
    args = args if isinstance(args, dict) else {}
    if tool_name == "terminal" and isinstance(args.get("command"), str):
        command = args["command"]
        try:
            first = next(_simple_commands(_tokenize(_split_heredocs(command)[0])))[0]
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
