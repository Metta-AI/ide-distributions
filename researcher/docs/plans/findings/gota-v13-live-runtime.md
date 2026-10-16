# v13 live numeric compatibility: failure and mechanical resolution

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

Historical blocker, now resolved by VERDICT-B3. The initial VERDICT-B2 allowed
identifier renames only; that intermediate copy failed the runtime checks below.
The final generated fixture also replaces 64 executable slash operators with
integer-division backslashes, as approved by VERDICT-B3.

## Artifact identity

The immutable submitted artifact remains 54,718 bytes, SHA-256
`d0fd3a8210bbf754e699cfd7022ea18d7d416d8d8f9861eb0f1f10ff40b978a1`.
The initial generator renamed seven executable identifier tokens `is` to
`v13_item_slot`, rejecting collisions. In that intermediate version, comments,
strings, whitespace, operators, and every other token remained identical. The intermediate identifier-only copy had 54,795 bytes,
SHA-256 `009f8127286d4115b79ec977c694b36e8a99dd130ca6d6d828ba3c0122cf191e`.
The tokenizer test proves this exact transformation. The source has **64 slash
operator tokens**, excluding comments and strings.

## Actual local episode

Pinned coworld 0.1.57 / softmax-cli 0.26.38, the downloaded live GotA manifest
2026.10.6.1, Polyworld `5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`.
`coworld run-episode MANIFEST V13_LIVE` (the same file repeated ten times),
`--variant competition --output-dir /tmp/ide-mvp-task14-v13-live-episode`,
with `DOCKER_DEFAULT_PLATFORM=linux/amd64`, completed as a local episode.
No credential or platform mutation was needed.

All ten private logs contain:

```text
BASIC error: BASIC integer is outside the fixed-point range
```

The actual output directory is `/private/tmp/ide-mvp-task14-v13-live-episode`:
`logs/policy_agent_0.log` through `logs/policy_agent_9.log`,
`logs/game.stdout.log`, `results.json`, and `replay`.
The game log reports `scripts: 0/10 active, 0 decisions`, replay `0 actions`,
31,200 ticks, time limit, all scores zero, and all heroes level 1 with zero XP.
The CLI's successful process exit means the simulator completed; it does not mean
that the policies played. The identifier rename fixed compilation/loading only.

## Root cause and focused proof

Historical Polyworld `7365e4e9390e97bbc8b7722b41a2ae88ae008c2b`,
`src/polyworld/basic.nim:950-957`, implements `safeDivide(int32,int32)` using
`left div right`. Slash maps to this instruction at lines 1027 and 2523-2524.
The historical game imports that integer BASIC module (`bots.nim:5`). This source
pin proves the historical contract; the submitted policy's exact source commit
is unknown, so it is not evidence of the artifact's original build environment.

Current Polyworld pins Bassy `a02e6dd5889310b13d21725c891bde8d01539f08` in
`coworld/dependencies.lock`. Bassy `src/bassy.nim:1641-1642` maps `/` to
`DivideOp` and `\` to `IntegerDivideOp`. At lines 4688-4693 their execution uses
`/` and `div`, respectively. `src/bassy/numbers.nim:175-192` implements `/` as
Q16.16 division and `div` as int32 division toward zero. `asFixed` at lines 88-97
rejects integer operands outside -32768 through 32767. `asInt` at lines 112-123
also rejects fractional values at integer-only boundaries.

The first confirmed failing top-level path is `lhPlan()`:
`lhPlan` calls `lhUpdateMemory` at line 670, which reaches
`v13-live.bas:478`, `oChase = selfAttackRange / 1000 + cfgChaseKu`.
The later `lhRange` expression at line 673 has the same problem. Live class attack ranges
are 70,000 through 390,000 (`content.nim:186-350`), all above the fixed-point
conversion ceiling. Native execution of prefixes through main calls
`cfgInit`, `scanObjects`, `shScanInventory`, `pnPlan`, `svPlan`, `ksPlan` passes
first tick; adding `lhPlan` fails. Prefixes are diagnostic copies in `/tmp`,
not changes to the authoritative artifact. Further diagnostics calling only
`lhUpdateMemory` pass with its body ending before line 478 and fail with line 478
included (`/tmp/v13-memory-{477,478}.bas` and adjacent logs). This isolates the
first failing expression rather than only the enclosing routine.

Minimal current-engine probes, run with the already-built pinned tool:

```sh
games/gota/tools/bin/gota_audit-5287c437 --smoke /tmp/gota-slash.bas 1 1
games/gota/tools/bin/gota_audit-5287c437 --smoke /tmp/gota-integer.bas 1 1
games/gota/tools/bin/gota_audit-5287c437 --smoke /tmp/gota-fraction.bas 1 1
```

- `gota-slash.bas`: `a = selfAttackRange / 1000` fails with the observed range error.
- `gota-integer.bas`: `a = selfAttackRange \ 1000` passes the short native probe.
- `gota-fraction.bas`: `a = 45 / 100 : b = a mod 16` fails with
  `BASIC value must be an exact int32`. The file uses two lines rather than a colon.

Probe outputs are adjacent `/tmp/gota-{slash,integer,fraction}.log`.
The fractional toy probe demonstrates a possible further numeric hazard:
small fractional divisions change historical truncation and can fail at
integer-only operations, indexes, or host calls. It does not prove that those
boundaries fail on additional paths in this exact policy. Uniform operator
migration is recommended because it restores the historical integer-division
operation throughout the source; selective fixes have not been evaluated.

## Approved extension and local resolution

VERDICT-B3 authorized lexical replacement of all 64 executable `/` tokens with
`\`, in addition to the seven identifier renames. The generator and token test
now prove exactly those 71 substitutions, preserving every other token. The
final variant SHA-256 is
`1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
This preserves the historical integer-division operation; the changed token is
outside the initial identifier-only authorization and is now explicitly approved.
The actual rerun at `/tmp/ide-mvp-task14-v13-integer-episode` completes with no
BASIC errors in any of the ten private logs, 10/10 scripts active, 288200
decisions and 861497 actions. Every hero submits actions and earns positive XP.
The episode times out after 31200 ticks with zero scores; no competitive
improvement or full historical equivalence is claimed. Do not disable the live engine's
fixed-point mode or change policy behavior to suppress the error.

The current league entry `sub_38be3038-d8d1-4275-91da-557ee52ba109` very likely fails
to load on the live engine, inferred from the original artifact's exact local
load rejection recorded in `gota-toolkit-drift.md`. No league result was inspected
for this claim, and no league entry was changed. The intermediate renamed variant's actual runtime failure
exposed a separate dialect migration issue, now mechanically resolved locally.
The submitted league artifact was not changed.
