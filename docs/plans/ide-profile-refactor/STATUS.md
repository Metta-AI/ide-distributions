# Build status (checkpoint, 2026-10-28 20:15 PDT)

Branch `jb/idd-profile-refactor`, not pushed. One commit per task; `uv run pytest -q` from the
repo root runs everything committed (≈1,100 tests at this checkpoint).

## Landed (entries 1–4 complete; parts of 3, 5, 6 and 7)

| Entry | Task | Commit |
|---|---|---|
| 1 | teardown, package, skeletons, profile shells, prepare v1, CI | `50a877d` … `2cae633` |
| 2 | store foundation, registry, goals/directives/coworlds, tasks/settings, claims, experiments, versions/champion, capabilities/intents/sessions/ui_state/notices/pins, refs, store CLI, migration 0002 | `4920a04` … `6a2d37e`, `f77f6d3` |
| 3 | tool base, goal/directive, claim/ui, task/settings, experiment, advisory hook, turn hook + session context, briefing | `b15091f` … `547dcb1` |
| 4 | platform protocol/real/fake, mixin + gitops, stats, xp + credits, platform/softmax_api/github/attachments, Coworld switch + setup + workspace tool | `108cdff` … `d6ec86b`, `e897f01`, `66a1f52` |
| 5 | Hermes runs client (5.1), tick context | `c7c8cb1`, `c567cd3` |
| 6 | contract schemas + validator + `research context-schema` | `e4d6ba3`, `7708ba7` |
| 7 | SOUL parts and generator, manifests (7.4) | `7711709` |

## In flight when the API rate limit hit (resets 2026-10-28 20:30 PDT)

Uncommitted files in the working tree belong to these tasks; resume the agent that owns each
(SendMessage), do not restart from scratch.

| Task | Owner agent (session-internal id) | State |
|---|---|---|
| 4.4 version.create/adopt, upload, submit, submission status, intents reconcile | `aec891ed5cd7718e3` | files written, fixing tests (`tools/version.py` had a syntax error at ~253, now tolerated by the catalog) |
| 5.2 tick, handlers, wakes, heartbeat, store checks, watcher CLI (W1) | `a61f229220c65fb0e` | running |
| 5.3–5.6 dispatch, recovery, waits, messages, resume, S1 spike (W2) | `ab08abdf216a09084` | running |
| 5.7–5.9 merge, goals, champion sync, checks, context panel, drain (W3) | `a77ac2372df93c126` | cut while reading; nothing written |
| 6.1–6.3 `research serve`, scripted client, CONTRACT.md | `a738b66e0d9ee9c09` | cut while reading; nothing written |
| 7.3 common skills | `a1d5f41b3b4e144ef` | cut while reading; nothing written |
| 7.2/7.3 Orchestrator + Researcher skills, client guides, task type bodies | `abe48af185e36df7c` | cut while reading; nothing written |

## Still to do after those

- 7.1 forum_post and wiki_edit tools (then `tools/build_prompts.py --write --manifests`; the budget test then asserts 18/14).
- 5.10 watcher scenarios; 6.3 acceptance scenario on the fake platform; 6.4 real-Hermes run (needs `HERMES_BIN` and an OpenRouter key; the broker scope `openrouter.inference` is auto-tier).
- 7.5 wake prompt final text; 7.6 prompt checks in CI; 7.7 lock-refresh finish + CLI staleness; 7.8 prepare final (registry-aware skill dirs, `IDE_DEFAULT_COWORLD`, retired keys, `EARLIER_DEFAULTS`); 7.9 docs (README, hosting.md, profile READMEs, core/README.md); 7.10 verification.
- A7.1 nightly platform check workflow; A7.2 tool-call format tests (model-gated); `tools/catalog` executor-table assertion and the stdlib guard over the whole catalog (A3.1).
- Known seams to resolve: `prepare.apply_config` resets `skills.external_dirs` (must read the registry, A5.9); `experiments` has no way to set `methods.candidate` after start (the experiment tool falls back to the Version built for the Experiment).
