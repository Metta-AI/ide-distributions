"""Coworld switching, source fetch, mixin import and lesson import (design B.11, B.14, D.2, D.4).

``switch`` is what ``workspace.coworld_switch`` and ``research coworld switch`` run: it makes a
Coworld the active one in the registry, prepares its folder and code repository, fetches its
source and mixin when it was never set up, emits ``coworld.setup_needed`` for the setup wake,
and points every profile's skill directories, trusted directory and terminal at the Coworld
(amendments A4.8, A5.9, A6.3). ``fetch`` clones the release-pinned source once, copies the
mixin into ``code/mixin/`` with the Coworld name prefixed to each skill (so a repository's
``survey`` never shadows the core ``survey``), installs declared toolchains and imports the
seed claims and lessons as Claims with ``doc`` evidence (D.4). A mixin that declares another
Coworld is refused and nothing is copied (A4.3).

Runs in the workspace environment: PyYAML is imported inside the functions that parse YAML, so
the module itself imports anywhere (the tool catalog loads it inside Hermes to build schemas).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import research_core
from research_core import environment_guide, gitops, layout, mixin, prepare
from research_core.locks import file_lock
from research_core.platform.errors import PlatformError
from research_core.store import claims, coworlds, events, notices
from research_core.store.db import Store
from research_core.store.errors import StoreError
from research_core.tools.base import ToolError

if TYPE_CHECKING:  # pragma: no cover
    from research_core.layout import Coworld, Workspace
    from research_core.tools.base import ToolContext

SETUP_EVENT = "coworld.setup_needed"
IMPORT_NOTE = "imported from the Coworld repository"
SUPPORTED_TOOLCHAINS = ("python",)
SKILL_FILE = "SKILL.md"
UPSTREAM_SHA_FILE = ".upstream-sha"
CITATIONS_FILE = "source-citations.json"
COWORLD_MD_CHARS = 4000
EXCERPT_CHARS = 500
PRUNED_DIRS = frozenset({".git", "__pycache__", "node_modules"})

Runner = Callable[..., subprocess.CompletedProcess]


# --- switch -----------------------------------------------------------------------------------


def switch(
    ctx: ToolContext,
    coworld_id: str,
    *,
    cites: str | None = None,
    source: str | None = None,
    commit: str | None = None,
    pending_request: dict[str, Any] | None = None,
    drain: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Make ``coworld_id`` the active Coworld and start its setup when it needs one.

    In order: ``drain()`` when the watcher supplies one (Researcher runs stop and resume after
    the switch); register and activate the registry row; create the folder and the code
    repository (``main`` with an empty first commit); when the Coworld was never set up, store
    ``pending_request``, ``fetch`` the source and mixin and emit ``coworld.setup_needed`` unless
    one is already waiting for the watcher; apply the profile config; and with ``cites`` (a
    user's request) add the notice that running work pauses. ``source`` and ``commit`` override
    the platform manifest (tests, private repositories).
    """
    try:
        folder = layout.folder_name_for(coworld_id)
    except ValueError as error:
        raise ToolError(str(error)) from None
    if pending_request is not None and not isinstance(pending_request, dict):
        raise ToolError("pending_request must be an object such as {text, cites}")
    result: dict[str, Any] = {"coworld_id": coworld_id}
    if drain is not None:
        result["drained"] = drain()

    wstore, actor = ctx.wstore, ctx.actor
    previous = coworlds.active(wstore)
    coworlds.register(wstore, actor, coworld_id, folder=folder)
    row = coworlds.activate(wstore, actor, coworld_id)
    _forget_coworld_store(ctx)
    result["switched_from"] = None if previous is None else previous["coworld_id"]

    coworld = ctx.workspace.coworld(coworld_id)
    coworld.ensure_dirs()
    initialized = not (coworld.code / ".git").exists()
    gitops.init_repo(coworld.code)
    result["code"] = {"path": str(coworld.code), "initialized": initialized}

    if pending_request is not None:
        row = coworlds.set_pending_request(wstore, actor, coworld_id, pending_request)
    result["setup"] = _setup_step(ctx, coworld, row, cites=cites, source=source, commit=commit)
    result["profile_config"] = apply_profile_config(
        ctx, coworld, mixin_skills_dir=coworld.mixin / "skills"
    )
    result["skill_overrides"] = record_skill_overrides(coworld.code)
    if cites:
        notice = notices.add(
            wstore,
            actor,
            text=f"Switching to {coworld_id}; running work pauses and resumes on switching back.",
            refs=[cites],
        )
        result["notice"] = notice["id"]
    result["coworld"] = coworlds.read(wstore, coworld_id)
    return result


