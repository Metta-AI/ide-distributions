# Tool and skill usability, November 6, 2026

The closing measurements for
[Asana 1220231786162236](https://app.asana.com/0/0/1220231786162236/f/). All are Kimi K3.1 Pro on
Moonshot. The final runs are at `6a94aa90`, the commit `usability_baseline.json` records. Intervals
are 95% Wilson. How to read a run is in
[`tool-usability-baseline-2026-11-04.md`](tool-usability-baseline-2026-11-04.md).

## The short version

- **The agreed rate holds through a real Session on final main.**
- **`task.create` stays in the grouped `task` tool.** Its own tool failed one of three bars set before
  any result.
- **The wait-first prompt for a credit refusal is not adopted.** It scored better than today's text
  but below 95%. This is a known weak spot, not fixed.

## Final runs at `6a94aa90`

| Test | First try | After 1 refusal | After 3 refusals | Cost |
|---|---:|---:|---:|---:|
| `test_real_session.py` | 120/120 | 40/40 | 39/40 | $3.82 |
| `test_mid_turn_recovery.py` | - | 40/40 | 39/40 | $0.87 |

The bar is at least 95% valid at each refusal depth. The earlier 37/40 after one refusal did not
repeat. These two tests have small samples: 39/40 and 40/40 cannot tell 99% from 95%.

The usability baseline, 905 runs, $37.75:

| | Nov 5 | Nov 6 |
|---|---:|---:|
| Right tool and action, all | 832/905 | 846/905 |
| Orchestrator | 604/625 | 610/625 |
| Researcher | 228/280 | 236/280 |
| First try valid | 819/821 | 822/829 |
| Arguments as the rule says | 137/144 | 136/143 |
| Comprehension | 655/675 | 660/660 |

Do not read this as a before and after: the two ran on different read results and fixtures, and the
comprehension questions changed. New in this baseline: of 24 runs that ended on a wrong write, 9 would
really have applied, 15 have no store check here, and no right act would be refused.

## `task.create` as its own tool

The bars were set before any result. The arms were `846b09ff` plus the ordinary-creation test
(`4dbc01c9`) and that plus `task_create` (`8815fb7f`), on branch `tools/task-create-own-tool`.

| Bar | Result | Passed |
|---|---|---|
| Forced `task_create` at least 95% at both depths, n of at least 100 | 99/100 and 100/100 (the grouped form was 172/190 after one refusal) | Yes |
| Recovery from the old grouped form at least 90%, n=100 | 80/100 and 69/100 | **No** |
| Ordinary creation not worse than control | 119/120 both | Yes |

Ordinary creation shows no gain, and it is the common case. Open chats keep the grouped form in their
history after a refresh, and from that history the model often assumes the refused call worked and goes
on to assign or approve the Task. So the split would trade a forced-refusal gain for a recovery loss. The
ordinary-creation test is kept (`test_ordinary_creation.py`).

## A credit refusal

The fixture was corrected in `95377a74`, so these replace the November 5 rates. `r-wait-credits` and
`r-hard-wait-credits-args` are one conversation judged two ways, so the pooled figure is the fair one.
n=100 each, at `846b09ff`, $3.44 and $2.46.

| Arm | `r-wait-credits` | `r-hard-wait-credits-args` | Pooled | Would really apply |
|---|---:|---:|---:|---:|
| Today's prompt | 81/100 | 79/100 | 160/200 (80%) | 6 |
| Wait-first prompt | 93/100 | 83/100 | 176/200 (88%) | 7 |

The bar was at least 95% and better than control, so the prompt is not adopted. The refusal already says
to call `task_wait`. The most common miss is `task.complete` (13 of 40 misses on today's prompt, 14 of 24
on wait-first), 8 each with `outcome: failed`, often with a note to rerun after the refill. That ends a
Task that a wait would have resumed. On today's prompt, 9 runs also retried `xp.request` and 10 hit the
request limit.

## Open issues

Tools and text:

- **A credit refusal can end a Task**, as above. The untried option is for the tool to refuse or warn on
  that `task.complete`, rather than more text.
- **Finding the right tool is the weak part for the Researcher**: 236/280, with 31 runs across all roles
  ending at the limit of 8 requests. Lowest at n=5: `r-expert` 0, `o-expert`, `r-hard-fail-args` and
  `r-hard-no-version-maintenance` 1. `r-hard-fail-args` is ambiguous, as the November 5 report says.
- **`settings.update` names and values are guessed**: 5 of the 7 invalid first tries (unknown setting
  names, or `anti_stall.hour` not a time of day). The other 2 sent `platform` a field that action lacks.
- **The audit is not exhaustive.** Its last re-audit found 40 more faults, and the readers agreed on 1 of
  them, so more remain. Its recorded exceptions are on Asana 1220232591962903.
- **No real league `xp.request` has been sent.** It needs credits.
- **A chat using the refreshed tools has not been seen live.** On one workspace on November 6, the boot
  refreshed all 113 chats, and the stored hash matched the release. No turn was sent after it. That last
  step is shown by `core/tests/hermes/test_frozen_sessions_real.py`.

Reliability, found along the way and outside this task's goal:

- A failure in an AIDE consumer, or a seam error answer to a consumer or the event watcher, ends the
  backend, and if it lasts it crash-loops the workspace (`image/README.md`).
- An unexpected failure in one AIDE request no longer ends the backend (#217). Repeated failures are
  only log lines: nothing limits or alerts on them.
- `research store quiesce`: a write already waiting when a hold begins can recycle the runtime (1 of 25
  holds at about 60 writes a second), and a Coworld registered during a hold is not locked. No caller was
  found in this repo or under metta's `clients` and `devops`.
- Backup copy order and a lost retry record are unaddressed. Hermes's `state.db` waits of 20 seconds
  against a 30-second hold are untested.

Elsewhere:

- Metta's `packages/softmax-cogent/README.md:71` links to the removed `play/SOUL.md`.
- Open PRs #111 and #115 change `play/`, which no longer exists.
