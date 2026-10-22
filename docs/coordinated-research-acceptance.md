# Coordinator simplification: acceptance evidence

Status: local acceptance passed; production rollout has not been performed.
Updated October 21, 2026. Distribution implementation: f9d016474b3eaa98b9c9b91e93517accbf01f0aa.

## Architecture

One durable, ordered workspace inbox admits coordinator inputs. Native task/run IDs own
execution identity. N >= 1 native researchers execute concurrently. Only the coordinator
publishes artifacts and submissions. Research results, Git integration, and request
fulfillment remain distinct facts.

Removed semantic task/attempt identities, replacement/dependency graphs, ancestry-based
completion, mandatory research journals, and independent cron planning. Native blocked
work resumes under its original task identity. Duplicate execution commands replay their
persisted result, including archived tasks. Stable completion identities enter the inbox
before notification cursors advance. Restart recovers the oldest unfinished input.

Cancellation revokes publication before stopping native work. All publication paths check
the latest completed native run. Episode reservations are atomic across researchers.
Conversion rejects active legacy tasks before changing credentials or installed profiles.

This simplifies ownership and lifecycle semantics. It does not reduce total lines or tool
count: exposed tools increased from 25 to 27, adding upload_build and budget_limit.

## Acceptance matrix

| Requirement | Observed evidence | Remaining |
| --- | --- | --- |
| Ordered coordinator inputs | Native FIFO, dead-owner recovery, admission crash-gap, replay, forged recovery rejection, notification dedupe checks pass in delivery image | No known implementation gap |
| N=1 and N=3 | Real native dispatchers and OS workers pass capacity, cancellation and replay checks | None at tested capacities |
| Native lifecycle | Original blocked task resumed; crash-after-create and archived replay pass | Full remote action/effect/acknowledgement proof below |
| Cancellation | Native stop, late-completion rejection, dirty-work preservation, publication admission fencing pass | None at the tested admission boundary; real API response held while native cancellation committed |
| Restart | Mixed running/blocked/archived fixture retains IDs, constraints, dirty Git, receipts and three ordered inputs | None for retained fixture |
| Live completion recovery | Task t_ca267c81 run 7 completion, inbox sequence 10, identity kanban:autoresearch:t_ca267c81:302 recovered after runtime restart and attempted upload | Publication chain below |
| Integration | Same-base concurrent changes and conflict resolution revalidate the resulting commit | None in integration tests |
| Budget | Shared atomic episode reservations and operator/user ceilings pass | No cumulative dollar-budget guarantee claimed |
| Real delivery | Live model built, smoke-tested, uploaded, integrated and explicitly submitted the committed artifact | Completed round, exact policy participation, public replay and coordinator readback verified |
| Behavior corpus | Same-model simulated-tool first-action cases: baseline 8/9, candidate 9/9 | Single runs; not evidence of general judgment improvement |

## Real build evidence

Original task: t_ca267c81, completed run 7. No replacement task was created.
Four unintended native auto-decomposer children were archived; auto-decomposition is now disabled.

- Commit: 893c4be36bd1bd84a086be30df998456ce865556.
- Complete build: build-evidence/62ee8269-1.
- Policy SHA-256: 93c970e4ec9fa56bf7b36c613d0bcf4b1da84199128f2757a39845621c7f8021.
- Native compile passed; generated retreat threshold is 40 and used by the health comparison.
- G2 smoke passed: seed 2026, 31,200 ticks, 10 active scripts, 288,200 decisions, 856,045 actions.
- This is smoke evidence, not a superiority evaluation or champion promotion.
- Upload name: gota-retreat40-302. Initial upload failed because the fixture advertised
  container-local localhost for storage. Corrected fixture; the same artifact uploaded successfully.