def _setup_step(
    ctx: ToolContext,
    coworld: Coworld,
    row: dict[str, Any],
    *,
    cites: str | None,
    source: str | None,
    commit: str | None,
) -> dict[str, Any]:
    """Step 4 of ``switch``: nothing for a set-up Coworld; otherwise fetch and emit the wake.

    A second switch while the first ``coworld.setup_needed`` is still unhandled emits nothing
    and fetches nothing: the wake that is coming reads the registry row, which already carries
    the latest ``pending_request``.
    """
    if row["set_up_at"]:
        return {"status": "set_up"}
    pending_seq = pending_setup_seq(ctx.wstore, coworld.coworld_id)
    if pending_seq is not None:
        return {
            "status": "pending",
            "event_seq": pending_seq,
            "note": "setup is already waiting for the watcher; the pending request was updated",
        }
    fetched = fetch(ctx, coworld.coworld_id, source=source, commit=commit)
    payload = {
        "coworld_id": coworld.coworld_id,
        "mixin": "found" if fetched["mixin"] is not None else "missing",
        "source": {"url": fetched["source"]["url"], "commit": fetched["source"]["commit"]},
        "lessons": fetched["lessons"],
        "pending_request": row["pending_request"],
        "cites": cites,
        "problems": fetched["problems"],
    }
    with ctx.wstore.tx(ctx.actor) as tx:
        seq = tx.event(SETUP_EVENT, coworld.coworld_id, payload)
    return {"status": "needed", "event_seq": seq, "fetch": fetched}


def pending_setup_seq(wstore: Store, coworld_id: str) -> int | None:
    """The seq of an unhandled ``coworld.setup_needed`` event for ``coworld_id``, if any."""
    for event in events.unhandled(wstore, [SETUP_EVENT]):
        if event.subject == coworld_id:
            return event.seq
    return None


def _forget_coworld_store(ctx: ToolContext) -> None:
    """The active Coworld changed: drop the context's cached Coworld store (a cached_property)."""
    store = ctx.__dict__.pop("store", None)
    if store is not None:
        store.close()


def _coworld_store(ctx: ToolContext, coworld: Coworld) -> Store:
    active = coworlds.active(ctx.wstore)
    if active is not None and active["coworld_id"] == coworld.coworld_id:
        return ctx.store
    return Store.open(coworld.store_path, kind="coworld")


# --- fetch ------------------------------------------------------------------------------------


