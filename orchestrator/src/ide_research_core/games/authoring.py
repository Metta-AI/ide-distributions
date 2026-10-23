"""Make, check and install game folders: optimizer-seed's mixin method in this contract's terms.

optimizer-seed adds a game by installing a *mixin* (``tools/add_game.sh``: clone it, stamp its
provenance, vendor it, list what to review) against the contract in its ``games/_template/``.
Here a game folder is ``games/<game>/`` (``GAME.md``, binding scripts, ``seed/``, binding skills),
and:

- ``new`` copies the researcher's ``games/_template/`` scaffold into ``<out>/games/<game>/``
  and fills its placeholders. With a mixin snapshot (``sources.fetch``) it vendors the mixin in
  ``mixin/`` and installs the mixin's skills under ``skills/``, renamed ``<game>-<skill>``.
- ``check`` is the install gate. *Problems* block installation; *gaps* (a binding or skill
  still the template's stub, unfilled guide sections, host requirements this host lacks) are
  reported and do not, as optimizer-seed says of a missing binding: announce it, proceed, and
  warn that results are weaker. The rules are listed in ``games/_template/CONTRACT.md``.
- ``install`` copies a card's committed folder (``git archive`` of one commit) into the
  host's ``games/`` directory (``games.scope.installed_games_dir``), atomically, and records
  where it came from in ``INSTALLED.json``. Only the orchestrator runs it.
"""

import json
import os
import re
import shlex
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ide_research_core.games.loader import GameError, load_game
from ide_research_core.games.scope import (
    DISTRIBUTION_ROOT,
    check_game_name,
    host_root,
    installed_games_dir,
)
from ide_research_core.io.archive import ArchiveError, extract_git_archive
from ide_research_core.io.docs import read_doc, write_json
from ide_research_core.io.locks import file_lock

TEMPLATE = "_template"
STUB_MARKER = "research-template-stub"
REQUIRED_SECTIONS = ("Seats and opponents", "Starter policy")
KNOWLEDGE_BINDINGS = ("ab", "survey", "replay-inspection", "eval-design", "diagnosis")
SKILL_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")  # Hermes skill names
NOT_COPIED = ("CONTRACT.md",)  # the field reference stays with the template
MIXIN_SKIPPED = ("skills", ".github")
INSTALL_RECORD = "INSTALLED.json"


