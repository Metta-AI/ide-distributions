# M1: Hermes CLI dispatch correction

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

The first C3-authorized flash compile on `afc8ab0` failed before generation.
Immutable failed build `afc8ab06-1` records three built-in attempts, each rejected
by argparse: top-level `-z/--oneshot` requires a prompt argument. No units were
produced, no gates ran, and M1 remains unqualified.

Installed Hermes is the sanctioned editable v0.21.5, source
`f97608f178d1ffeca59860195ab7da295f7c8e5f`, matching the existing Hermes facts.
Its `hermes_cli/_parser.py` defines `--query-file`, boolean `--oneshot` and
`--max-turns` on `chat`; `--usage-file` is top-level. The compiler now uses
`hermes --usage-file PATH chat --oneshot --query-file PATH -t file --max-turns 40
--model MODEL --provider openrouter`. Model selection, scratch isolation, input
identity, outputs, write guards and usage validation remain enforced.

The contained fake regression now requires this exact parser boundary. It first
failed with the previous dispatch, then passes with the corrected form. Real
`hermes chat --help` confirms those flags without a model call. The backend guide
is the owned operational reference; the old Plan 02 dispatch example is stale.

Evidence is `/tmp/ide-mvp-c3-real-m1-20261008-101446/` and
`/tmp/ide-mvp-c3-hermes-chat-help.txt`. Dedicated-key numerical usage before and
after the failed compile was identically `$0.002887121`, remaining
`$99.997112879`; the conservative delta from the parent's earlier shared-key
baseline is `$0.000781132`. No key was printed or stored by this task. No upload,
hosted XP, sibling edits or Task 2.5 action occurred.

## Usage-owner correction after reviewed retry

The corrected parser command reached three real flash sessions, but chat did not
write usage.json. Driver guards rejected it and entered built-in repair rounds;
the process group was stopped once this structural failure was identified.
No gates or immutable successful build were produced. Source tracing supersedes
the earlier conclusion that accepting --usage-file implied emission.

Pinned Hermes main.py:1850 constructs chat kwargs without usage_file. Only
main.py:3093 forwards it to oneshot.run_oneshot. That runner directly constructs
AIAgent at oneshot.py:573 without max_iterations; run_agent.py:266 defaults to
sys.maxsize, and agent_init.py:2366 creates its budget from that argument.
The isolated agent.max_turns configuration therefore cannot fix top-level -z.
No global Hermes source or personal configuration was changed.

The compiler uses official `chat --oneshot --format stream-json` with the existing
40-turn/file/model/provider flags. stream_json.py:84-94 maps the final result's
cumulative token counters into input/output/total/cache_read/cache_write.
turn_finalizer.py:633 reads session counters, accumulated after each response by
turn_usage.py:171-180. cli_single_query.py:473 creates the init record with model
and session; :288 emits the terminal result. The driver validates this protocol,
retains the raw JSONL stream, and writes explicitly derived usage.json with source
hermes-chat-stream-json, original records, and raw-stream SHA256. The model cannot
write that receipt. Missing, malformed, duplicate, wrong-model, wrong-session,
nonzero or invalid-token records fail closed. Changed/session-rotated streams are
rejected rather than guessing attribution.

This protocol omits cost, API-call, reasoning and auxiliary usage fields. It
identifies the requested initial model, not separately the served/fallback model.
Budget proof requires conservative token prices plus reconciled provider balances;
this receipt is not the native oneshot ledger or complete billing accounting.

Retry evidence: /tmp/ide-mvp-c3-real-m1-20261008-101854/. Key usage before
0.002887121 and immediately after0.004858604 reflected posting lag, not complete
real spend. Isolated DB numerical receipts recorded386903input/42134output/
3745088cache-read tokens and63APIcalls; estimatedmaincost0.19145509USD, no actualcost.
Charging all cache buckets at the full flash input rate bounds measuredmainusage
at0.64086565USD, excluding auxiliaries. The conservative shared $40 budget remains
in force. No paid retry follows this fix until independent scoped review.

Validation: eight new parser tests failed before implementation. The corrected
contained Hermes/dispatch suite passed31 tests in10.60s. Ruff remains eight
inherited driver diagnostics; test file and new helper introduce no diagnostics.
Documentation audit updated the backend guide and this finding before commit.

## Readable specification context

The first clean-stream attempt `b1b32b33-2` failed the unchanged write-scope guard:
the model wrote probe_cap.txt to measure the reader's long-line cap. Its tools
could not read full compiled_text values from escaped JSON lines. This was an
input presentation defect, not a reason to permit extra outputs. No gates ran.

Every component's exact compiled_text now also lives in a normal multiline
`context/specs/<ID>.md` scratch input before the initial snapshot. The prompt
directs the model there. The work order, source text, source hashes, interfaces,
baseline and output scope remain unchanged. Current C.farm text has18original
lines with longest932characters; K.config has55lines with longest545. No wrapping,
truncation, semantic rewrite or authored policy substitution is introduced.

The fake first reproduced missing context files, then verifies full specification
bytes in both trivial and full-v13 generation. New probe-file and spec-edit cases
prove the snapshot guard still rejects those writes. Focused Hermes/dispatch
validation:33passed in11.11s. Documentation audit updated the backend reference
and this finding before commit.

Clean-stream setup requires the supported tirith scanner installed before agent
startup. On a fresh home Hermes asynchronously downloads it and cli.py:981 prints
a startup warning to stdout, corrupting its JSONL protocol. The retained task-local
tirith0.4.2 executable hasSHA256
873d8834902dbc47f339f79088d0a839f8932f5a8c34dcaedacb60fa6f2d0922.
The execution wrapper copies this verified executable into each new isolated home,
checks its version, and calls the sanctioned interpreter's ensure_installed()
which resolves it synchronously. No security setting or parser check is disabled.
This is a local environment preflight; runtime hosting remains out of scope.
