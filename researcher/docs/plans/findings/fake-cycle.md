# Finding: full research cycle on the fake game (Plan 03 Task 6.0)

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

**Verdict: PASS.** Runs 1 and 3 passed every assertion (run 3 with both product fixes of the run, before review fix round 1); run 2 reached every stage, including a published promotion, but failed the no-duplicate-XP assertion, which found bug 2. Each passing cycle took about 30 minutes of the 90-minute budget.
2026-10-08, run by Claude for James.

Test: `tests/integration/test_fake_cycle.py` (marker `hermes`, `ad5e8a9`). Run it with:

```bash
cd autoresearch
export OPENROUTER_API_KEY=...   # the test fails, not skips, without it
uv run pytest -m hermes tests/integration/test_fake_cycle.py -v -s
```

`FAKE_CYCLE_MAX_USD` (default 6) stops a run whose OpenRouter usage grows by more.

## Setup

- Hermes v0.21.5 (`v2026.9.24`, `f97608f`), editable uv tool. Fresh install of `git archive
  HEAD` into a throwaway `HERMES_HOME` (`conftest.install`), bare remote, fakegame,
  persistent FakeSoftmax.
- Workers: `z-ai/glm-5.3` through OpenRouter (`OPENROUTER_MODEL` in the profile `.env`),
  dedicated test key.
- Dispatcher and jobs: the real gateway (`hermes gateway run`), with the three shipped cron
  jobs resumed (queue-keeper every minute, xp-poll every 5 minutes, health). The test never
  runs a runtime job itself after bootstrap.
- No scripted card bodies. The real doctrine and skills. The plan is the seed `plan.md`.
- The one human input: a `steer` (urgency `next`) with a `proposed_topic` "Reduce errors",
  submitted before the first run. It names the problem (errors) and points at the fakegame
  `GAME.md` Mechanics section as tier A evidence, so the first hypothesis does not need a
  replay (fake XP stores no replays; see "Observations").
