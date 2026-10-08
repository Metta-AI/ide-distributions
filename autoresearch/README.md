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
| [`docs/designs/autoresearch-runtime-2026-10-06.html`](docs/designs/autoresearch-runtime-2026-10-06.html) | Draft for review | How it runs: Hermes profile distribution, Hermes Kanban as the next-action work queue, cron scripts, Hermes plugin, configuration, hosting neutrality, comms transport and adapters, credentials, compiler port plan, tests, schedule, risks |

The System Design is the authority for research-side behaviour. The Runtime
document must stay consistent with it.

The review record for the System Design (two rounds of comments with replies) is in
[`docs/designs/reviews/`](docs/designs/reviews/).

## Integrations

| Document | Covers |
|---|---|
| [`docs/integrations/softmax-ide-bridge.md`](docs/integrations/softmax-ide-bridge.md) | The Softmax IDE bridge as the external agent on `comms/`: the inbox files it writes, the outbox fields it reads, how it treats acknowledgements, and what is not exercised yet |

## Tracking

Asana: "GotA auto-research agent on Hermes: autonomous research loop + comms
interface, live on softmax.com" (task 1219186976892787) and its subtasks.