def fetch(
    ctx: ToolContext, coworld_id: str, *, source: str | None = None, commit: str | None = None
) -> dict[str, Any]:
    """Clone the Coworld's source, import its mixin and seeds, commit on ``main``.

    The source URL and commit come from the platform manifest (``game.runnable``) unless
    overridden. Returns what was found: ``mixin`` (None when there is none or it was refused),
    ``source``, ``coworld_md``, ``lessons`` (import counts), ``toolchains``, ``commit`` (the
    import commit, None when nothing changed) and ``problems``. Never raises for a bad
    repository: every shortfall is a problem the setup wake reads.
    """
    coworld = ctx.workspace.coworld(coworld_id)
    coworld.ensure_dirs()
    gitops.init_repo(coworld.code)
    problems: list[str] = []
    manifest = _manifest(ctx, coworld_id, problems)
    runnable = _runnable(manifest)
    url = source or runnable.get("source_url")
    sha = commit or runnable.get("commit")
    name = (manifest or {}).get("name") or coworld_id
    result: dict[str, Any] = {
        "coworld_id": coworld_id,
        "name": name,
        "source": {"url": url, "commit": sha, "path": str(coworld.source)},
        "coworld_md": None,
        "mixin": None,
        "lessons": _empty_counts(),
        "toolchains": [],
        "commit": None,
        "problems": problems,
    }
    if not url:
        problems.append(
            "no source repository: the Coworld manifest names no source_url and none was given"
        )
        return result
    try:
        with file_lock(coworld.state / "source.lock"):
            checked_out = _ensure_source(url, coworld.source, sha)
    except gitops.GitError as error:
        problems.append(f"cloning {url}: {error}")
        return result
    result["source"]["commit"] = checked_out
    result["coworld_md"] = _coworld_md(coworld.source)

    mixin_dir = find_mixin(coworld.source)
    if mixin_dir is None:
        problems.append(
            "no MIXIN.md in the source repository; the setup wake builds a mixin from scratch"
        )
    else:
        relative = mixin_dir.relative_to(coworld.source).as_posix() or "."
        loaded = _load_mixin(mixin_dir, relative, problems)
        if loaded is not None and loaded.coworld != coworld_id:
            problem = f"MIXIN.md declares {loaded.coworld}, workspace is {coworld_id}"
            problems.append(problem)
            result["problem"] = problem
            return result
        if loaded is not None:
            copied = copy_mixin(mixin_dir, coworld.mixin, coworld_name=name)
            problems.extend(copied["problems"])
            result["mixin"] = {
                "source_dir": relative,
                "path": str(coworld.mixin),
                "coworld": loaded.coworld,
                "coworld_version": loaded.coworld_version,
                "commands": sorted(loaded.commands),
                "artifact": loaded.artifact,
                "skills": copied["skills"],
            }
            installed = install_toolchains(ctx.workspace, loaded.toolchains)
            result["toolchains"] = installed["toolchains"]
            problems.extend(installed["problems"])

    store = _coworld_store(ctx, coworld)
    result["lessons"] = import_lessons(ctx, coworld.source, store=store)
    if gitops.dirty_files(coworld.code):
        message = f"Import mixin from {url}@{checked_out}"
        result["commit"] = gitops.commit_all(coworld.code, message)
    return result


def _manifest(ctx: ToolContext, coworld_id: str, problems: list[str]) -> dict[str, Any] | None:
    try:
        manifest = ctx.platform().coworld_manifest(coworld_id)
    except PlatformError as error:
        problems.append(
            f"the platform has no manifest for {coworld_id}: {error.detail}"
            f" ({error.route or 'coworld_manifest'} {error.status})"
        )
        return None
    return manifest if isinstance(manifest, dict) else None


def _runnable(manifest: dict[str, Any] | None) -> dict[str, Any]:
    game = ((manifest or {}).get("manifest") or {}).get("game") or {}
    runnable = game.get("runnable") if isinstance(game, dict) else None
    return runnable if isinstance(runnable, dict) else {}


def _ensure_source(url: str, dest: Path, sha: str | None) -> str:
    """The clone at ``dest`` detached at ``sha`` (or the default branch tip when no commit is
    pinned); an existing clone already at that commit is left alone."""
    # Coworld source_url is provenance: GitHub tree/blob URLs name a repository revision,
    # not a clone endpoint. Keep the revision when converting it to a Git repository URL.
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.hostname in {"github.com", "www.github.com"}
        and len(parts) >= 4
        and parts[2] in {"tree", "blob", "commit"}
    ):
        url = f"https://github.com/{parts[0]}/{parts[1].removesuffix('.git')}.git"
        sha = sha or parts[3]
    head = _head(dest)
    if head is not None and sha and head.startswith(sha):
        return head
    if sha:
        return gitops.clone_pinned(url, dest, sha)
    if head is None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        gitops.git(None, "clone", "--quiet", "--depth=1", "--no-tags", url, str(dest))
    return gitops.rev_parse(dest, "HEAD")


