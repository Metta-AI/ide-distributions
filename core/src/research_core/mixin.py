"""MIXIN.md front matter and the mixin's shell commands (design D.1, amendment A3.2).

A mixin is the game-specific folder at ``<coworld>/code/mixin/``. Its ``MIXIN.md`` opens with a
YAML front matter block that names the Coworld, the shell commands the tools run (``build``,
``check``, ``upload``, ``evaluate``), the metrics, the artifact ``version.create`` records, the
evidence types, the toolchains setup installs and an optional ``cost_per_episode``. The prose
after the block is kept as ``Mixin.body``.

Runs only in the workspace environment: PyYAML is imported inside ``load`` so the module itself
stays importable anywhere. ``run_command`` starts a mixin command detached and waits up to a
budget; a command that outlives the budget keeps running and is picked up later through
``resume``, possibly from another process (B.15: the full output is always in a log file).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

FRONT_MATTER_FILE = "MIXIN.md"
REQUIRED_COMMANDS = ("build", "check")
OPTIONAL_COMMANDS = ("upload", "evaluate")
ARTIFACT_KINDS = ("file", "image")
METRIC_DIRECTIONS = ("up", "down")

DEFAULT_BUDGET_S = 240
TAIL_CHARS = 2000

# The outer shell runs the command in an inner shell so an ``exit N`` inside the command cannot
# skip the bookkeeping; the exit code is written to ``$2`` (the ``.exit`` file beside the log)
# and then re-raised, so ``Popen.returncode`` carries it too.
_WRAPPER = 'sh -c "$1"; code=$?; echo "$code" > "$2"; exit "$code"'


class MixinError(Exception):
    """A MIXIN.md the tools cannot use. ``problems`` lists everything wrong with it at once."""

    def __init__(self, problems: Sequence[str] | str) -> None:
        if isinstance(problems, str):
            problems = [problems]
        self.problems: list[str] = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass
class Mixin:
    coworld: str
    coworld_version: str | None
    commands: dict[str, str]
    metrics: list[dict]
    artifact: dict
    evidence_types: list[dict]
    toolchains: list[dict]
    cost_per_episode: float | None
    skills_dir: Path | None
    path: Path
    body: str

    def to_dict(self) -> dict:
        return {
            "coworld": self.coworld,
            "coworld_version": self.coworld_version,
            "commands": dict(self.commands),
            "metrics": [dict(m) for m in self.metrics],
            "artifact": dict(self.artifact),
            "evidence_types": [dict(e) for e in self.evidence_types],
            "toolchains": [dict(t) for t in self.toolchains],
            "cost_per_episode": self.cost_per_episode,
            "skills_dir": str(self.skills_dir) if self.skills_dir else None,
            "path": str(self.path),
            "body": self.body,
        }


@dataclass(frozen=True)
class CommandResult:
    """What a mixin command did so far.

    ``finished`` is False while the command still runs past its budget; then ``resume_token``
    is set and ``exit_code`` is None. A finished command with ``exit_code`` None ended without
    writing its exit file (killed, or the shell itself died). ``output_tail`` is the last
    ``TAIL_CHARS`` characters of the log, which holds stdout and stderr interleaved.
    """

    name: str
    command: str
    finished: bool
    exit_code: int | None
    output_tail: str
    log_path: Path
    duration_s: float
    pid: int
    resume_token: str | None = None

    @property
    def ok(self) -> bool:
        return self.finished and self.exit_code == 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "finished": self.finished,
            "exit_code": self.exit_code,
            "output_tail": self.output_tail,
            "log_path": str(self.log_path),
            "duration_s": round(self.duration_s, 3),
            "pid": self.pid,
            "resume_token": self.resume_token,
        }


# --- front matter -------------------------------------------------------------------------


def load(mixin_dir: Path) -> Mixin:
    """Parse ``<mixin_dir>/MIXIN.md``; raise ``MixinError`` with every problem at once."""
    import yaml

    mixin_dir = Path(mixin_dir)
    file = mixin_dir / FRONT_MATTER_FILE
    if not file.is_file():
        raise MixinError([f"{file} not found"])
    front, body, problem = split_front_matter(file.read_text(encoding="utf-8"))
    if problem:
        raise MixinError([f"{FRONT_MATTER_FILE}: {problem}"])
    try:
        data = yaml.safe_load(front)
    except yaml.YAMLError as error:
        problem = f"front matter is not valid YAML: {error}"
        raise MixinError([f"{FRONT_MATTER_FILE}: {problem}"]) from None
    if not isinstance(data, Mapping):
        raise MixinError([f"{FRONT_MATTER_FILE}: front matter must be a mapping"])

    problems: list[str] = []
    coworld = _text(data, "coworld", problems, required=True)
    coworld_version = _version(data.get("coworld_version"), problems)
    commands = _commands(data.get("commands"), problems)
    metrics = _metrics(data.get("metrics"), problems)
    artifact = _artifact(data.get("artifact"), problems)
    evidence_types = _evidence_types(data.get("evidence_types"), problems)
    toolchains = _toolchains(data.get("toolchains"), problems)
    cost_per_episode = _cost(data.get("cost_per_episode"), problems)
    if problems:
        raise MixinError(problems)

    skills_dir = mixin_dir / "skills"
    return Mixin(
        coworld=coworld,
        coworld_version=coworld_version,
        commands=commands,
        metrics=metrics,
        artifact=artifact,
        evidence_types=evidence_types,
        toolchains=toolchains,
        cost_per_episode=cost_per_episode,
        skills_dir=skills_dir if skills_dir.is_dir() else None,
        path=mixin_dir,
        body=body,
    )


def split_front_matter(text: str) -> tuple[str, str, str | None]:
    """Split a document into (front matter YAML, body, problem).

    The front matter is the block between a ``---`` first line and the next ``---`` line.
    ``problem`` names what is wrong when there is no such block; the YAML is then empty.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text, "no front matter: the file must start with a '---' line"
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :]), None
    return "", text, "front matter is not closed: no second '---' line"


