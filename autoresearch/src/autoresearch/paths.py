"""Workspace layout: where each kind of record lives under a workspace root."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Layout:
    """Maps record kinds to paths under ``root`` (canonical workspace or a worker worktree).

    kb/claims, kb/hubs, kb/archive, kb/log (one ``<ULID>.md`` per entry), kb/_generated;
    topics, hypotheses, experiments, results, versions (``<V-id>/version.json`` + events/);
    evidence/index (``<sha>.json``); journal; comms/{outbox,staged,inbox};
    cards/requests; strategy; candidates; stats; plan.md; AGENTS.md;
    .supervisor/ledger (durable, tracked).
    """

    root: Path

    def _sub(self, relative: str) -> Path:
        return self.root / relative

    @property
    def kb(self) -> Path:
        return self._sub("kb")

    @property
    def claims(self) -> Path:
        return self._sub("kb/claims")

    @property
    def hubs(self) -> Path:
        return self._sub("kb/hubs")

    @property
    def archive(self) -> Path:
        return self._sub("kb/archive")

    @property
    def kb_log(self) -> Path:
        return self._sub("kb/log")

    @property
    def kb_generated(self) -> Path:
        return self._sub("kb/_generated")

    @property
    def topics(self) -> Path:
        return self._sub("topics")

    @property
    def hypotheses(self) -> Path:
        return self._sub("hypotheses")

    @property
    def experiments(self) -> Path:
        return self._sub("experiments")

    @property
    def results(self) -> Path:
        return self._sub("results")

    @property
    def versions(self) -> Path:
        return self._sub("versions")

    @property
    def evidence_index(self) -> Path:
        return self._sub("evidence/index")

    @property
    def journal(self) -> Path:
        return self._sub("journal")

    @property
    def comms_outbox(self) -> Path:
        return self._sub("comms/outbox")

    @property
    def comms_staged(self) -> Path:
        return self._sub("comms/staged")

    @property
    def comms_inbox(self) -> Path:
        return self._sub("comms/inbox")

    @property
    def card_requests(self) -> Path:
        return self._sub("cards/requests")

    @property
    def strategy(self) -> Path:
        return self._sub("strategy")

    @property
    def candidates(self) -> Path:
        return self._sub("candidates")

    @property
    def stats(self) -> Path:
        return self._sub("stats")

    @property
    def plan(self) -> Path:
        return self._sub("plan.md")

    @property
    def agents_md(self) -> Path:
        return self._sub("AGENTS.md")

    @property
    def ledger(self) -> Path:
        return self._sub(".supervisor/ledger")
