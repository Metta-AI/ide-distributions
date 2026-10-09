---
name: xp-request
description: Use when launching or relaunching an experiment's XP.
---

# XP request

This skill implements the launch steps of System Design §5.4: create XP requests per the
decision rule, handle a credit refusal, record the handles, and set `evaluating`. It adapts
optimizer-seed `run-eval` and the co-gas custody rules. Use it in a design card after
`build-upload`, and in a "Launch E-…" card.

Doctrine upheld: D-03, D-06, D-11, D-21.

## Reads and writes

- Reads: the experiment (its frozen design and candidate binding), credits.
- Writes: XP requests, their handles in `experiment.xp_requests`, the experiment status,
  and the journal.

## Procedure

1. Find the attempt number. A "Launch E-…" card names it in its reason. Otherwise it is
   `lifecycle.launch_attempts + 1` of the experiment (1 for a new experiment). Do not change
   `lifecycle.launch_attempts` yourself.
2. Recover first. Requests that already exist must not be created again:
   `"$AUTORESEARCH_CLI" softmax xp recover <E-id>`
   Check the running peers too (D-21): `"$AUTORESEARCH_CLI" queue list --state claimed`. If
   another card names this experiment, do not launch it as well: record that in the journal
   and finish `partial`. `last_heartbeat_at` is in epoch seconds.
3. Optionally read credits (the account's XP credit balance; `available: false` means the
   read failed, which is not an error):
   `"$AUTORESEARCH_CLI" softmax credits`
4. Create the requests. Each seed of the design gets one candidate and one baseline request
   with one episode, so the pair plays the same seed:
   `"$AUTORESEARCH_CLI" softmax xp create <E-id> --batch 1 --attempt <n>`
   To launch the seeds in slices, add `--batches <K>` and run batches 1 to K. The tool
   creates only the requests that are missing, records each handle in the experiment at
   once, and sets the status to `evaluating`.
5. Read `data`:
   - `credit_denied: false`: every request exists (`created` and `existing`). Write the
     request count, `pairing`, and attempt in the journal. Finish the card `done` with
     `complete-card`.
   - `credit_denied: true`: stop here. The tool already set `awaiting-credits` and
     `lifecycle.next_launch_after` (the backoff). Requests already created stay recorded;
     `missing` lists the rest. Finish the card `partial`. The XP poller makes a new launch
     card when the credit balance is above 0 again (or after the backoff, when the balance
     cannot be read). Do not create a launch card yourself.
6. Never wait for XP in the card (D-11). The runtime makes the review card when every
   request is terminal. Results count as evidence only when completed and inspected (D-06).

## Failure handling

- Reason `identity` (D-03): stop. Send a `help_request` that names the problem (see
  `communicate`), and finish `blocked`.
- Reason `no_binding`: the experiment is not uploaded. Run `build-upload` first.
- Reason `baseline_not_champion`: the design's baseline is no longer the current champion
  (another policy was promoted since the design froze), so this experiment can never
  promote and nothing was launched. Do not edit the design (D-09). Record it in the journal,
  request a new experiment for the same hypothesis against the current champion
  (`"$AUTORESEARCH_CLI" queue request create --title "Experiment for <H-id>" --mode experimentation --why "champion changed; redesign against it" --ref ar://hypothesis/<H-id>`),
  and finish `done`.
- Reason `champion_unknown` or `champion_conflict`: the league shows no champion, or the
  league and the loop disagree (A9). Nothing was launched. Record it and finish `blocked`
  (for a conflict the runtime already told the user; for no champion send a
  `help_request`).
- Reason `empty_batch`: `--batches` is larger than the seeds allow. Use fewer batches.
- Reason `attempt_incomplete`: an earlier attempt of the batch still lacks requests (for
  example after a credit denial). Rerun `xp create` with the attempt the message names. It
  creates only the missing requests. A new attempt would repeat every request.
- Reason `busy`: a process is running `xp create`, `upload` or `submit` for this experiment
  now; `error.details.holder` names its card, run, pid and command. If `details.own_run` is
  `true`, it is your own earlier command (for example one that went to the background): wait
  for it to finish and use its result; do not rerun it and do not finish `partial`. To wait,
  call the native `process_manage` tool with action `wait`, that command's `session_id` and
  `timeout` 1800 ("Long commands" in the `queue` skill), then read the result with
  `"$AUTORESEARCH_CLI" softmax xp recover <E-id>`.
  Otherwise another card holds it: do not loop, record it in the journal, and finish
  `partial`.
- Reason `bad_attempt`: the attempt skips a number. Use the attempt the message names.
- Reason `attempt_not_needed`: the latest attempt is complete and nothing failed. Do not
  relaunch; the runtime makes the review card.
- Reason `platform` (a refusal, not a tool fault), or a crash mid-batch: run the same `xp create` command again with the
  same `--batch` and `--attempt`. It creates only what is missing. After a second failure,
  finish `partial` and say so in the journal.
- Lost handles: `softmax xp recover <E-id>` lists every request tagged for the experiment,
  and `xp create` backfills any missing from the record.
- A traceback, an `errored` gate, or a CLI failure of our own tooling is a tool fault: do not retry it. Follow the D-26 sequence (see `complete-card`, "Tool fault").