- Test shims (environment only): the host root `config.yaml` (`max_in_progress: 2`, 10 s
  dispatch tick; the installer's job, A3), a fake credential that names its player, the
  champion on the fake leaderboard (the seed STRATEGY built with the fakegame build binding,
  uploaded and submitted, as an operator's first submission would be), 6 fake credits at
  the start (the balance hits 0 after three seed pairs of the first launch) and a top-up
  once the experiment is `awaiting-credits`, and `AUTORESEARCH_CREDIT_BACKOFF_MINUTES=2`.

## What the cycle proves

Stage times are seconds after the gateway started.

| Brief item | Evidence | Run 1 | Run 2 | Run 3 |
|---|---|---|---|---|
| Planning creates a Topic and cards | planning request published; Topic from the steer's `proposed_topic`; steer acknowledged | 316 | 436 | 436 |
| An investigation produces a hypothesis | investigation request published; `H-…` (patience 5 to 8) citing GAME.md as evidence | 438 | 556 | 677 |
| Calibration writes a noise record | calibration card (admitted by C10 when the experiment request arrived) wrote `stats/metric-noise.yaml` for `fake-1` from league episodes (run 2: 400 rows kept) | 679 | 677 | 677 |
| Experiment freezes a design, builds, uploads, launches XP | `stats design` froze 40 seeds (`n_min` 32, primary `errors` down, min effect 1, performance guardrail `score`); fakegame build; `softmax upload`; `softmax xp create`, one 1-episode request per (seed, arm), seats `[0]`, opponent `fake-house:v1` (A12) | 799 | 1039 | 917 |
| `credits=0` mid-launch: `awaiting-credits`, resume, no duplicate XP | 6 requests, then denial; experiment `awaiting-credits`; relaunch created only the missing 74 | 1147 (no duplicates) | 1159, **6 duplicates** (bug 2) | 1159 (runtime "Launch E-…" card, attempt 1; 80 requests, no duplicates) |
| xp-poll requests the review | runtime request `[review:<E>:n80]` | 1404 | 1402 | 1402 |
| Review writes a Result and a decision | Result `R-…` linked; decision `promote` (errors −3.05, CI [2.7, 3.4]; score diff 0.0) | 1631 | 1629 | 1750 |
| Promotion submits once and reaches `published` | promotion card ran `softmax submit`; FakeSoftmax submissions = champion + the candidate; champion `strategy/STRATEGY.md` = the candidate | 1752 | 1750 | 1871 |

## Wall time and cost

Hermes estimates (last column) are Hermes's own per-session cost estimates; they add up to about
$1.27 and are not comparable with the key's usage below, which is higher and may also carry
other agents' calls on the same key.

| Run | Result | Cycle | Sessions | Tokens (input / output / cache read) | Hermes estimate |
|---|---|---|---|---|---|
| 1 | **PASSED** | 29.2 min | 8 | 656k / 42k / 5.9M | $0.48 |
| 2 | Failed one assertion: 6 duplicate XP requests (bug 2). Every stage reached, promotion `published`. | 29.2 min | 8 | 560k / 36k / 5.0M | $0.40 |
| 3 | **PASSED** (on `ad5e8a9`+`4569558`; before review fix `c2443d6`/`9cce176`) | 31.2 min | 7 | 415k / 35k / 5.1M | $0.39 |

OpenRouter key usage for the whole task: **$2.89** by `GET /api/v1/key` (usage 2.908 before
run 1, 5.797 after run 3). Budget was $15. The key's `usage` lags by minutes, so a per-run delta can read 0.

## Bugs found and fixed (each its own commit with a test)

1. **The fake game had no lever** (`666c4ca`). Fake XP results were hashes of (seed, policy),
   so no candidate could ever be significantly better and the cycle could not reach
   `promote`. `fake_build` now records the STRATEGY parameters in `policy.json` (and accepts
   the candidate `STRATEGY.md` or its directory, which is what the experiment skill passes);
   FakeSoftmax plays seeded episodes by those parameters (patience lowers errors and leaves
   score unchanged; aggression shifts score); `GAME.md` documents seats, opponents,
   mechanics and binding calls.
2. **A relaunch could repeat every XP request** (`4569558`, found by run 2). After the credit
   denial the design card requested its own "relaunch" card, which ran `xp create --attempt
   2`: all 80 requests again, 6 of them duplicates of attempt 1. Nothing enforced the
   attempt rule (`lifecycle.launch_attempts` is never incremented, so the attempt was a free
   model choice). `xp create` now refuses a new attempt while the batch's latest attempt
   lacks requests (`attempt_incomplete`, naming the attempt to rerun) and an attempt that
   skips a number (`bad_attempt`). Review fix round 1 (code in `c2443d6`, tests in `9cce176`) closed the rest of the
   hole: a complete attempt could still be repeated (the runtime relaunch completes attempt
   1, then a self-requested card runs `--attempt 2`). A new attempt now also needs a failed
   or cancelled request in the latest attempt (`attempt_not_needed` otherwise), so relaunches
   that run one after another never repeat a healthy attempt. Not covered then: two
   relaunchers at the same moment (no cross-process lock), and a `--batches` change between
   launches. The first is now closed: `xp create`, `upload` and `submit` hold a
   per-experiment file lock (`.supervisor/runtime/locks/xp-<E>.lock`), so a second process
   waits, then refuses `busy` (worker-to-worker-comms step 1; two-process tests in
   `tests/unit/softmax/test_softmax_cli.py`). A `--batches` change is still not covered.
   Run 3 ran with the first guard but predates the second, and no self-requested relaunch
   happened in it, so neither guard was exercised: its "no duplicate XP" result rests on the
   agent's behaviour in that run, not on the guards. The guards are proven by unit
   tests (`tests/unit/softmax/test_softmax_cli.py`), not by a rerun of the paid cycle.

Skill fix:

3. **`kanban_complete` without `summary`** (`66b24cb`, found by run 1). The investigation
   worker called `kanban_complete` with `{metadata: {}, board: "default"}` eight times;
   Hermes refused each ("provide at least one of: summary (preferred), result"), the tool
   loop guard stopped the run, and Hermes spawned a second run only to complete the card.
   `complete-card` now says the summary is required, `board` stays unset, and shows the call.
   Run 2 still had glm-5.3 omit the summary on two cards (one recovered in the same run, one
   needed a second run); see "Observations".

Operational knob (not a bug): `AUTORESEARCH_CREDIT_BACKOFF_MINUTES` (`70e6093`) sets the
first A10 relaunch wait (default 30 minutes, doubling, cap 8 hours). The test sets 2 so the
relaunch fits in 90 minutes.

## Observations (no change made)

- **glm-5.3 often omits `summary` in `kanban_complete`** even with the skill text (run 2:
  2 of 8 cards; run 3: none). The cost is a repeated call or a second run of the card, not lost work: the
  handoff is committed by `card finish` first. Repeated failures also fire the
  `repeated_tool_error` signal, which escalated to runtime `help_request` messages in run 1.
  A plugin cannot add the field to the call; if this persists with kimi-k3, a plugin block
  message with the exact call shape is the next step.
- **The design card requested its own relaunch card** (run 2) although `xp-request` says the
  XP poller makes it. With bug 2 fixed (both rounds), such a card can only fill the missing
  requests of the latest attempt, or start a new attempt after a failed request.
- **No replay before the first experiment.** D-05 asks for a replay before a strategy change,
  and XP is the only replay source. A fresh install has no XP yet, so the first hypothesis
  needs another source (here the steer named the documentation). On GotA the first cycle
  will hit the same question; the real-cycle task (6.1) should decide whether a
  baseline-only XP or the docs/source (tier S/A) count.
- **Experiment card did the whole launch in one card** (design, build, upload, XP) in all
  runs, as the experiment skill asks.
- `n_min` came out 32 from league noise (errors paired sd about 2.0, min effect 1.0); the
  agent chose 40 seeds. The XP result was far stronger than the minimum (errors −2.6 to −3.05
  per episode, score unchanged), so the decision never depended on the noise estimate.
