# Coordinator simplification: acceptance evidence

> **Retired.** This is the acceptance evidence of the previous profiles (the serial
> coordinator inbox), which were removed on October 28, 2026 and rebuilt from
> `docs/designs/ide-profile-refactor-2026-10-28-final.html`. The current profiles have no
> coordinator; their acceptance scenario is the `research serve` end-to-end test described in
> `docs/hosting.md`. This file and `coordinated-research-proof.json` are kept for history.

Updated October 22, 2026. This is local acceptance proof, not a production rollout.
Runtime distribution: `90899586f50513eac2b996e03e9a8df6e6320b30`.
Host runtime source: `1c2e8ade32`; retained-input test correction: `ac3f9ab4b5`.

## Ownership

One native durable FIFO inbox admits coordinator inputs. Its persisted result consumes an
input, including returned failures. Waiting has no arbitrary deadline. Process death leaves
the original input recoverable. Native notifications enqueue before advancing their cursor.

One native transaction deduplicates task creation. Native tasks and runs own lifecycle;
blocked work resumes the same task. N researchers execute concurrently outside the serial
coordinator. Only the coordinator uploads, integrates and submits.

Removed plugin commands, wakes and handled-event tables, event_ack, board_reconcile,
historical event scans, the extra workspace lease and per-tool audit writes. Publication
admission checks native authority; downstream upload, Git and submission retain their
existing idempotency. Retries preserve arguments. This removes ownership layers, not every
idempotency mechanism in the services it calls. The whole PR is not net-negative in lines.

## Acceptance matrix

| Requirement | Evidence |
| --- | --- |
| Durable ordered input | Real native FIFO, dead-owner recovery, HTTP admission crash-gap, replay and forged-recovery rejection pass. Returned failure replays without execution; the next input proceeds. |
| Retained input recovery | Image replacement preserves three unfinished user/completion/cron inputs. The native HTTP API recovers them in that order before admitting a new chat. |
| Parallel workers | Real native N=1 and N=3 dispatcher/OS-worker tests pass capacity, cancellation and replay checks. Automatic decomposition is disabled. |
| Native lifecycle and dedupe | Crash-after-create, archived-command replay, blocked-task resume and stale-run rejection pass. The live proof ends with exactly one task, done, and no unfinished inbox inputs. |
| Cancellation | Native stop, stale completion rejection, dirty-work preservation and publication fencing pass. The earlier real-HTTP cancellation proof held an accepted submission response while cancellation committed; its receipt remained reportable and the next effect was refused. |
| Integration | Same-base changes and conflicting Git changes revalidate the resulting commit. Upload receipt integration passed in the live flow. |
| Budget | Shared atomic episode reservations and user/operator ceilings pass. No cumulative dollar-budget guarantee is claimed. |
| Retained state | Conversations, tasks, dispatch state, receipts, dirty Git and configuration survive replacement. Unsupported migration fails before durable mutation. |
| Delivery | Build, compile, smoke, upload, integration, explicit submission, completed match, replay and coordinator/browser readback pass below. |

## Current live proof

- Original task `t_6ca9a8f4`, completed run 2. Run 1 blocked; the coordinator resumed the same task.
- Build `retreat-health-40-v13live-prebuilt-20261022-01` uses the shipped BASIC starter with retreat threshold 40.
- G1 compile passed. G2 smoke passed: seed 2026, 31,200 ticks, 10 active scripts, 288,200 decisions, 861,497 actions.
- Artifact SHA-256: `54fdae2a67921cd5ba2d5f47249b61816584416cbfb5d59238cc9a7554125f6a`, matching smoke evidence.
- Complete build evidence commit: `a69018e8f919601e8effd948a9fc5ce75e8f3bed`.
- Uploaded record: `V-01M5HDM94P7YJ7WKB1KFM831P8`; server policy: `330a09da-f57c-4e71-b6df-2bdcb1a0d356`.
- Receipt commit `cea36c88ee0e1516e0a5cd730a0b44c280a94ce3` integrated at `b56b7276f4b38c3ce279fe76c33aaa60c98bf2c0`.
- Submission: `sub_9981bd53-f537-43cb-99b9-0e65b06cc446`, placed.
- Round `round_79b9da79-9c2e-4dbf-af86-73fa45d54374` completed.
- Episode `3584d0f9-1cce-4ed4-ab82-5f7e90fd4230` completed with that exact policy at 09:21:30 UTC.
- Public replay: HTTP 200, 2,209,211 bytes; SHA-256 `e66981826b0652933aa6052871b5e8c7b36ba8bd10822c479d1cc55d2f5f00bb`.
- The coordinator independently read `latest_league_result`; browser readback showed the submission, exact policy and completed episode. Score 0.0 is not a superiority claim.

The build and upload used the reviewed queue implementation before runtime replacement.
Submission and readback used the final published runtime modules, without source overlays:
`sha256:6b780e0bd97733a31409084bdfad05111c6a708e45fd87752d86c60346061bf6`.
The final image's build.py and prebuilt.py hashes exactly match the build's recorded inputs.
Replacement preserved the completed task and upload; no replacement task or second upload
was needed. It fixed an explicit-submission check that incorrectly required STRATEGY.md
instead of verifying the recorded source hash. Semantic and prebuilt provenance now share
that check; 32 promotion tests cover submission, replay, recovery and altered-source rejection.

The fixture ran one smoke episode and one league match. Scheduling was paused immediately
after admitting the round. No paid XP experiment or superiority evaluation ran.

## Validation and reproduction

Researcher CI passed its full suite. Focused review coverage passed 195 researcher tests,
54 orchestrator tests and five real native integration scenarios. Host coverage passed
205 tests on Mac and the platform-specific watcher on Linux, plus 41 job-completion tests,
four browser regressions, IDE typecheck and scoped lint/type checks. Host Bazel, native and
workspace CI passed. Machine-readable details and decision-corpus results are in
`coordinated-research-proof.json`.

The same-model decision corpus passed 9/9 for baseline and candidate with identical
bound-workspace context. The first candidate run scored 6/9 because the fixture omitted
that context and returned placeholders to discovery tools. These are single simulated-tool
runs, not evidence of general judgment or performance improvement.

The first retained-chat test left its three deliberately pending inputs ahead of a new
request while recovery was paused. The corrected harness exercises real recovery before
new chat admission; it does not delete or fabricate completion of the pending inputs.

Fixture: `http://localhost:53720/ide-next`; manifest
`/tmp/nix-shell.S7dy7V/ide-next-ec2-gWJpto/manifest.json`.
Browser flow: send build/upload/submit, resume the blocked original task, then verify the
completed match. Runner:
`node packages/softmax-ide/tests/system/ide-next-ec2.mjs phase <manifest> coordinator-request`.
The normal API, Temporal, round runner, episode runner, job watcher, event processor and
OrbStack executed the match. Screenshot: `/tmp/review-final-browser-readback.png`.
Retained test:
`bash devops/hermes-fleet/applications/autoresearch/check_state.sh ide-coordinator-proof:review-merged ide-coordinator-proof:review-final`.

## Rollout boundary

Merge the distribution before the paired host pin. Drain active legacy tasks before profile
adoption; the hosted-worker README provides preflight and previous-image recovery steps.
A production read-only check at 08:19 UTC found zero nonterminal episode payloads missing
coworld_id. No production data was changed. Input retries and execution commands are
deduplicated; semantic interpretation of two differently worded requests remains a model decision.