class AuthoringError(RuntimeError):
    def __init__(self, reason: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = details


# --- new ---


def _fill(text: str, values: Mapping[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _snapshot_row(snapshot: dict | None) -> str:
    return f"{snapshot['repo']} at `{snapshot['commit']}`" if snapshot else "none"


def _mixin_note(mixin: dict | None) -> str:
    if mixin is None:
        return "No mixin was installed: fill this folder from the game source."
    return (
        f"Made from the mixin {mixin['repo']} at `{mixin['commit']}`. Its files are in "
        "`mixin/` (start with `mixin/MIXIN.md` and `mixin/AGENTS.md`) and its skills are under "
        "`skills/`. Move its knowledge into this guide, the binding skills and the seed, and "
        "verify every mechanics claim against the game source."
    )


def _rename_skill(path: Path, name: str) -> None:
    """Set a copied skill's front matter ``name``, keeping the rest of the file (a skill with
    no ``name`` line is left for ``check`` to report)."""
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"(?m)^name:.*$", f"name: {name}", text, count=1), encoding="utf-8")


def _vendor_mixin(target: Path, game: str, snapshot: Path) -> list[str]:
    """``add_game.sh`` here: the mixin minus its skills into ``mixin/``; its skills into
    ``skills/``, renamed for this game. Returns the installed skill names."""
    shutil.copytree(
        snapshot,
        target / "mixin",
        ignore=lambda directory, names: [
            name for name in names if Path(directory) == snapshot and name in MIXIN_SKIPPED
        ],
    )
    installed = []
    skills = snapshot / "skills"
    for skill in sorted(skills.glob("*/SKILL.md")) if skills.is_dir() else []:
        folder = skill.parent.name
        if not SKILL_NAME.fullmatch(folder):
            continue
        shutil.copytree(skill.parent, target / "skills" / folder, dirs_exist_ok=True)
        _rename_skill(target / "skills" / folder / "SKILL.md", f"{game}-{folder}")
        installed.append(f"{game}-{folder}")
    return installed


def new(
    game: str,
    league_id: str,
    out: Path,
    *,
    title: str | None = None,
    source: dict | None = None,
    mixin: dict | None = None,
    template_root: Path = DISTRIBUTION_ROOT,
) -> dict:
    """Scaffold ``<out>/games/<game>/``. ``source`` and ``mixin`` are ``sources.fetch`` results."""
    check_game_name(game)
    template = template_root / "games" / TEMPLATE
    if not (template / "GAME.md").is_file():
        raise AuthoringError("setup", f"this profile ships no game template at {template}")
    target = Path(out) / "games" / game
    if target.exists():
        raise AuthoringError("exists", f"{target} already exists")
    if mixin is not None:
        # A link could make the copy or the skill rename write outside the new folder.
        snapshot = Path(mixin["path"])
        links = sorted(str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if p.is_symlink())
        if links:
            raise AuthoringError("invalid_mixin", f"the mixin holds symbolic links: {links[:5]}")
    shutil.copytree(template, target, ignore=shutil.ignore_patterns(*NOT_COPIED))
    values = {
        "GAME": game,
        "GAME_TITLE": title or game,
        "LEAGUE_ID": league_id,
        "SOURCE_REPO": source["repo"] if source else "",
        "SOURCE_ROW": _snapshot_row(source),
        "MIXIN_ROW": _snapshot_row(mixin),
        "MIXIN_NOTE": _mixin_note(mixin),
        "DATE": datetime.now(UTC).date().isoformat(),
    }
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.suffix in (".md", ".py"):
            path.write_text(_fill(path.read_text(encoding="utf-8"), values), encoding="utf-8")
    if source is None:  # ``source`` is optional in the contract; without a repository, omit it
        game_md = target / "GAME.md"
        lines = game_md.read_text(encoding="utf-8").splitlines(keepends=True)
        game_md.write_text("".join(line for line in lines if not line.startswith("source:")))
    skills = _vendor_mixin(target, game, Path(mixin["path"])) if mixin else []
    return {
        "path": str(target),
        "mixin_skills": skills,
        "source": source,
        "mixin": mixin,
        "check": check(game, Path(out)),
    }


# --- check ---


def _command_paths(game: str, label: str, command: str, root: Path, problems: list[str]) -> list[Path]:
    """The files of this game a binding or resolver command names, after checking them."""
    try:
        words = shlex.split(command)
    except ValueError as error:
        problems.append(f"{label}: cannot parse {command!r}: {error}")
        return []
    named = []
    for index, word in enumerate(words):
        if word.startswith("games/"):
            named.append(Path(word))
        elif word.startswith("games.") and index and words[index - 1] == "-m":
            named.append(Path(*word.split(".")))
    found = []
    folder = (root / "games" / game).resolve()
    for path in named:
        if not (root / path).resolve().is_relative_to(folder):
            problems.append(f"{label}: {command!r} names a path outside games/{game}/ ({path})")
            continue
        for candidate in (root / path, (root / path).with_suffix(".py")):
            if candidate.exists():
                found.append(candidate)
                break
        else:
            problems.append(f"{label}: {path} does not exist")
    if not named:
        problems.append(f"{label}: {command!r} names no file under games/{game}/")
    return found


def _has_stub(path: Path) -> bool:
    files = [path] if path.is_file() else sorted(path.rglob("*.py")) if path.is_dir() else []
    return any(STUB_MARKER in file.read_text(encoding="utf-8", errors="replace") for file in files)


def _stub_sections(body: str) -> list[str]:
    sections = re.split(r"(?m)^## ", body)
    return [part.splitlines()[0].strip() for part in sections[1:] if STUB_MARKER in part]


def _check_skills(game: str, folder: Path, problems: list[str], gaps: list[str]) -> list[str]:
    names = []
    for path in sorted(folder.rglob("SKILL.md")):
        relative = path.relative_to(folder)
        if len(relative.parts) != 3 or relative.parts[0] != "skills":
            problems.append(f"{relative}: a skill must be at skills/<skill>/SKILL.md")
            continue
        meta, _ = read_doc(path)
        wanted = f"{game}-{relative.parts[1]}"
        if meta.get("name") != wanted or not SKILL_NAME.fullmatch(wanted):
            problems.append(f"{relative}: name must be {wanted!r}, not {meta.get('name')!r}")
        if not str(meta.get("description") or "").strip():
            problems.append(f"{relative}: description is empty")
        names.append(wanted)
    for binding in KNOWLEDGE_BINDINGS:
        path = folder / "skills" / binding / "SKILL.md"
        if not path.is_file():
            gaps.append(f"binding skill {game}-{binding} is missing")
        elif STUB_MARKER in path.read_text(encoding="utf-8"):
            gaps.append(f"binding skill {game}-{binding} is still the template")
    return names


def _host_missing(game, env: Mapping[str, str]) -> dict:
    variables = [name for name in game.host_env if not env.get(name)]
    tools = [name for name in game.host_tools if shutil.which(name, path=env.get("PATH")) is None]
    return {"env": variables, "tools": tools}


def check(game: str, root: Path, env: Mapping[str, str] = os.environ) -> dict:
    """Problems and gaps of ``<root>/games/<game>/`` (module docstring)."""
    problems: list[str] = []
    gaps: list[str] = []
    check_game_name(game)
    folder = Path(root) / "games" / game
    report = {"name": game, "path": str(folder), "problems": problems, "gaps": gaps}
    if not (folder / "GAME.md").is_file():
        problems.append(f"{folder}/GAME.md does not exist")
        return {**report, "ok": False}
    text = (folder / "GAME.md").read_text(encoding="utf-8")
    front = text.split("\n---", 1)[0]
    if "{{" in front:
        problems.append("GAME.md front matter still holds a {{...}} placeholder")
    meta, body = read_doc(folder / "GAME.md")
    if meta.get("name") != game:
        problems.append(f"GAME.md name is {meta.get('name')!r}, not the folder's {game!r}")
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            problems.append(f"{path.relative_to(folder)} is a symbolic link")
    if (folder / INSTALL_RECORD).exists():
        problems.append(f"{INSTALL_RECORD} is reserved: installation writes it")
    contract = None
    try:
        contract = load_game(Path(root), game)
    except GameError as error:
        problems.append(str(error))
    if contract is not None:
        for binding, command in contract.bindings.items():
            for path in _command_paths(game, f"bindings.{binding}", command, Path(root), problems):
                if _has_stub(path):
                    gaps.append(f"binding {binding} is still the template stub")
        for kind, command in contract.reference_types.items():
            _command_paths(game, f"reference_types.{kind}", command, Path(root), problems)
        report["host_missing"] = _host_missing(contract, env)
        report["contract"] = {
            "league_id": contract.league_id,
            "bindings": contract.bindings,
            "reference_types": contract.reference_types,
            "outcome_metrics": [metric.name for metric in contract.outcome_metrics],
            "host_requirements": {"env": contract.host_env, "tools": contract.host_tools},
            "source": contract.source,
        }
    for section in REQUIRED_SECTIONS:
        if not re.search(rf"(?m)^## {section}\s*$", body):
            problems.append(f"GAME.md has no `## {section}` section")
    gaps.extend(f"GAME.md section `{name}` is still the template" for name in _stub_sections(body))
    for seed in ("plan.md", "strategy/STRATEGY.md"):
        path = folder / "seed" / seed
        if not path.is_file():
            problems.append(f"seed/{seed} does not exist")
        elif STUB_MARKER in path.read_text(encoding="utf-8"):
            gaps.append(f"seed/{seed} is still the template")
    report["skills"] = _check_skills(game, folder, problems, gaps)
    files = [path for path in folder.rglob("*") if path.is_file() and not path.is_symlink()]
    report["files"] = len(files)
    report["bytes"] = sum(path.stat().st_size for path in files)
    return {**report, "ok": not problems}


# --- install ---


def _installed(target: Path) -> dict:
    record = target / INSTALL_RECORD
    return json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}


