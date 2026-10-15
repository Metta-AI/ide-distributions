"""Skill proposals and the daily re-raise of unanswered proposals (System Design §5.7;
Overview C9 ``skills.create_dir``). Runtime only: queue-keeper runs both each run.

Skill sweep. Hermes writes agent-created skills to ``$HERMES_HOME/local/skills-staging/``
(bootstrap sets ``skills.create_dir``; outside every workspace). ``sweep_skills`` copies each
skill directory (one with a ``SKILL.md``) that is new or changed into canonical
``skills-proposed/<name>/`` (replacing what was there), commits it, and publishes one runtime
``suggestion`` with ref ``ar://skill-proposal/<name>``. The suggestion's ``runtime_key``
(before 2026-10-08: its title) carries the skill's content hash, so a crash between the commit and the publication is finished by the
next run, and an unchanged skill is never suggested twice. A changed skill's new suggestion
``supersedes`` the previous open one.

Re-raise. A proposal is a ``suggestion`` whose refs name an ``ar://skill-proposal/`` or
``ar://doctrine-proposal/``. While it is open (no reply to it or to any of its re-raises, not
superseded, not expired) and the latest message about it is at least ``RERAISE_EVERY`` old,
``reraise`` publishes a runtime ``suggestion`` on its thread with the same refs and
``reraises: <its id>`` and ``reraised_at`` (the run's time). The outbox is the only state. ``open_proposals`` gives the one-line
list that planning briefings show.
"""

import datetime
import hashlib
import shutil
from pathlib import Path

from ide_research_core.comms import delivery, runtime_messages
from ide_research_core.comms.inbound import derived_status, parse_time
from ide_research_core.comms.publisher import already_published, publish_message
from ide_research_core.comms.reader import read_since
from ide_research_core.io import gitops
from ide_research_core.runctx import RunContext
from ide_research_core.runtime.config_migrations import SKILLS_STAGING_RELATIVE
from ide_research_core.runtime.ledger import canonical_lock, commit

SKILLS_PROPOSED = "skills-proposed"
PROPOSAL_REF_PREFIXES = ("ar://skill-proposal/", "ar://doctrine-proposal/")
RERAISE_EVERY = datetime.timedelta(days=1)
HASH_CHARS = 12


def staging_dir(hermes_home: Path) -> Path:
    return Path(hermes_home) / SKILLS_STAGING_RELATIVE


def _files(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _tree_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()[:HASH_CHARS]


def _outbox(canonical: Path) -> list[dict]:
    return read_since(canonical, "proposals", after=0, include_unknown=True)


def _previous_suggestion(outbox: list[dict], name: str) -> str | None:
    """The latest suggestion for skill ``name``: by ``runtime_key``, or by its title if it was
    published before runtime_key existed."""
    key_prefix = runtime_messages.skill_proposal_key_prefix(name)
    title_prefix = runtime_messages.skill_proposal_legacy_prefix(name)
    found = [
        m["id"]
        for m in outbox
        if m.get("kind") == "suggestion"
        and (
            str(m.get("runtime_key", "")).startswith(key_prefix)
            or str(m.get("title", "")).startswith(title_prefix)
        )
    ]
    return found[-1] if found else None


def sweep_skills(ctx: RunContext, allow_no_remote: bool = False) -> list[str]:
    """Copy new or changed staged skills into ``skills-proposed/`` and suggest each once;
    return the names suggested. Takes the canonical lock."""
    canonical = ctx.canonical
    staging = staging_dir(ctx.hermes_home)
    suggested = []
    if not staging.is_dir():
        return suggested
    with canonical_lock(canonical):
        for skill in sorted(path for path in staging.iterdir() if (path / "SKILL.md").is_file()):
            target = canonical / SKILLS_PROPOSED / skill.name
            staged = _files(skill)
            if _files(target) != staged:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(skill, target)
                commit(canonical, [target], f"skills-proposed: {skill.name}")
            message = runtime_messages.skill_proposal(
                skill.name, _tree_hash(staged), SKILLS_PROPOSED, gitops.head(canonical)
            )
            if already_published(canonical, message):
                continue
            outbox = _outbox(canonical)
            publish_message(
                canonical,
                message,
                allow_no_remote,
                expects_response=True,
                supersedes=_previous_suggestion(outbox, skill.name),
            )
            suggested.append(skill.name)
    return suggested


def _is_proposal(message: dict) -> bool:
    return (
        message.get("kind") == "suggestion"
        and not message.get("reraises")
        and any(
            str(ref.get("uri", "")).startswith(PROPOSAL_REF_PREFIXES)
            for ref in message.get("refs") or []
            if isinstance(ref, dict)
        )
    )


def _replied(canonical: Path, ids: set[str]) -> bool:
    return any(
        inbound.get("kind") == "reply" and inbound.get("in_reply_to") in ids
        for inbound in delivery.inbox(canonical).values()
    )


def _open(canonical: Path, outbox: list[dict], now: datetime.datetime) -> list[tuple[dict, list]]:
    """(proposal, its re-raises) for every open proposal."""
    found = []
    for message in outbox:
        if not _is_proposal(message):
            continue
        reraises = [m for m in outbox if m.get("reraises") == message["id"]]
        ids = {message["id"], *(m["id"] for m in reraises)}
        if derived_status(canonical, message["id"], now).state != "open":
            continue
        if _replied(canonical, ids):
            continue
        found.append((message, reraises))
    return found


def open_proposals(canonical: Path, now: datetime.datetime) -> list[str]:
    """``BriefingInputs.open_proposals``: one line per open proposal."""
    return [
        f"{message['id']} {message['title']} (since {message['created']})"
        for message, _ in _open(canonical, _outbox(canonical), now)
    ]


def reraise(ctx: RunContext, now: datetime.datetime, allow_no_remote: bool = False) -> list[str]:
    """Re-raise each open proposal whose latest message is a day old (module docstring);
    return the ids re-raised. Takes the canonical lock."""
    canonical = ctx.canonical
    raised = []
    with canonical_lock(canonical):
        outbox = _outbox(canonical)
        for message, reraises in _open(canonical, outbox, now):
            latest = max(
                [parse_time(message["created"])]
                + [parse_time(m.get("reraised_at") or m["created"]) for m in reraises]
            )
            if now - latest < RERAISE_EVERY:
                continue
            publish_message(
                canonical,
                runtime_messages.proposal_still_open(message, gitops.head(canonical)),
                allow_no_remote,
                expects_response=True,
                thread=message.get("thread") or message["id"],
                reraises=message["id"],
                reraised_at=now.isoformat(timespec="seconds"),
            )
            raised.append(message["id"])
    return raised