def _head(repo: Path) -> str | None:
    if not (repo / ".git").exists():
        return None
    result = gitops.git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    return result.stdout.strip() or None if result.returncode == 0 else None


def _coworld_md(source: Path) -> dict[str, Any] | None:
    path = source / "COWORLD.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"path": "COWORLD.md", "chars": len(text), "text": text[:COWORLD_MD_CHARS]}


def find_mixin(source: Path) -> Path | None:
    """The directory holding the shallowest ``MIXIN.md`` under ``source`` (ties: by path)."""
    source = Path(source)
    found: list[tuple[int, str, Path]] = []
    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in PRUNED_DIRS)
        if mixin.FRONT_MATTER_FILE in files:
            directory = Path(root)
            depth = len(directory.relative_to(source).parts)
            found.append((depth, directory.as_posix(), directory))
    return min(found)[2] if found else None


def _load_mixin(mixin_dir: Path, relative: str, problems: list[str]) -> mixin.Mixin | None:
    try:
        return mixin.load(mixin_dir)
    except mixin.MixinError as error:
        problems.extend(f"{relative}/{mixin.FRONT_MATTER_FILE}: {p}" for p in error.problems)
        return None


def copy_mixin(mixin_dir: Path, dest: Path, *, coworld_name: str) -> dict[str, Any]:
    """Copy the mixin folder to ``dest`` (rsync-like: files are overwritten, extra files kept,
    ``.git`` skipped) with each ``skills/<name>/`` landing as ``skills/<coworld_name>-<name>/``
    and its ``SKILL.md`` ``name:`` rewritten to match (design D.2, Q16)."""
    mixin_dir, dest = Path(mixin_dir).resolve(), Path(dest)

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {n for n in names if n in PRUNED_DIRS}
        if Path(directory).resolve() == mixin_dir:
            skipped.add("skills")
        return skipped

    shutil.copytree(mixin_dir, dest, ignore=ignore, dirs_exist_ok=True)
    skills: list[dict[str, str]] = []
    problems: list[str] = []
    source_skills = mixin_dir / "skills"
    if source_skills.is_dir():
        for skill_dir in sorted(p for p in source_skills.iterdir() if p.is_dir()):
            prefix = f"{coworld_name}-"
            name = skill_dir.name
            prefixed = name if name.startswith(prefix) else prefix + name
            target = dest / "skills" / prefixed
            shutil.copytree(
                skill_dir,
                target,
                ignore=shutil.ignore_patterns(*PRUNED_DIRS),
                dirs_exist_ok=True,
            )
            problem = _rename_skill(target / SKILL_FILE, prefixed)
            if problem:
                problems.append(problem)
            skills.append({"from": name, "to": prefixed})
    return {"skills": skills, "problems": problems}


def _rename_skill(skill_file: Path, new_name: str) -> str | None:
    """Set ``name: <new_name>`` in the skill's front matter; the problem when that is not
    possible (the files are still copied, Hermes shows them under their old name)."""
    label = f"skills/{skill_file.parent.name}/{SKILL_FILE}"
    if not skill_file.is_file():
        return f"{label}: missing; the skill has no front matter to rename"
    text = skill_file.read_text(encoding="utf-8")
    front, body, problem = mixin.split_front_matter(text)
    if problem:
        return f"{label}: {problem}; name not rewritten"
    new_front, count = re.subn(r"(?m)^name:[^\n]*$", f"name: {new_name}", front, count=1)
    if count == 0:
        new_front = f"name: {new_name}\n{front}"
    skill_file.write_text(f"---\n{new_front}---\n{body}", encoding="utf-8")
    return None