def _write_receipt(receipt: Path | None, result: dict) -> dict:
    if receipt is not None:
        write_json(receipt, result)
    return result


def _read_receipt(receipt: Path | None) -> dict | None:
    """A readable receipt, or None (an unreadable one falls back to ``INSTALLED.json``)."""
    try:
        return json.loads(receipt.read_text(encoding="utf-8")) if receipt else None
    except (OSError, ValueError):
        return None


def _receipt_path(host: Path, game: str, card: str | None, run_id: int | None) -> Path | None:
    if card is None or run_id is None:
        return None
    return host / "game-history" / game / "receipts" / f"{card}-{run_id}.json"


def install(
    game: str,
    worktree: Path,
    commit: str,
    *,
    env: Mapping[str, str] = os.environ,
    replace: bool = False,
    card: str | None = None,
    run_id: int | None = None,
) -> dict:
    """Install the committed ``games/<game>/`` of ``worktree`` at ``commit`` on this host.

    A card run installs once: its receipt (``game-history/<game>/receipts/<card>-<run>.json``,
    written for every success) answers a retry, so a late retry never undoes a newer
    installation; an installation whose receipt was not written yet (``INSTALLED.json`` names
    the same card run) counts as done. A replacement keeps
    the previous folder in ``game-history/<game>/`` and puts it back if the swap fails."""
    check_game_name(game)
    if (DISTRIBUTION_ROOT / "games" / game / "GAME.md").is_file():
        raise AuthoringError("shipped", f"{game!r} ships with the distribution; install another name")
    host = host_root(env)
    target = installed_games_dir(env) / game
    receipt = _receipt_path(host, game, card, run_id)
    staging = host / ".game-staging"
    staging.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(host / ".game-install.lock"), tempfile.TemporaryDirectory(dir=staging) as tmp:
        answered = _read_receipt(receipt)
        if answered is not None:
            return {**answered, "already_installed": True}
        current = _installed(target)
        if card is not None and (current.get("card"), current.get("run_id")) == (card, run_id):
            return _write_receipt(
                receipt, {"installed": str(target), "already_installed": True, "record": current}
            )
        try:
            extract_git_archive(
                ["-C", str(worktree), "archive", "--format=tar", commit, f"games/{game}"], Path(tmp)
            )
        except ArchiveError as error:
            raise AuthoringError("invalid_archive", f"games/{game} at {commit}: {error}") from None
        report = check(game, Path(tmp), env)
        report["path"] = str(target)
        if not report["ok"]:
            raise AuthoringError(
                "invalid_game", f"games/{game} at {commit} fails the game checks", report
            )
        if target.exists():
            if current.get("commit") == commit:
                return _write_receipt(
                    receipt, {"installed": str(target), "already_installed": True, "check": report}
                )
            if not replace:
                raise AuthoringError(
                    "already_installed",
                    f"{game!r} is installed from {current.get('commit')}; pass replace to "
                    "install this commit instead",
                )
        staged = Path(tmp) / "games" / game
        record = {
            "game": game,
            "commit": commit,
            "card": card,
            "run_id": run_id,
            "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        (staged / INSTALL_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        replaced = None
        if target.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            replaced = host / "game-history" / game / f"{stamp}-{uuid.uuid4().hex[:8]}"
            replaced.parent.mkdir(parents=True, exist_ok=True)
            os.rename(target, replaced)
        try:
            os.rename(staged, target)
        except OSError:
            if replaced is not None:
                os.rename(replaced, target)
            raise
        result = {
            "installed": str(target),
            "already_installed": False,
            "replaced": str(replaced) if replaced else None,
            "record": record,
            "check": report,
        }
        return _write_receipt(receipt, result)
