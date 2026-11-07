# Datadog agent logging: build scope

What is being built from [the plan](datadog-agent-logging-2026-11-03.html). Where the two disagree, this file wins. The plan
stays useful for evidence, Hermes observation points and Datadog limits, but its outbox and Worker are gone.

James's instruction: "Do not add any gates, testing/deployment flags, extra safety mechanisms, or so on ... the initial
implementation should be tight and minimal." The goal: everything about agent operations reaches Datadog, and a Session can
be rebuilt from Datadog alone. [`docs/agent-telemetry.md`](../agent-telemetry.md) says how to read it and what it cannot show.

## The shape

`observation -> pickled copy -> memory queue -> redaction -> chunked JSON lines -> /proc/1/fd/1 -> Datadog Agent -> Datadog Logs -> rebuild command`

- Orchestrator and Researcher profiles. Capture is always on, with no setting.
- The gateway plugin records context, requests, replies, tools and lifecycle. `research serve`, every other `research` command
  and the bridge record metadata. Only the gateway writer takes free text.
- Datadog Logs holds the content: service `ide-agent-telemetry`, in the `main` index at 15 days.
- `research telemetry rebuild` reads only Datadog's Logs search API, and prints JSON.

## Decisions

| Decision | Why |
|---|---|
| Pod logs, not an outbox, exporter and Worker | The Agent already collects container output. The pod test below lost nothing. |
| Lines are at most 4,096 bytes, with 3,000-byte chunks, written to `/proc/1/fd/1` | `CONTRACT.md`, "Lines", says why. |
| Each line starts with a newline of its own | Anything else on the stream can leave a line open, which would join the next telemetry line. Removing `PYTHONUNBUFFERED=1` would not end that, and would bring back the exit abort #223 removed. `agent-telemetry.md`, "How it reaches Datadog", gives the measurements. |
| Stdout's stream, not stderr | Canary stderr lines arrived with `status:error`. |
| Each line names its workspace, from `bootstrap.json` | Pod tags do not identify the owner. |
| One queued writer for every process. A caller pickles and enqueues, and one thread writes | A caller never waits. The direct writer this replaces could stall a tool past its commit on a stuck stream. |
| Exit flushes are bounded: 2 s for a command, 2 s per profile for the gateway, 3 s for `research serve` | `image/processes.py` gives ten seconds, and a stuck stream must not hold a process. |
| The gateway's log handler stays on the root logger when `telemetry.configure_logging` moves the others behind its queue | It only enqueues. Behind that queue a full stderr pipe stopped it: 0 of 5 lines arrived, against 5 of 5 on the root logger (measured 2026-11-06). An exit can also lose what is queued. |
| A full queue counts its drops, and the writer's thread logs each new count | Logging on a caller's thread can block on the same full pipe. |
| Writers outside the gateway mask Softmax tokens in metadata strings | A client chooses `actor` and `client.kind`. The patterns are plain regexes, so this needs no Hermes. |
| The bridge writes its own refusal records | 501, 422, 503 and a failed selection never reach the seam. A hop to it waited up to 2 s per refusal. |
| `on_model_attempt` is the one source of bodies | Middleware sees a request before stream options, and cannot see retries. A process killed during a call leaves `model_request` / `started` and what it had committed. |
| Messages are native JSON addressed by digest, with cache markers in the manifest | Markers move at every request. In the manifest, 95.5% of manifests stayed `append` over 100 real turns, against 0.5%. |
| Content stays in `main` at 15 days, sent once per process | A separate 30-day index was refused with 403 "Out of contract retentions are not authorized", and Flex is not on this org. James chose `main`. `agent-telemetry.md`, "Retention", says what this promises. |
| Store events are observed after COMMIT, with allowed metadata listed per event | A transaction can roll back, and payloads hold exception text. |
| Admission, command and Session fields come from the row, read after the commit | Cheaper than changing about a dozen event payloads, and no new call sites. |
| The bridge's request ID is a header, not a body field | `admissions.read` refuses a replay whose body differs, and each attempt has its own ID. |
| aiohttp's access lines are sent, except the seam's own successful `GET /p/<profile>/v1/runs/<id>` | They are the only record of a direct bridge-to-gateway call and its status. The poll runs once a second per live run and says nothing the seam's records lack. |
| Rebuild orders by process, then time, then `sequence` | An attempt is written when it ends, and an auxiliary call when it starts. |
| A row's text comes from its first listing, `rewritten_to` from its last. Join by row ID first, and a listing with none only if the row's own result agrees | Hermes's prune rewrites a row in place and drops row IDs after it. A call ID can come back on another row. |
| A row no request lists takes its own text: from its commit record, or a tool row's from its result. Nothing else stands in | A turn's last rows reach no later request, Hermes's tool guard writes its text with no model call, and a kill during a turn's first call leaves a user's message only on its commit, so "the last reply" was the wrong row. |
| The package is `research_core/agent_telemetry`, not `telemetry` | `research_core/telemetry.py` is #186's OpenTelemetry module. A package of that name would hide it, and git reports no conflict. |

## Measured on James's workspace, 2026-11-05 (raw results are not in the repo)

| Measure | Result |
|---|---|
| One writer, largest line that arrives whole | 900,000 bytes. Above that, text lands unparsed under `hermes-runtime`. |
| 16 concurrent writers, 496 lines per size | 4,096 bytes: all whole. 4,097: 49 unparsed. 16,000: 125 unparsed, 8 valid JSON with the wrong content. |
| Bursts of 1, 10, 60 MB and paced 240 MB over about 15 rotations | No loss, no duplicates |
| Real sleep and default delete, about 0 s wait, one run each | No loss |
| Written to searchable | 12 to 27 s |

The pod test used probe lines. No real captured Session has been rebuilt from real Datadog yet. Overhead, memory, backlog and
cost figures are in `agent-telemetry.md`, "Proof and cost". The benchmarks that took them are deleted.

## Cut: do not build

- **The delivery path:** SQLite outbox, exporter, platform submit route, Worker admission, R2 objects, queue, Logs consumer,
  receipts, delivery retries, a record contract in metta, `delivery_id`, segments, cross-process flush signalling.
- **Controls:** capture setting, per-workspace override, default-off rollout, capture on/off intervals, size caps, upload limits,
  Datadog quota, cap alerts.
- **Health and alerting:** health and gap records, checkpoints, coverage states, monitors, alerts, counters, a daily synthetic
  Session, scheduled rebuild, registry gate in CI, runbook.
- **Elsewhere:** Sensitive Data Scanner, LLM Observability, browser logging, RUM, cron inventory, a sleep handshake, runtime
  tokenizers, a generic final-request patch, an extra invocation ID.

## Rules for builders

- `research_core/agent_telemetry` and `plugin/capture.py` import only the standard library, because they run in Hermes's
  interpreter. Since #186 the rest of the plugin also imports the OpenTelemetry SDK there; capture still does not.
- Synchronous code. Plain words. Tests start their own Sessions.
- Access work stays small (James: "very little"). `agent-telemetry.md`, "Read a Session", says what is checked.

## Future list

Considered and not built. Do not add these or others without asking James: resending content by age for a process that runs
for weeks, pruning the in-memory record of what was sent, a
check that a line reached the Agent, reopening a stream that failed to open, rejecting a metadata line whose caller used the
wrong field name, logging an `observe_commit` failure off the caller's thread, proof that queues are empty at `ready_for_sleep`,
a `status: timeout` flag on rebuild's partial results, the rebuild saying when a Session's earliest surviving record isn't its
start.