def install_toolchains(
    workspace: Workspace, toolchains: list[dict[str, Any]], *, run: Runner = subprocess.run
) -> dict[str, Any]:
    """Install the toolchains a mixin declares under ``<workspace>/tools/<kind>-<version>/``.

    Supported: ``python`` (``uv python install <version>`` then a venv at that path). Other
    kinds are recorded as problems for the setup wake to handle by hand (a ``nim`` tarball URL
    with a sha256 is the planned next kind). A toolchain already present is left alone.
    """
    results: list[dict[str, Any]] = []
    problems: list[str] = []
    for toolchain in toolchains:
        kind, version = toolchain.get("kind"), toolchain.get("version")
        label = f"toolchain {kind}" + (f" {version}" if version else "")
        if kind not in SUPPORTED_TOOLCHAINS:
            problems.append(
                f"{label}: setup installs only {', '.join(SUPPORTED_TOOLCHAINS)}; install it "
                f"under {workspace.tools} by hand"
            )
            results.append({"kind": kind, "version": version, "status": "unsupported"})
            continue
        if not version:
            problems.append(f"{label}: a version is required")
            results.append({"kind": kind, "version": None, "status": "failed"})
            continue
        target = workspace.tools / f"{kind}-{version}"
        entry = {"kind": kind, "version": version, "path": str(target), "status": "present"}
        if not (target / "bin" / "python").exists():
            problem = _install_python(version, target, run)
            if problem:
                problems.append(f"{label}: {problem}")
            entry["status"] = "failed" if problem else "installed"
        results.append(entry)
    return {"toolchains": results, "problems": problems}


def _install_python(version: str, target: Path, run: Runner) -> str | None:
    target.parent.mkdir(parents=True, exist_ok=True)
    for argv in (
        ["uv", "python", "install", version],
        ["uv", "venv", "--python", version, str(target)],
    ):
        try:
            done = run(argv, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return "uv not found on PATH; it is needed to install a Python toolchain"
        if done.returncode != 0:
            return (
                f"`{' '.join(argv)}` failed with exit code {done.returncode}\n"
                f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}"
            )
    return None


# --- lesson import (D.4) ----------------------------------------------------------------------


class _SeedProblem(Exception):
    pass


def _empty_counts() -> dict[str, Any]:
    return {"claims": 0, "lessons": 0, "skipped": [], "created": []}


def import_lessons(ctx: ToolContext, source_dir: Path, *, store: Store | None = None) -> dict:
    """Import every optimizer-seed ``seed/`` folder under ``source_dir`` as Claims.

    Claims (``kb/claims/*.md`` or ``claims/*.md``): the first body sentence is the summary, the
    rest plus a "What would change our mind" paragraph from ``revise_if`` is the body, ``kind``
    and ``topics`` become tags with ``imported``. Lessons (``lessons/*.md``): the ``lesson``
    sentence with tags ``lesson, imported``. Every Claim cites its seed file as ``doc`` evidence
    and, for each seed citation found in ``source-citations.json``, the cited source lines with
    their excerpt. Deterministic and idempotent: a summary already in the store is skipped.
    One bad file never fails the import; it lands in ``skipped`` with its reason.
    """
    store = ctx.store if store is None else store
    source_dir = Path(source_dir)
    counts = _empty_counts()
    for seed in _seed_dirs(source_dir):
        citations = _citations(seed, counts["skipped"], source_dir)
        claim_files = sorted(seed.glob("kb/claims/*.md")) + sorted(seed.glob("claims/*.md"))
        for file in claim_files:
            _import_seed_file(store, ctx.actor, file, source_dir, citations, "claim", counts)
        for file in sorted(seed.glob("lessons/*.md")):
            _import_seed_file(store, ctx.actor, file, source_dir, citations, "lesson", counts)
    return counts


def _seed_dirs(source_dir: Path) -> list[Path]:
    found: list[Path] = []
    for root, dirs, _files in os.walk(source_dir):
        dirs[:] = sorted(d for d in dirs if d not in PRUNED_DIRS)
        if Path(root).name == "seed":
            found.append(Path(root))
            dirs[:] = []
    return found


