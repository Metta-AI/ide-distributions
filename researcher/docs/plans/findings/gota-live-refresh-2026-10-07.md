# GotA live engine refresh — 2026-10-07

NOTES-2 requested live verification and reuse of the committed GotA lab compiler.
This finding records direct evidence before changing the compiler pin.

## Live identity and isolation

Read the league as Games Bond (`ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1`) with
project coworld0.1.57 and softmax-cli0.26.38. A temporary HOME contained only a
copy of the user token before selecting that player. The `/whoami` response
confirmed the Player subject before league/coworld reads. The original credential
file remained byte-identical and the temporary credential copy was removed.
No hosted XP, upload, submission, or model call was made.

The live league `league_3c60897b-25cf-4b37-9d1a-8554c1198f28` now resolves to:

- Coworld: `cow_6fb99f9c-98e4-4a9e-802c-ecc59621f179`.
- Version: **2026.10.7.2**.
- Polyworld: **801749e509e888b423ba44ee2188946c22e3ba85**.
- Source: `examples/gods_of_the_arena` at that commit.

Local evidence: `/tmp/ide-mvp-c2-live/identity.json` and the downloaded manifest
under `/tmp/ide-mvp-c2-live/game/cow_6fb99f9c-98e4-4a9e-802c-ecc59621f179/`.
This supersedes 2026.10.6.1 / 5287c437 for current qualification. Earlier findings
remain historical evidence at their stated pin.

## Unchanged v13-live load and play

The baseline SHA-256 remains
`1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
No baseline bytes changed. A local `coworld run-episode` used the newly downloaded
manifest, ten copies of that exact artifact, and `--variant competition`.
The command ran with an empty isolated HOME and local Docker configuration.

Shared G2 artifact/log inspection verified every seat's policy hash and completion,
all ten active VMs, no BASIC failures, and complete results:

| Measure | Result |
| --- | ---: |
| Active scripts | 10/10 |
| Decisions | 288200 |
| Recorded actions | 913550 |
| Ticks | 31200 |
| Seed | 2026 |
| Outcome | time_limit |
| Per-seat XP | 3001,2203,4050,3149,2865,3139,3370,4365,3116,2499 |

All scores were zero because the episode timed out; this is health evidence, not
competitive strength. Evidence: `/tmp/ide-mvp-c2-v13-live-episode/` and
`/tmp/ide-mvp-c2-live/v13-play.json`.

## Structured-mode measurement

Read-only reference: `personal_labs_gota@09d3ed28d488fa965e36d4d4f6d99439952e18c7`.
The lab's `gota_match.nim`, `build_gota_match.sh`, and `engine.env` working bytes
were checked against the committed files. The installed Nim version plus those
three hashes reproduced the existing harness fingerprint. Its binary was used
read-only; all probe artifacts were written under `/tmp/ide-mvp-c2-native/`.
The lab checkout and dependency checkout were not modified.

The harness pin is 801749e5; Bassy remains
`a02e6dd5889310b13d21725c891bde8d01539f08`. Direct strict compilation measured:

| Resource | Exact legacy v13-live | Mode-marker-only structured probe | Structured limit |
| --- | ---: | ---: | ---: |
| Globals | 251 | 329 | 512 |
| Arrays | 32 | 89 | 128 |
| Array cells | 3464 | 157384 | 262144 |
| Routines | 29 | 29 | 64 |
| Code instructions | 4378 | 4378 | 20000 |
| Source bytes including injected declarations | 54795 | 58470 | 69191 |

Both strict compiles passed. Structured declarations consume 78 globals,
57 arrays,153920 cells even if unused. Structured policy text has its own
65535-byte cap; injected declarations explain the larger measured-source limit.
Legacy limits remain256globals/32arrays/4096cells/65536source bytes.

The structured probe prepended only `' @gota-structures` to baseline bytes in a
temporary file. Two full native matches used ten identical policies, seed1,
Open Draft,28800 battle ticks (31200 total) and no fixed-roster bypass. Both had
no failed seats, final state hash `00000000AFAC3CBE`, identical per-seat XP,
and peaks15706instructions/24357work. This is a bounded mode check, not a
20-seed action-stream equivalence proof or generated-policy M1 qualification.

Evidence includes native binary identity, host registry, both resource reports,
and both match results under `/tmp/ide-mvp-c2-native/`.

## Required pin changes and reuse decision

Current toolkit builds/defaults, compiler host/context, shared gates, manifest
selection and GAME documentation must move to801749e5/2026.10.7.2. Preserve the
historical pin where needed to read old evidence. The immutable legacy v13-live
artifact remains the oracle; load/play now passes on the new engine.

Compiler resource checks must distinguish legacy and structured policy modes and
include all injected host storage. The measured structured headroom supports
using the lab's structured mode for generated policies instead of physical pvId
packing. A full generated-fixture measurement must still check the runtime and
unit total; old packed-legacy qualification does not transfer automatically.

Use the committed lab's host surface, engine context, tested harness mechanics,
resource measurement and GotA lifecycle facts with provenance. Keep this project's
G layer, goal wire, existing unit ABI, Hermes backend and v13-live target. The lab
uses a different unit ABI, wire names, grammar extensions and starter target, so
whole-file adoption of its runtime or strategy would replace those choices.
Its audit engine contract still names the older pin, and some prompt text conflicts
with measured all-declared-host-storage accounting; those claims require explicit
correction rather than copying them as new-pin qualification.

Implementation/adoption details and remaining qualification belong in compiler
PROVENANCE and REPORT-C. No real LLM compilation is authorized by this refresh.