def _is_text(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _text(data: Mapping, key: str, problems: list[str], *, required: bool) -> str:
    value = data.get(key)
    if value is None:
        if required:
            problems.append(f"{key}: required")
        return ""
    if not _is_text(value):
        problems.append(f"{key}: expected a non-empty string")
        return ""
    return value.strip()


def _version(value: object, problems: list[str]) -> str | None:
    if value is None:
        return None
    if _is_number(value):  # YAML reads 1.0 as a float; keep it as written
        return str(value)
    if not _is_text(value):
        problems.append("coworld_version: expected a string")
        return None
    return value.strip()


def _commands(raw: object, problems: list[str]) -> dict[str, str]:
    if raw is None:
        problems.append("commands: required (build and check at least)")
        return {}
    if not isinstance(raw, Mapping):
        problems.append("commands: expected a mapping of name to shell command")
        return {}
    commands: dict[str, str] = {}
    for name, command in raw.items():
        if not _is_text(command):
            problems.append(f"commands.{name}: expected a non-empty shell command")
            continue
        commands[str(name)] = command.strip()
    for name in REQUIRED_COMMANDS:
        if name not in raw:
            problems.append(f"commands.{name}: required")
    return commands


def _metrics(raw: object, problems: list[str]) -> list[dict]:
    """Accept a list of metric mappings or a mapping ``name -> spec``; return a list."""
    if raw is None:
        return []
    entries: list[object]
    if isinstance(raw, Mapping):
        entries = []
        for name, spec in raw.items():
            if isinstance(spec, str):  # shorthand: ``win: up``
                spec = {"direction": spec}
            if not isinstance(spec, Mapping):
                problems.append(f"metrics.{name}: expected a mapping or a direction")
                continue
            entries.append({"name": name, **spec})
    elif isinstance(raw, list):
        entries = list(raw)
    else:
        problems.append("metrics: expected a list or a mapping")
        return []

    metrics: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"metrics[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(f"{label}: expected a mapping")
            continue
        metric = dict(entry)
        name = metric.get("name")
        if _is_text(name):
            label = f"metrics.{name}"
            if name in seen:
                problems.append(f"{label}: declared twice")
            seen.add(name)
        else:
            problems.append(f"{label}.name: required, a non-empty string")
        if metric.get("direction") not in METRIC_DIRECTIONS:
            problems.append(f"{label}.direction: expected one of {', '.join(METRIC_DIRECTIONS)}")
        if "role" in metric and not _is_text(metric["role"]):
            problems.append(f"{label}.role: expected a non-empty string")
        for key in ("min_effect", "tolerance"):
            if metric.get(key) is not None and not _is_number(metric[key]):
                problems.append(f"{label}.{key}: expected a number")
        episodes = metric.get("minimum_episodes")
        if episodes is not None and not (isinstance(episodes, int) and episodes >= 1):
            problems.append(f"{label}.minimum_episodes: expected a positive integer")
        metrics.append(metric)
    return metrics


def _artifact(raw: object, problems: list[str]) -> dict:
    if raw is None:
        problems.append("artifact: required ({kind, path})")
        return {}
    if not isinstance(raw, Mapping):
        problems.append("artifact: expected a mapping {kind, path}")
        return {}
    artifact = dict(raw)
    if artifact.get("kind") not in ARTIFACT_KINDS:
        problems.append(f"artifact.kind: expected one of {', '.join(ARTIFACT_KINDS)}")
    if not _is_text(artifact.get("path")):
        problems.append("artifact.path: required, a non-empty string")
    return artifact


def _evidence_types(raw: object, problems: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append("evidence_types: expected a list")
        return []
    types: list[dict] = []
    for index, entry in enumerate(raw):
        if _is_text(entry):
            types.append({"name": entry.strip()})
        elif isinstance(entry, Mapping) and _is_text(entry.get("name")):
            types.append(dict(entry))
        else:
            problems.append(f"evidence_types[{index}]: expected a name or a mapping with a name")
    return types


def _toolchains(raw: object, problems: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append("toolchains: expected a list of {kind, version?, url?, sha256?}")
        return []
    toolchains: list[dict] = []
    for index, entry in enumerate(raw):
        label = f"toolchains[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(f"{label}: expected a mapping")
            continue
        toolchain = dict(entry)
        if not _is_text(toolchain.get("kind")):
            problems.append(f"{label}.kind: required, a non-empty string")
        for key in ("version", "url", "sha256"):
            if toolchain.get(key) is not None and not _is_text(toolchain[key]):
                problems.append(f"{label}.{key}: expected a string")
        toolchains.append(toolchain)
    return toolchains


def _cost(raw: object, problems: list[str]) -> float | None:
    if raw is None:
        return None
    if not _is_number(raw) or raw < 0:
        problems.append("cost_per_episode: expected a number of credits, zero or more")
        return None
    return float(raw)


# --- commands -----------------------------------------------------------------------------


def with_workspace_bin(env: Mapping[str, str] | None) -> dict[str, str]:
    """The environment for a mixin command, with the workspace environment's ``bin`` first on
    ``PATH``: a command's ``python3``, ``coworld`` and ``softmax`` are then the workspace's own
    (the interpreter the lock pins), not whatever the host puts first."""
    from research_core import layout

    merged = dict(os.environ if env is None else env)
    try:
        bin_dir = layout.from_env(merged).venv / "bin"
    except Exception:  # no workspace in this environment: leave PATH alone
        return merged
    if bin_dir.is_dir():
        merged["PATH"] = f"{bin_dir}{os.pathsep}{merged.get('PATH', '')}"
    return merged


def run_command(
    mixin: Mixin,
    name: str,
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    budget_s: float = DEFAULT_BUDGET_S,
    log_dir: Path,
) -> CommandResult:
    """Start the mixin command ``name`` in ``cwd`` and wait up to ``budget_s`` for it.

    The command runs detached (its own session) with stdout and stderr appended to
    ``<log_dir>/<name>-<UTC timestamp>.log``; its exit code is written to ``<log>.exit``. When
    it ends within the budget the result is complete. Otherwise the process keeps running and
    the result has ``finished=False`` with a ``resume_token`` for ``resume``. A failed command
    is only reported here; the caller records the broken capability (A4.1).
    """
    if name not in mixin.commands:
        raise MixinError([f"the mixin declares no '{name}' command"])
    command = mixin.commands[name]
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")
    log_path = log_dir / f"{name}-{stamp}Z.log"
    exit_path = _exit_path(log_path)
    started = time.time()
    with open(log_path, "ab") as log:
        log.write(f"$ {command}\n".encode())
        log.flush()
        process = subprocess.Popen(
            ["sh", "-c", _WRAPPER, "ide-mixin-command", command, str(exit_path)],
            cwd=str(cwd),
            env=with_workspace_bin(env),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    token = json.dumps(
        {"pid": process.pid, "log": str(log_path), "started": started, "name": name, "cmd": command}
    )
    try:
        process.wait(timeout=budget_s)
    except subprocess.TimeoutExpired:
        return CommandResult(
            name=name,
            command=command,
            finished=False,
            exit_code=None,
            output_tail=_tail(log_path),
            log_path=log_path,
            duration_s=time.time() - started,
            pid=process.pid,
            resume_token=token,
        )
    exit_code = _read_exit(exit_path)
    return CommandResult(
        name=name,
        command=command,
        finished=True,
        exit_code=process.returncode if exit_code is None else exit_code,
        output_tail=_tail(log_path),
        log_path=log_path,
        duration_s=time.time() - started,
        pid=process.pid,
    )


def resume(token: str) -> CommandResult:
    """Report on a command started earlier, by anyone, from its ``resume_token``.

    Completion is read from the ``.exit`` file the wrapper writes, which survives the parent
    that started the command. A missing exit file with a dead pid means the command was killed
    (``finished=True, exit_code=None``). A pid recycled by an unrelated process would keep a
    killed command "running"; the exit file is the primary signal for that reason.
    """
    try:
        info = json.loads(token)
        pid = int(info["pid"])
        log_path = Path(info["log"])
        started = float(info["started"])
        name = str(info["name"])
        command = str(info.get("cmd", ""))
    except (ValueError, KeyError, TypeError) as error:
        raise MixinError([f"not a resume token: {error}"]) from None
    exit_path = _exit_path(log_path)
    _reap(pid)
    exit_code = _read_exit(exit_path)
    if exit_code is not None:
        finished, ended = True, exit_path.stat().st_mtime
    elif _alive(pid):
        finished, ended = False, time.time()
    else:
        finished, ended = True, time.time()
    return CommandResult(
        name=name,
        command=command,
        finished=finished,
        exit_code=exit_code,
        output_tail=_tail(log_path),
        log_path=log_path,
        duration_s=max(0.0, ended - started),
        pid=pid,
        resume_token=None if finished else token,
    )


def _exit_path(log_path: Path) -> Path:
    return log_path.with_name(log_path.name + ".exit")


def _read_exit(exit_path: Path) -> int | None:
    """The exit code in the file, or None while it is absent or not yet written out."""
    try:
        return int(exit_path.read_text().strip())
    except (OSError, ValueError):
        return None


def _tail(log_path: Path, chars: int = TAIL_CHARS) -> str:
    try:
        size = log_path.stat().st_size
        with open(log_path, "rb") as log:
            log.seek(max(0, size - chars * 4))
            return log.read().decode("utf-8", errors="replace")[-chars:]
    except OSError:
        return ""


def _reap(pid: int) -> None:
    """Collect the child's exit status when it is ours, so a zombie is not mistaken for alive."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
