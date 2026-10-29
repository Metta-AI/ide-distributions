# Spike S1 — dispatching Researcher Sessions through the Hermes runs API

**Question.** Can the watcher run several Researcher Sessions in one `researcher` profile
through `POST /p/<profile>/v1/runs` with `session_id`, steer and stop them, and resume a
Session after the gateway dies under its run, with each Session's history isolated from the
others? The design (B.13, H "Validation before and during the build") makes one profile hold
several Sessions on this answer. If isolation fails, the fallback is the Kanban model
(B.13): the Task table as the board and each run a fresh attempt continued from handoff
notes. That is a design change James decides, not something to patch around.

**Where.** `core/tests/hermes/test_spike_s1.py` (marker `hermes`, deselected by default).
It skips unless `HERMES_BIN` names a Hermes executable and `OPENROUTER_API_KEY` is set.

```
HERMES_BIN=<hermes> OPENROUTER_API_KEY=<key> uv run pytest -m hermes core/tests/hermes/test_spike_s1.py
```

The OpenRouter key is vendable through the token broker (`openrouter.inference`, auto tier):

```
cd ~/coding/metta && python3 scripts/token_broker_client.py exec --scope openrouter.inference \
  --reason "spike S1: Hermes runs API dispatch" -- \
  env HERMES_BIN=<hermes> uv run --project <this checkout> pytest -m hermes core/tests/hermes/test_spike_s1.py
```

## What the test does

1. Installs `orchestrator/` and `researcher/` into a temporary host home with
   `hermes profile install`, writes one `API_SERVER_KEY` (32 hex characters), the free
   `API_SERVER_PORT`, `API_SERVER_ENABLED=true` and the OpenRouter key into every profile's
   `.env` (A5.8), and runs each profile's `prepare.py`.
2. Starts `hermes gateway run` in a subprocess with `GATEWAY_MULTIPLEX_PROFILES=true` and the
   same `API_SERVER_*` values in its environment, and waits for `GET /v1/capabilities` to
   answer 200 with the key.
3. Starts three runs in the `researcher` profile with three minted session ids
   (`api_<epoch>_<8hex>`), each with the input "Reply with the word READY and stop." and an
   `Idempotency-Key`.
4. Steers the first run while it is `running`, stops the second, and kills the gateway process
   while the third is live.
5. Restarts the gateway, reads the third run's status back (durable because the start carried
   an idempotency key; the design expects `interrupted`), and resumes the third session id
   with a new run.
6. Waits for the remaining runs to end, then reads `GET /api/sessions/<id>/messages` for all
   three sessions and asserts: every session holds its own first prompt; only the first holds
   the steer text; only the third holds the resume text; the third has at least one assistant
   turn after the resume (history survived the restart).
7. Re-sends the resume with the same key and body and asserts the gateway replays the same
   run id; sends it with another body and asserts a 409 (`idempotency_key_conflict`).

## What the watcher relies on if this passes

- `resume_session` (`core/src/research_core/watcher/resume.py`): steer while `running`,
  otherwise start again with the same session id; the idempotency key per attempt makes a
  start after a crash replay the stored run instead of doubling it.
- `dispatch.py`: one `researcher` profile can hold `max_researchers` task Sessions at once.
- `recovery.py`: a gateway restart leaves `interrupted` runs behind, which recovery resumes on
  the same session id with "continue from your last commit".

## Result

<!-- s1:begin -->
Not run yet in this checkout: `HERMES_BIN` and `OPENROUTER_API_KEY` were absent when entry 5
was built (2026-10-28), so the test skipped cleanly. The test rewrites this block with the
outcome, the timings (install and prepare, gateway ready, three starts, gateway restart,
kill-to-resume) and the per-step notes when it runs.
<!-- s1:end -->

If the result is FAIL on isolation (a session's history shows another session's text, or the
resumed session lost its history), stop and report to James: the Kanban fallback is the
decision to make. A FAIL on timing or on the kill-and-restart path is a hosting or
configuration problem to diagnose first (`gateway.log` in the test's temporary directory
holds the gateway output).