- Server policy version: 3485b6f7-0b93-4bf1-a4c5-3bfffff99d47.
- Upload-request and upload-completion replays each returned that same server version.
- Receipt commit: df430d5a7db3a7508c69622c1e5cb6dbc761da51.
- Integrated canonical commit: 696ebaec9409723423c138dc34de7ce8b63c16b7.
- Explicit submission: sub_fb245b5d-0ef3-4615-bb93-755bc6036081; server status placed.
- Round: round_63f73dff-9ede-48d7-be28-1a8bb69dd8e0.
- Episode request: ereq_f3f8450d-a2b7-4476-b296-62e5aec558b1.
- First real match completed as episode 5d0d6c02-0239-41ea-a118-aade37f9abf0.
- Replay-enabled proof: round round_0fd879ff-896b-4e94-b910-66f888253acc, episode
  0c402354-3a04-4e90-9ec9-caf2a7834e19, both completed. The original uploaded policy
  participated; the public replay returned HTTP 200 with 2,545,826 bytes.
- Three local episodes total: one G2 smoke and two league matches. No paid XP or
  statistical superiority evaluation. League scheduling remains paused.

The native cancellation scenario was also run with its transport callback replaced by a
real local API POST, holding the accepted response until native cancellation committed.
Server submission sub_b31faead-6df8-4b1c-b5ef-da6d0814c402 remained reportable afterward;
the next publication attempt was rejected without another remote call. This tests native
admission against a real remote effect, not a full model or CLI response-loss scenario.
The disposable probe used auto_champion=never and the league was paused to prevent extra rounds.

## Focused validation

- Distribution orchestrator: 54 passed, five native-install cases excluded from that invocation.
- Delivery-image native lifecycle/capacity: three passed, one deselected.
- Compiler: 82 passed. Upload/CLI: 97 passed. Workspace integration: 19 passed.
- Host short-path suite: 204 passed. Linux watcher checks: two passed.
- Clean and retained delivery-image installation checks passed.
- Changed-source Ruff and host type checks passed. Scoped migration/runtime Bazel checks passed.
- Initial Mac runs encountered Unix socket path-length and watcher-platform failures;
  short-path reruns and Linux watcher checks supplied the relevant passing evidence.

The delivery image is sha256:688eaa79bc229a79b196076c8751c658197e94176e10449e68ca60f7809bda29.
The live fixture was verified at http://localhost:51115; it is disposable, not production proof.
Host implementation commit: 33162787f4. Publish the paired distribution and host changes together;
drain active legacy tasks before adopting the new profile.


## Behavioral comparison and delivery repairs

The nine-case corpus used the same model and simulated tools. Candidate: 9/9, 28 calls,
111,606 tokens, 65.03 seconds, reported inference cost $0.004096. Baseline: 8/9, 24 calls,
136,310 tokens, 126.13 seconds, $0.006135. Cache state and single-run sampling limit any
performance inference. The baseline failed to resume a blocked task after an integrated checkpoint.

Real delivery required fixture repairs: a storage endpoint reachable from both host and
containers, the public replay bucket, the normal admission seed, and existing leaderboard
projection migrations. The API's replay lookup also incorrectly used a manifest display name;
it now uses the stored Coworld ID. That domain defect is independent of coordinator ordering.
Existing completion tests passed (41), execution package Bazel tests/typechecks passed, and
fixture formatting passed. The first match remains preserved; only a second match was run
for replay-visible readback after the missing public bucket was identified.

The live cancellation transport holds a real accepted HTTP response while cancellation
commits. It does not simulate arbitrary provider failures. Model-directed semantic deduplication
is not a deterministic guarantee: input retries and execution commands are deduplicated;
intentional repeated work remains a coordinator decision.

## Final lifecycle UI verification

The UI now displays native task status directly. A done task reads “Worker finished”;
missing deliverable metadata cannot turn it into an unverified lifecycle state.
Three browser/reference regressions and the IDE typecheck pass. The retained browser
proof displays the coordinator’s completed round, episode and exact submitted policy.
Evidence: phase-runs/022f848a-e1b7-4ce1-b63f-67b44c89f84c/coordinator-request.png
under the retained fixture directory.
