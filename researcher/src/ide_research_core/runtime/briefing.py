"""Per-card briefings (System Design §5.1; Runtime §5.3).

A briefing has a short shared part (the card, a doctrine reminder, current signals), the
card's own slice (a one-line summary per ref, and the Topics those refs are or point at), the
inbound messages relevant to this card, and, for planning cards only, ``plan.md``, the open
proposals and the planning backlog. Everything is read at canonical HEAD.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from ide_research_core.comms.resolver import ResolveError, resolve
from ide_research_core.queue.card import CardSpec

MARKER = "<!-- autoresearch-briefing task={task_id} -->"
SUMMARY_LIMIT = 160
URGENT = "now"
# How a ref reaches its Topics: a direct ``topics`` list, or one hop to a parent record.
_PARENT_FIELDS = {
    "experiment": ("hypothesis", "hypothesis"),
    "result": ("experiment", "experiment"),
}


@dataclass
class BriefingInputs:
    """Runtime-computed context for briefings.

    - signals: one line per current signal (hard stop, blocking, XP credits, ...).
    - inbound: routed inbound messages, each a dict with ``id``, ``kind``, ``urgency``,
      ``body`` and ``responsible_task`` (the one card that must acknowledge it).
    - open_proposals, planning_backlog: one line each; shown to planning cards only.
    """

    signals: list[str] = field(default_factory=list)
    inbound: list[dict] = field(default_factory=list)
    open_proposals: list[str] = field(default_factory=list)
    planning_backlog: list[str] = field(default_factory=list)


def _load(canonical: Path, uri: str) -> tuple[dict, str] | None:
    """(front matter or JSON, body) of a ref at HEAD, or None if it does not resolve."""
    try:
        resolved = resolve(canonical, uri)
    except ResolveError:
        return None
    text = resolved.content.decode(errors="replace")
    if resolved.path.endswith(".json"):
        loaded = json.loads(text)
        return (loaded if isinstance(loaded, dict) else {}), ""
    if resolved.path.endswith(".md"):
        post = frontmatter.loads(text)
        return dict(post.metadata), post.content
    return {}, text


def _one_line(text: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line if len(line) <= SUMMARY_LIMIT else line[: SUMMARY_LIMIT - 1] + "…"


def _summary(meta: dict, body: str) -> str:
    for key in ("title", "statement", "summary", "policy_name"):
        if isinstance(meta.get(key), str) and meta[key].strip():
            return _one_line(meta[key])
    return _one_line(body) or "(no summary)"


def _topic_ids(canonical: Path, uri: str, meta: dict, depth: int = 2) -> list[str]:
    kind = uri.removeprefix("ar://").split("/", 1)[0]
    if kind == "topic":
        return [str(meta.get("id") or uri.rsplit("/", 1)[-1])]
    if isinstance(meta.get("topics"), list):
        return [str(topic) for topic in meta["topics"]]
    if kind in _PARENT_FIELDS and depth > 0:
        parent_type, parent_field = _PARENT_FIELDS[kind]
        parent_id = meta.get(parent_field)
        if isinstance(parent_id, str):
            parent_uri = f"ar://{parent_type}/{parent_id}"
            loaded = _load(canonical, parent_uri)
            if loaded is not None:
                return _topic_ids(canonical, parent_uri, loaded[0], depth - 1)
    return []


def _format_measures(measures) -> str:
    if not isinstance(measures, list) or not measures:
        return "none"
    parts = []
    for measure in measures:
        if isinstance(measure, dict):
            text = f"{measure.get('name')} ({measure.get('direction')})"
            if "tolerance" in measure:
                text += f", tolerance {measure['tolerance']}"
            parts.append(text)
    return "; ".join(parts) or "none"


def _slice(canonical: Path, spec: CardSpec) -> list[str]:
    lines = ["## This card's slice", ""]
    topic_ids: list[str] = []
    if not spec.refs:
        lines.append("- (no refs)")
    for uri in spec.refs:
        loaded = _load(canonical, uri)
        if loaded is None:
            lines.append(f"- `{uri}`: (does not resolve at canonical HEAD)")
            continue
        lines.append(f"- `{uri}`: {_summary(*loaded)}")
        for topic_id in _topic_ids(canonical, uri, loaded[0]):
            if topic_id not in topic_ids:
                topic_ids.append(topic_id)
    for topic_id in topic_ids:
        loaded = _load(canonical, f"ar://topic/{topic_id}")
        if loaded is None:
            continue
        meta = loaded[0]
        lines += [
            "",
            f"### Topic {topic_id}: {meta.get('title', '')}",
            "",
            f"- Objective: {meta.get('objective', '')}",
            f"- Metrics: {_format_measures(meta.get('metrics'))}",
            f"- Guardrails: {_format_measures(meta.get('guardrails'))}",
        ]
    return lines


def _inbound(task_id: str, inbound: list[dict]) -> list[str]:
    mine = [message for message in inbound if message.get("responsible_task") == task_id]
    urgent_elsewhere = [
        message
        for message in inbound
        if message.get("responsible_task") != task_id and message.get("urgency") == URGENT
    ]
    if not mine and not urgent_elsewhere:
        return []
    lines = ["## Inbound messages", ""]
    for message in mine:
        heading = (
            f"### {message.get('id')} ({message.get('kind')}, urgency {message.get('urgency')})"
            " — you must acknowledge this"
        )
        lines += [
            heading,
            "",
            str(message.get("body", "")).strip(),
            "",
        ]
    for message in urgent_elsewhere:
        handler = message.get("responsible_task") or "the planning card"
        lines.append(
            f"- {message.get('id')} ({message.get('kind')}, urgent): read-only, "
            f"handled by {handler}"
        )
    return lines


def _planning(canonical: Path, inputs: BriefingInputs) -> list[str]:
    loaded = _load(canonical, "ar://plan")
    plan = loaded[1].strip() if loaded else "(no plan.md at canonical HEAD)"
    lines = ["## plan.md", "", plan, "", "## Open proposals", ""]
    lines += [f"- {proposal}" for proposal in inputs.open_proposals] or ["- none"]
    lines += ["", "## Planning backlog", ""]
    lines += [f"- {item}" for item in inputs.planning_backlog] or ["- none"]
    return lines


def build_briefing(canonical: Path, spec: CardSpec, task_id: str, inputs: BriefingInputs) -> str:
    lines = [
        MARKER.format(task_id=task_id),
        f"# Briefing: {spec.title}",
        "",
        f"- Task: {task_id} (request {spec.request_id})",
        f"- Mode: {spec.mode}; priority {spec.priority}",
        f"- Why: {spec.why}",
        "- Refs: " + (", ".join(f"`{ref}`" for ref in spec.refs) or "none"),
        "",
        "Follow the doctrine in AGENTS.md. Do this card's one unit of work in its mode, write the",
        "journal entry with its Decisions section, and acknowledge the inbound messages below that",
        "you are responsible for.",
        "",
    ]
    if inputs.signals:
        lines += ["## Current signals", "", *[f"- {signal}" for signal in inputs.signals], ""]
    lines += _slice(canonical, spec) + [""]
    inbound = _inbound(task_id, inputs.inbound)
    if inbound:
        lines += inbound + [""]
    if spec.mode == "planning":
        lines += _planning(canonical, inputs) + [""]
    return "\n".join(lines).rstrip() + "\n"
