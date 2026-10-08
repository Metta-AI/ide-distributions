# Task 2.4: real Hermes M1 qualification

**Completed locally on 2026-10-08.** Real Hermes/Kimi generation produced immutable
build `00a0bdcc-2`, which passed G1–G5, the full twenty-seed native equivalence
oracle, and twenty build-bound attribution audits. Task 2.5 has not begun. No
upload, hosted XP, publication, competitive qualification or goal-truth proof is
part of this result.

## Qualified artifact and evidence

- Build source commit: `00a0bdcc1414cc9b162d3a5e30a8097f11db6a17`.
- Policy SHA256: `9a091ac3aaa925aa7c2a89c5a944ef38f1a36eefe966c84f037e36a42298b9fc`.
- Unchanged v13-live reference SHA256:
  `1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
- Engine: `801749e509e888b423ba44ee2188946c22e3ba85`, Coworld `2026.10.7.2`;
  Bassy `a02e6dd5889310b13d21725c891bde8d01539f08`.
- Hermes v0.21.5 source: `f97608f178d1ffeca59860195ab7da295f7c8e5f`.
  Primary model: `moonshotai/kimi-k3`; local CLI packages: coworld `0.1.57`,
  softmax-cli `0.26.38`.

Immutable build evidence is retained at
`autoresearch/games/gota/compiler/strategy/compiled/00a0bdcc-2/`.
The main run is `/tmp/ide-mvp-c3-real-m1-20261008-110957/`, including
`primary-verify.json`, `oracle/equivalence.json`, `goal-audit-seed-1.json` through
`goal-audit-seed-20.json`, `qualification-summary.json` and budget receipts.
These large local artifacts are not source-committed. The complete execution and
review report is `/tmp/ide-mvp-c3-m1-report.md`.

G1 measured 384 globals, 102 arrays, 157,993 array cells, 54 routines, 5,056 static
instructions and 51,529 policy bytes. G2 ran all ten seats for 31,200 ticks,
288,200 decisions and 913,550 actions. G3 passed ten full native seeds. G4 passed
its twenty side/seed runs as an advisory screen; candidate and reference both had
zero Glory and win rate, so this establishes no competitive strength. G5 parsed
the compiler wire for all ten seats.

The `--build` oracle bound immutable policy/map/source and native provenance. All
seeds 1–20 matched the exact reference's ordered attempted commands across
624,040 validated records; no first difference was found. Oracle report SHA256:
`1b36bf793c6d6cc37ce5f00e71f477da0d9b473ddfcf2c8be83dc941bc9cb408`.
The twenty audits each covered candidate seats 0–4 with complete declared-goal
attribution and no issues: 2,882,000 decisions total. All five-level predicate
checks and all three goal claims remain **unmeasurable**.

## Execution history and corrected failures

C3 explicitly authorized flash first and a $40 aggregate M1 budget. Every compile
had numerical dedicated-key receipts before and after execution. Real Hermes ran
outside pytest, in fresh isolated homes, with no changes to personal profiles or the user's existing credential files. Tests retained the mandatory fake-only guard.

| Run directory suffix | Model | Result |
| --- | --- | --- |
| `20261008-101446` | GLM flash | Failed build `afc8ab06-1`: top-level `-z` required a prompt argument; three argparse exits, no generation and zero direct key delta. |
| `20261008-101854` | GLM flash | Aborted after chat omitted usage.json; generated output could not qualify, and built-in repair calls were stopped. |
| `20261008-103840` | GLM flash | Aborted because an asynchronous tirith startup warning corrupted stdout JSONL; strict parsing rejected it. |
| `20261008-104620` | GLM flash | Failed build `b1b32b33-2`: the model wrote forbidden probe_cap.txt while diagnosing truncated JSON specification lines. |
| `20261008-105546` | GLM flash | Unqualified: repeated upstream idle timeouts; one completed generation also failed the required guess-ID format. Interrupted repair usage is retained. |
| `20261008-110957` | Kimi K3 | First generation validated and assembled. G2 initially failed because OrbStack was stopped; interrupted repair usage is retained. The same unchanged stage later verified and finalized successfully. |

The reviewed port was retained. Switching to the lab compiler would have required
new Hermes, package and provenance integration. The corrections were mechanical:

1. `f319fc2` corrected the actual Hermes parser boundary; independent review passed.
2. `b1b32b3` adopted official `chat --format stream-json`, retaining the 40-turn
   limit. A validated, explicitly labeled usage receipt keeps cumulative main
   token counts, original records and raw-stream SHA256. It does not claim native
   usage-file, cost, API-call, reasoning or auxiliary coverage. Review passed.
3. A verified task-local tirith 0.4.2 executable was installed before startup in
   each isolated home. Security and strict JSON parsing stayed enabled.
4. `00a0bdc` exposed byte-identical component text in ordinary multiline scratch
   context files. Work-order hashes, interfaces, baseline and output scope stayed
   unchanged; probe/spec-edit regressions still fail closed. Review passed.
5. OrbStack was started without deleting, resetting or stopping other resources.
   The existing game image was present. The supported `verify` lifecycle resumed
   staged build `00a0bdcc-2` and finalized it without another paid generation.

Focused fake-only Hermes/dispatch checks passed 33 tests in 11.11 seconds after
the last source fix. Eight driver Ruff diagnostics remain inherited; no new
lint diagnostic was introduced. Source hashes and immutable-build enforcement
were preserved. Each source fix passed documentation audit and independent review.
The earlier C2 incident remains historical and is not erased by this authorized run.

## Budget and limits

The initial shared-key usage was $0.002105989. Final observed usage was
$2.743670536, with $97.256329464 remaining. The observed shared-account increase
was **$2.741564547**, including any sibling use; it is not exact task attribution.
Provider posting lag was observed, so the final task invoice is not independently
proved by this endpoint snapshot.

Across all retained session databases, flash measured 816,501 input, 163,683
output and 6,292,416 cache-read tokens over 128 reported API calls. Kimi measured
259,138 input, 37,955 output and 835,328 cache-read tokens over 23 reported calls,
including its interrupted repair session. Pricing every cache bucket at the full
current input rate gives measured-main estimates of $1.14817905 flash and
$1.14541542 Kimi. Those estimates omit failed-response and auxiliary charges;
the larger provider-account delta is retained separately. The primary monitor
checked every five seconds, was configured to stop at a $25 observed/estimated
threshold, and
reserved $13 for an in-flight request under the unchanged $40 cap. No further
paid generation followed the primary interruption.

The 4 GiB disk preflight passed before execution and before the full oracle
(16.78 GiB free). Final free space was 13.10 GiB. Referenced successful and failed
evidence was retained. Native receipts bind the enumerated source/configuration,
compiler and executable inputs; they retain the previously documented external
C compiler/Nim runtime/system-SDK attestation limits.
