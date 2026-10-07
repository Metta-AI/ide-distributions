# autoresearch

An autonomous research agent that improves game policies (first game: Gods of the
Arena, GotA) without human approval, and reports to an external "Preston-role"
interface agent through a file-based communication interface.

**Status (2026-10-06): design only.** This directory holds the design documents.
No runtime code exists yet.

`autoresearch/` is intended to become the root of a Hermes Agent *profile
distribution* (installed with `hermes profile install <repo>`). The other
directories of `ide-mvp/` (UI, infrastructure) stay outside it.

## Design documents

Open the HTML files in a browser. Each one has a built-in comment sidebar: select
text, add a comment, then use **Copy JSON** to send feedback.

| Document | Status | Covers |
|---|---|---|
| [`docs/designs/autoresearch-system-design-2026-10-06.html`](docs/designs/autoresearch-system-design-2026-10-06.html) | Approved 2026-10-06 | The research agent itself: research method and modes, ontology (topics, hypotheses, experiments, results, claims and hubs, STRATEGY), doctrine, procedures, skills and tools, message semantics, stuck signals, game contract, parameters |

The System Design is the authority for research-side behaviour. A companion
Runtime document (how the system runs on Hermes) follows separately.

The review record for the System Design (two rounds of comments with replies) is in
[`docs/designs/reviews/`](docs/designs/reviews/).

## Tracking

Asana: "GotA auto-research agent on Hermes: autonomous research loop + comms
interface, live on softmax.com" (task 1219186976892787) and its subtasks.