def _citations(seed: Path, skipped: list[dict], source_dir: Path) -> dict[str, dict]:
    citations: dict[str, dict] = {}
    for file in sorted(seed.glob(f"**/{CITATIONS_FILE}")):
        try:
            loaded = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            skipped.append({"file": _relative(file, source_dir), "reason": f"not JSON: {error}"})
            continue
        if isinstance(loaded, dict):
            citations.update({k: v for k, v in loaded.items() if isinstance(v, dict)})
    return citations


def _import_seed_file(
    store: Store,
    actor,
    file: Path,
    source_dir: Path,
    citations: dict[str, dict],
    kind: str,
    counts: dict[str, Any],
) -> None:
    relative = _relative(file, source_dir)
    try:
        meta, body, body_line = _parse_seed(file)
    except _SeedProblem as error:
        counts["skipped"].append({"file": relative, "reason": str(error)})
        return
    if kind == "claim":
        summary, rest = _first_sentence(body)
        topics = meta.get("topics") if isinstance(meta.get("topics"), list) else []
        tags = [meta.get("kind"), *topics, "imported"]
        if meta.get("revise_if"):
            rest = f"{rest}\n\nWhat would change our mind: {meta['revise_if']}".strip()
        cited = [
            (e.get("ref"), e.get("stance"))
            for e in (meta.get("evidence") or [])
            if isinstance(e, dict) and e.get("ref")
        ]
    else:
        lesson = meta.get("lesson")
        summary, rest = _first_sentence(body)
        if isinstance(lesson, str) and lesson.strip():
            summary = lesson.strip()
            rest = body.removeprefix(summary).strip()
        tags = ["lesson", "imported"]
        cited = [(ref, "supports") for ref in (meta.get("scar") or []) if isinstance(ref, str)]
    if not summary:
        counts["skipped"].append({"file": relative, "reason": "empty body"})
        return
    summary = _cap_words(summary, claims.SUMMARY_WORDS)
    existing = store.one("SELECT id FROM claims WHERE summary = ?", (summary,))
    if existing is not None:
        reason = f"already imported as C-{existing['id']}"
        counts["skipped"].append({"file": relative, "reason": reason})
        return
    version = meta.get("last_checked_version")
    note = f"{IMPORT_NOTE} (seed {meta.get('id') or file.stem}"
    note += f"; last checked Coworld version {version})" if version is not None else ")"
    evidence = [
        {"type": "doc", "ref": f"{relative}#L{body_line}", "stance": "supports", "note": note}
    ]
    evidence += [_citation_evidence(ref, stance, citations) for ref, stance in cited]
    try:
        record = claims.write(
            store,
            actor,
            summary=summary,
            body=_cap_words(rest, claims.BODY_WORDS),
            tags=[str(t).strip() for t in tags if isinstance(t, str | int) and str(t).strip()],
            evidence=[link for link in evidence if link is not None],
        )
    except StoreError as error:
        counts["skipped"].append({"file": relative, "reason": "; ".join(error.problems)})
        return
    counts[f"{kind}s"] += 1
    counts["created"].append(record["id"])


def _citation_evidence(ref: str, stance: Any, citations: dict[str, dict]) -> dict | None:
    citation = citations.get(ref)
    if citation is None:
        return None
    path = citation.get("path") or str(ref).split(":")[0]
    start, end = citation.get("start"), citation.get("end")
    section = f"L{start}-L{end}" if start and end else "cited"
    commit = str(citation.get("commit") or "")[:12]
    excerpt = str(citation.get("text") or "").strip()[:EXCERPT_CHARS]
    note = (
        f"{IMPORT_NOTE}" + (f" at {commit}" if commit else "") + (f": {excerpt}" if excerpt else "")
    )
    return {
        "type": "doc",
        "ref": f"{path}#{section}",
        "stance": stance if stance in claims.STANCES else "supports",
        "note": note,
    }


