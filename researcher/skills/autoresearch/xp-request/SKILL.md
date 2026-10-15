---
name: xp-request
description: Use when launching or relaunching an experiment's XP.
---

# Launch XP and hand off the wait

Implements the launch steps of System Design §5.4. Use after `build-upload` or on an assigned
launch card. Doctrine upheld: D-03, D-06, D-09, D-11, D-21, D-27.

Read the experiment's frozen design, uploaded candidate binding and recorded requests.
The orchestrator assigns work on this entity; your job is to recover or launch the specified
batch, preserve its state and complete without waiting for the platform.

## Procedure

1. Read `"$RESEARCH_CLI" records show <E-id>`. Use the attempt named on the launch card;
   otherwise use `lifecycle.launch_attempts + 1`. Never increment that field yourself.
2. Recover existing requests first:
   `"$RESEARCH_CLI" softmax xp recover <E-id>`
   Existing handles are work already launched, not a reason to create another attempt.
3. Create the missing requests for the frozen design:
   `"$RESEARCH_CLI" softmax xp create <E-id> --batch 1 --attempt <n>`
   Each seed gets one candidate and one baseline request, one episode each. For planned
   slices, use `--batches <K>` and the assigned batch number. The tool records each handle
   immediately, preserves existing requests, and updates the experiment's state.
4. Read the returned `created`, `existing`, `missing`, `pairing` and refusal fields. Journal
   the attempt, batch, request IDs and what remains. Read the experiment again for its
   durable state; do not hand-edit platform bookkeeping.
5. Finish through `card`, including integration, with the following handoff:

   | Result | Handoff |
   | --- | --- |
   | All requests exist | `outcome: done`, `experiment.id`, and `awaiting: {xp: [request IDs]}`. |
   | Credit refusal | `outcome: partial`, `awaiting: {credits: true}`; name existing and missing requests in the journal and `next`. |
   | Permit asks remain open | `outcome: partial`, `awaiting: {permit: <ask ID>}`. Report every `permit_asks` entry, the exact batch/attempt and any credit shortfall to the orchestrator. |
   | Permit declined | Respect the no. Complete `partial` with the refusal and remaining state; do not launch again or change the request to avoid the decision. |
   | League not chosen/mismatched | Complete `partial` with the returned reason and a clear `next` for the orchestrator; do not guess a different league. |

   For combined credit/permit waits, report the permit in `awaiting` and the full combination
   in the experiment and `next`. Do not discard already launched handles.
6. Never wait for XP inside the card (D-11). The orchestrator watches the platform and
   commissions a review or another launch card. Only completed, inspected episodes count
   as evidence (D-06).

## Failure handling

- `identity`: the credential is not our Player. Send `help_request:` and block; do not switch identity.
- `no_binding`: use `build-upload` first if it is within this card's work.
- `baseline_not_champion`: nothing launched; do not edit a frozen design. Recommend a new
  experiment against the current champion in `metadata.next`, with hypothesis/experiment refs.
- `champion_unknown` or `champion_conflict`: report the current league/canonical mismatch
  and block for an orchestrator answer. Do not assume a separate process reported it.
- `experiment_closed`: the experiment is stopped/reviewed or a permit was declined. Preserve
  existing handles, report the state and finish without another launch.
- `permit_batch_mismatch`: use exactly the saved `batch`, `attempt` and `batches` when the
  orchestrator has commissioned the permitted retry. Never alter requests to bypass the wait.
- `permit_league_changed`: report `saved_league` and `configured_league`, then block for
  resolution; do not substitute a league.
- `empty_batch`: `--batches` exceeds the available seeds. Correct the slicing.
- `attempt_incomplete`: use the earlier attempt named by the tool to fill only its missing
  requests. `bad_attempt`: use the next valid attempt. `attempt_not_needed`: no relaunch;
  return existing handles for review.
- `busy`: do not duplicate a running command. If `details.own_run` is true, wait on your own
  process with `process_manage` as in `card`, then recover its handles. Otherwise report the
  holder and complete `partial`; do not loop or take its lock.
- A retryable `platform` refusal or interrupted launch can replay the same batch/attempt to
  create only missing requests. If it still fails, preserve the state and complete `partial`.
- A tooling traceback, an `errored` check or `tool_missing` follows D-26 in `card`. Do not retry it.
