# Coordinator simplification: acceptance evidence

Status: incomplete; do not deploy as the accepted replacement yet.

The intended architecture is one durable workspace decision stream and N >= 1 native
researchers. Native task/run IDs own execution identity. Research artifacts, Git
integration, and fulfillment of a user request are separate facts.

## Implemented

- Native task creation with persisted execution-command replay, including archived tasks.
- Native blocked-task resumption without interpreting Git ancestry as completion.
- Native completion without a mandatory canonical research journal.
- Cron submits durable native wakes instead of running a separate planner.
- Failed or runtime-interrupted wakes receive bounded, idempotent retries. Explicitly
  cancelled wakes stay cancelled. Uncertain admission outside retention fails visibly.
- A workspace-wide native lease serializes coordinator turns; researchers remain concurrent.
- Native dispatcher capacity is configurable and enforced across concurrent dispatchers.
- Episode reservations are atomic across researchers. User limits cannot raise the allowance.
- Integration and explicit submission check the latest completed native run at admission.
- Explicit submission retains its separate path from statistical promotion.
- Conversion rejects active legacy tasks before changing installed profiles.

## Evidence

These checks exercise different boundaries; none substitutes for the full delivery flow.

| Acceptance requirement | Evidence | Remaining proof or implementation |
| --- | --- | --- |
| One decision stream; N=1/N=3 | Native lease exclusivity and independent-workspace tests; real native dispatchers and OS workers at both capacities | Durable globally ordered input admission is missing. A mutex is not an inbox. |
| Crash and replay | Concurrent task-create replay, archived replay, lost HTTP wake response, failed/interrupted wake retry | Full action/effect/acknowledgement crash matrix |
| Cancellation | Native worker termination, unrelated workers continue, late completion rejected, archived integration/submission rejected | Cancel during a real remote submission and reconcile previously admitted effects; audit autonomous promotion paths |
| Restart | Retained local IDE chat, native research delegation, browser history and runtime restart passed | One restart combining running, blocked, archived work, user constraints, and unfinished Git state |
| Parallel workspaces | Independent native session databases execute concurrently | Covered at native runtime boundary |
| Build, smoke, upload, explicit submit, completed match | Existing domain tests and scripted IDE delegation | Full real service flow through the candidate is not yet run |
| Integration | Workspace integration tests, including conflict behavior | Concurrent researchers from one base plus integrated-result revalidation |
| Shared budget | Atomic episode reservation tests and operator/user cap tests | Covered for episodes; no new cumulative dollar-budget guarantee |
| Fixed behavior corpus | Same live model scored 7/7 first-action cases on baseline and candidate | Failed-upload case and full execution outcomes are missing |

The seven-case corpus uses simulated tools. It does not prove improved model judgment,
remote effects, or end-to-end fulfillment. Baseline used 25 model calls and 143,853 tokens;
candidate used 20 calls and 76,150 tokens. These are single runs, not a statistical comparison.

The local IDE proof uses the retained system harness and a scripted model. It proves actual
browser, bridge, native profile, and restart behavior, not live-model task completion.

## Latest focused validation

- 238 distribution tests passed before the final wake-recovery change.
- 50 orchestrator tests passed after wake recovery; five native-install tests excluded.
- Native N=1 and N=3 dispatcher/cancellation proofs passed after the latest changes.
- Changed orchestrator Python code and tests pass Ruff.
- Earlier pinned runtime and application images built; installed profile checks passed.
  Those images predate the latest wake, budget, and publication-admission edits and require rebuilding.

Do not interpret this checkpoint as acceptance completion or production readiness.