def _parse_seed(file: Path) -> tuple[dict[str, Any], str, int]:
    """(front matter, body, 1-based line of the body's first non-empty line)."""
    import yaml

    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _SeedProblem(f"unreadable: {error}") from None
    front, body, problem = mixin.split_front_matter(text)
    if problem:
        raise _SeedProblem(problem)
    try:
        meta = yaml.safe_load(front)
    except yaml.YAMLError as error:
        raise _SeedProblem(f"front matter is not valid YAML: {error}") from None
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise _SeedProblem("front matter must be a mapping")
    body_lines = body.splitlines()
    leading = next((i for i, line in enumerate(body_lines) if line.strip()), len(body_lines))
    body_line = front.count("\n") + 3 + leading
    return meta, body.strip(), body_line


_SENTENCE = re.compile(r"^(?P<first>.*?[.!?])(?:\s+|$)(?P<rest>.*)$", re.DOTALL)


def _first_sentence(text: str) -> tuple[str, str]:
    text = text.strip()
    match = _SENTENCE.match(text)
    if match is None:
        return text, ""
    return match["first"].strip(), match["rest"].strip()


def _cap_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]) + "…"


def _relative(file: Path, root: Path) -> str:
    try:
        return file.relative_to(root).as_posix()
    except ValueError:
        return file.as_posix()


# --- profile config (A5.9) -------------------------------------------------------------------


def apply_profile_config(
    ctx: ToolContext, coworld: Coworld, *, mixin_skills_dir: Path, run: Runner = subprocess.run
) -> dict[str, Any]:
    """Point every profile at the active Coworld: ``skills.external_dirs`` (core common, core
    role, the mixin's skills), ``skills.trusted_project_dirs`` and ``terminal.cwd`` (the code
    repository), in this profile and every ``IDE_RESEARCHER_PROFILES`` sibling (A5.9).

    Uses ``prepare.HermesCli`` (``hermes config get|set`` with ``HERMES_HOME`` per profile).
    Without ``HERMES_HOME`` (tests, a bare CLI run) nothing is changed and the note says so.
    Defers to ``prepare.apply_skill_dirs`` once a later preparation module provides it.
    """
    env = ctx.env
    code_dir = coworld.code
    if not env.get("HERMES_HOME"):
        return {"applied": [], "problems": [], "note": "HERMES_HOME is not set; skipped"}
    hermes = env.get("HERMES_BIN") or shutil.which("hermes")
    if not hermes:
        note = "hermes executable not found (set HERMES_BIN or put hermes on PATH); skipped"
        return {"applied": [], "problems": [], "note": note}
    profile_home = Path(env["HERMES_HOME"])
    upstream = getattr(prepare, "apply_skill_dirs", None)
    if upstream is not None:
        return upstream(
            profile_home,
            hermes,
            mixin_skills_dir=mixin_skills_dir,
            code_dir=code_dir,
            env=env,
            run=run,
        )
    core_src = _core_src(env)
    applied: list[str] = []
    problems: list[str] = []
    for profile, role in _profile_targets(profile_home, ctx.role, env):
        if not profile.is_dir():
            problems.append(f"profile {profile.name}: no profile home at {profile}")
            continue
        cli = prepare.HermesCli(hermes, profile, env, run)
        desired = {
            "skills.external_dirs": [
                *prepare.skill_dirs_for(core_src, role),
                str(mixin_skills_dir),
            ],
            "skills.trusted_project_dirs": [str(code_dir)],
            "terminal.cwd": str(code_dir),
        }
        try:
            for key, value in desired.items():
                if cli.config_get(key) != value:
                    cli.config_set(key, value)
                    applied.append(f"{profile.name}: set {key}")
        except prepare.PrepareError as error:
            problems.append(f"profile {profile.name}: {error}")
    return {"applied": applied, "problems": problems}


