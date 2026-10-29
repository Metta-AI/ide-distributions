---
name: credits
description: Use before spending XP credits or after a credits refusal.
---

# Credits

XP requests spend the account's credits. Credits are the platform's: you cannot buy them, and a refusal for want of them is a wait, not a broken tool.

## Know the balance

`platform.credits` gives the balance, the amount held by pending requests, the next refill (amount and time) and the recent requests with their compute cost. Read it before any batch and whenever a result's credits line looks low. The briefing carries the last reading.

## Know the cost

- A batch costs seeds × arms × episodes requests. The `xp.request` result carries an `estimate` from the recent cost per episode (or the mixin's `cost_per_episode` when there is no history) next to the balance that is left.
- Before a verdict-sized Experiment, compute the estimate yourself from `platform.credits` and `n_min`; when it exceeds the balance, shrink the seeds, split the batch keeping both arms of each seed together, or wait for the refill. Decide this before `experiment.start`, not after half the pairs ran.
- Held credits return when a request finishes or is cancelled.

## Tell the user when low

- The Orchestrator: `ui.notify` with the balance, the refill time and what waits on it, once per change, not every turn.
- A Researcher: say it in the Task's result, or `task.ask` when the Experiment cannot finish on the balance.

## A refusal

There is no local pre-check: the platform's 402 is the refusal, and the tool reports the balance, the held amount and the next refill with it.

- A Researcher: `task.wait {task, kind: credits, detail: "XP for X-5: <n> requests, about <cost>"}` and end the run. The watcher resumes you when the balance covers it. Requests already accepted stay recorded as runs; `xp.request` again creates only the missing ones.
- The Orchestrator: tell the user (`ui.notify`), finish other work, and request again after the refill. A user's "go ahead" changes nothing here: the platform decides.

## The per-Experiment setting

`ask_above_requests_per_experiment` (read with `settings.read`) bounds your own initiative: above it, the Orchestrator gets a warning and proceeds; a Researcher is told to `task.ask`. It never refuses, and a user request overrides it.

## Failure handling

- `platform.credits` unreadable: `xp.request` still runs; the platform's answer decides.
- A request pending for long holds credits: `xp.status {request}` refreshes it; cancel through `softmax_api {method: POST, path: /v2/experience-requests/<xreq>/cancel}` only when the Experiment abandons it.
