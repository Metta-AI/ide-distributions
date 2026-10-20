"""Load a game contract from ``games/<name>/GAME.md``. Core code never names a game."""

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ide_research_core.io.docs import read_doc

BINDING_NAMES = ("build", "local_check", "upload", "evaluate", "replay_inspect")
COMPILERS = ("pw", "basic", "none")
DIRECTIONS = ("up", "down")
# How a maintenance card reads the game source commit the current game version runs.
# ``coworld_manifest``: ``Softmax.game_source_pin`` (the commit in the Coworld manifest).
PIN_SOURCES = ("coworld_manifest",)


class GameError(Exception):
    """The game contract is missing, or has missing or invalid fields."""


@dataclass(frozen=True)
class GameMetric:
    name: str
    direction: Literal["up", "down"]


@dataclass(frozen=True)
class Game:
    """A game contract. Fields mirror the YAML front matter of ``GAME.md`` (the body is ``guide``).

    - name: game directory name under ``games/``.
    - bindings: command lines for build, local_check, upload, evaluate, replay_inspect.
      Relative paths resolve from the repo root.
    - reference_types: extra reference type -> resolver script path.
    - outcome_metrics: list of ``{name, direction: up|down}``.
    - performance_guardrails: metric names (each must be an outcome metric).
    - min_episodes_floor: lowest allowed N for episodes per comparison.
    - league_id: league the evaluate binding plays in.
    - noise_source: where metric noise is measured (for example ``league_episodes``).
    - layers: STRATEGY.md layers that apply to this game.
    - compiler: ``pw``, ``basic`` or ``none``.
    - source: optional ``{repo, pin}``: the game source repository (git URL or local path) and
      how to read the commit the current game version runs (one of ``PIN_SOURCES``). With it,
      a maintenance card renews code citations when the commit changes; without it, claims only go
      stale on a version change.
    - host_env, host_tools: optional ``host_requirements: {env: [...], tools: [...]}``: the
      environment variables and executables (on PATH) a worker needs for the bindings, such as
      a build. A worker checks them before a long build and blocks its card with a
      ``help_request:`` reason when one is missing.
    """

    name: str
    bindings: dict[str, str]
    reference_types: dict[str, str]
    outcome_metrics: list[GameMetric]
    performance_guardrails: list[str]
    min_episodes_floor: int
    league_id: str
    noise_source: str
    layers: list[str]
    compiler: Literal["pw", "basic", "none"]
    guide: str
    source: dict[str, str] | None = None
    host_env: tuple[str, ...] = ()
    host_tools: tuple[str, ...] = ()


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_bindings(meta: dict, problems: list[str]) -> None:
    bindings = meta.get("bindings")
    if not isinstance(bindings, dict):
        problems.append("bindings: must be a map")
        return
    for binding in BINDING_NAMES:
        if not _is_text(bindings.get(binding)):
            problems.append(f"bindings.{binding}: missing or empty")
    for extra in sorted(set(bindings) - set(BINDING_NAMES)):
        problems.append(f"bindings.{extra}: unknown binding")


def _check_metrics(meta: dict, problems: list[str]) -> list[str]:
    metrics = meta.get("outcome_metrics")
    if not isinstance(metrics, list) or not metrics:
        problems.append("outcome_metrics: must be a non-empty list")
        return []
    names = []
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict) or not _is_text(metric.get("name")):
            problems.append(f"outcome_metrics[{index}].name: missing")
            continue
        names.append(metric["name"])
        if metric.get("direction") not in DIRECTIONS:
            problems.append(f"outcome_metrics[{index}].direction: must be up or down")
    return names


def _check_fields(meta: dict) -> list[str]:
    problems: list[str] = []
    for key in ("name", "league_id", "noise_source"):
        if not _is_text(meta.get(key)):
            problems.append(f"{key}: missing or empty")
    _check_bindings(meta, problems)
    reference_types = meta.get("reference_types")
    if not isinstance(reference_types, dict):
        problems.append("reference_types: must be a map")
    metric_names = _check_metrics(meta, problems)
    guardrails = meta.get("performance_guardrails")
    if not isinstance(guardrails, list) or not guardrails:
        problems.append("performance_guardrails: must be a non-empty list")
    else:
        for guardrail in guardrails:
            if guardrail not in metric_names:
                problems.append(
                    f"performance_guardrails: {guardrail!r} is not in outcome_metrics"
                )
    floor = meta.get("min_episodes_floor")
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
        problems.append("min_episodes_floor: must be a positive integer")
    layers = meta.get("layers")
    if not isinstance(layers, list) or not all(_is_text(layer) for layer in layers):
        problems.append("layers: must be a list of names")
    if meta.get("compiler") not in COMPILERS:
        problems.append("compiler: must be one of pw, basic, none")
    _check_source(meta, problems)
    _check_host_requirements(meta, problems)
    return problems


def _check_host_requirements(meta: dict, problems: list[str]) -> None:
    requirements = meta.get("host_requirements")
    if requirements is None:
        return
    if not isinstance(requirements, dict):
        problems.append("host_requirements: must be a map with env and/or tools")
        return
    for key in ("env", "tools"):
        names = requirements.get(key, [])
        if not isinstance(names, list) or not all(_is_text(name) for name in names):
            problems.append(f"host_requirements.{key}: must be a list of names")
    for extra in sorted(set(requirements) - {"env", "tools"}):
        problems.append(f"host_requirements.{extra}: unknown field")


def _check_source(meta: dict, problems: list[str]) -> None:
    source = meta.get("source")
    if source is None:
        return
    if not isinstance(source, dict):
        problems.append("source: must be a map with repo and pin")
        return
    if not _is_text(source.get("repo")):
        problems.append("source.repo: missing or empty")
    if source.get("pin") not in PIN_SOURCES:
        problems.append(f"source.pin: must be one of {', '.join(PIN_SOURCES)}")
    for extra in sorted(set(source) - {"repo", "pin"}):
        problems.append(f"source.{extra}: unknown field")


def load_game(repo_root: Path, name: str) -> Game:
    path = Path(repo_root) / "games" / name / "GAME.md"
    if not path.exists():
        raise GameError(f"game {name!r}: {path} not found")
    meta, body = read_doc(path)
    problems = _check_fields(meta)
    if problems:
        raise GameError(f"game {name!r} ({path}): " + "; ".join(problems))
    return Game(
        name=meta["name"],
        bindings={binding: meta["bindings"][binding] for binding in BINDING_NAMES},
        reference_types=dict(meta["reference_types"]),
        outcome_metrics=[GameMetric(m["name"], m["direction"]) for m in meta["outcome_metrics"]],
        performance_guardrails=list(meta["performance_guardrails"]),
        min_episodes_floor=meta["min_episodes_floor"],
        league_id=meta["league_id"],
        noise_source=meta["noise_source"],
        layers=list(meta["layers"]),
        compiler=meta["compiler"],
        guide=body,
        source=dict(meta["source"]) if meta.get("source") is not None else None,
        host_env=tuple((meta.get("host_requirements") or {}).get("env", [])),
        host_tools=tuple((meta.get("host_requirements") or {}).get("tools", [])),
    )


def binding_argv(command_line: str) -> list[str]:
    """argv for a binding or resolver command line (run from the repo root): ``python3`` and
    bare ``.py`` scripts run with the current interpreter."""
    command = shlex.split(command_line)
    if command and command[0] == "python3":
        return [sys.executable, *command[1:]]
    if command and command[0].endswith(".py"):
        return [sys.executable, *command]
    return command