def _profile_targets(profile_home: Path, role: str, env) -> list[tuple[Path, str]]:
    names = [n.strip() for n in (env.get("IDE_RESEARCHER_PROFILES") or "researcher").split(",")]
    siblings = [profile_home.parent / n for n in names if n]
    targets = [(profile_home, role)]
    targets += [(p, "researcher") for p in siblings if p.resolve() != profile_home.resolve()]
    return targets


def _core_src(env) -> Path:
    checkout = environment_guide.core_checkout(env)
    if checkout is not None:
        return checkout / "core" / "src"
    return Path(research_core.__file__).resolve().parents[1]


# --- skill overrides (A7.3) ------------------------------------------------------------------


def core_skill_sha(name: str) -> str | None:
    """sha256 of the core skill's ``SKILL.md`` (the file Hermes reads), or None when the core
    ships no skill of that name (the skills folder may not exist yet)."""
    root = Path(research_core.__file__).parent / "skills"
    matches = sorted(root.glob(f"**/{name}/{SKILL_FILE}")) if root.is_dir() else []
    if not matches:
        return None
    return hashlib.sha256(matches[0].read_bytes()).hexdigest()


def skill_overrides(code: Path) -> list[dict[str, Any]]:
    """Every override under ``code/.agents/skills/<name>/`` with the upstream sha it was made
    against and whether the core skill changed since (``stale``)."""
    overrides_dir = Path(code) / ".agents" / "skills"
    if not overrides_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for directory in sorted(p for p in overrides_dir.iterdir() if p.is_dir()):
        marker = directory / UPSTREAM_SHA_FILE
        recorded = marker.read_text().strip() if marker.is_file() else None
        current = core_skill_sha(directory.name)
        out.append(
            {
                "name": directory.name,
                "upstream_sha": recorded or None,
                "core_sha": current,
                "stale": bool(recorded) and current is not None and recorded != current,
            }
        )
    return out


def record_skill_overrides(code: Path) -> list[dict[str, Any]]:
    """Write ``.upstream-sha`` for overrides that lack one (the core skill as it is now), then
    report every override. An existing marker is kept: it records what the override was based
    on, which is what makes ``stale`` meaningful."""
    for override in skill_overrides(code):
        if override["upstream_sha"] is None and override["core_sha"] is not None:
            marker = Path(code) / ".agents" / "skills" / override["name"] / UPSTREAM_SHA_FILE
            marker.write_text(override["core_sha"] + "\n")
    return skill_overrides(code)


# --- status -----------------------------------------------------------------------------------


def setup_status(ctx: ToolContext) -> dict[str, Any]:
    """Where the active Coworld's setup stands: the registry row, source and mixin presence,
    the declared commands, capability status and the pending request."""
    from research_core.store import capabilities

    row = coworlds.active(ctx.wstore)
    if row is None:
        return {
            "coworld": None,
            "note": "no active Coworld; workspace.coworld_switch or research coworld switch",
        }
    coworld = ctx.workspace.coworld(row["coworld_id"])
    return {
        "coworld": row,
        "folder": str(coworld.root),
        "code_repo": (coworld.code / ".git").exists(),
        "source": {"present": _head(coworld.source) is not None, "commit": _head(coworld.source)},
        "mixin": _mixin_status(coworld),
        "capabilities": capabilities.status(ctx.store),
        "pending_request": row["pending_request"],
        "setup_pending_event": pending_setup_seq(ctx.wstore, row["coworld_id"]),
    }


def _mixin_status(coworld: Coworld) -> dict[str, Any]:
    if not (coworld.mixin / mixin.FRONT_MATTER_FILE).is_file():
        return {"present": False}
    try:
        loaded = mixin.load(coworld.mixin)
    except mixin.MixinError as error:
        return {"present": True, "problems": error.problems}
    skills_dir = loaded.skills_dir
    skills = sorted(p.name for p in skills_dir.iterdir() if p.is_dir()) if skills_dir else []
    return {
        "present": True,
        "coworld": loaded.coworld,
        "coworld_version": loaded.coworld_version,
        "commands": sorted(loaded.commands),
        "skills": skills,
        "problems": [],
    }
