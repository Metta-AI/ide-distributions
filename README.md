# ide-distributions

Hermes Agent profile distributions for the Softmax IDE. Two research profiles share one package;
a third, `play/`, stands alone.

| Folder | What it is |
|---|---|
| [`core/`](core/) | `research-core`, the shared package: the record store, the record and platform tools, the Hermes plugin wiring, the watcher, the platform client (real and fake), the `research` CLI, the prompt parts and the skills. |
| [`orchestrator/`](orchestrator/README.md) | The research lead: the profile the user talks to. Plans Goals and Tasks, answers the Researchers, makes and submits policy Versions, runs the watcher. |
| [`researcher/`](researcher/README.md) | A research worker. Each Session does one Task in the background and speaks only to the Orchestrator. |
| [`play/`](play/README.md) | Play: the user's own program on one coworld. Independent of the research profiles; it keeps its own vendored copy of the former shared code. |

The research profiles follow the
[IDE profile refactor design](docs/designs/ide-profile-refactor-2026-10-28-final.html) (open it
in a browser). The implementation plan, one file per stack entry, is in
[`docs/plans/ide-profile-refactor/`](docs/plans/ide-profile-refactor/00-overview.md); the
overview fixes the architecture decisions the design leaves open.

The rebuild landed on October 28, 2026 (Pacific). What hosting needs from these profiles, and
what they guarantee, is in [`docs/hosting.md`](docs/hosting.md); the retired profiles' notes
under `docs/` carry a banner saying so.

## How the pieces fit

- Each profile folder is a thin Hermes distribution: a manifest, a config, a generated `SOUL.md`,
  a plugin shim, scripts and profile configuration. Hermes copies the folder on
  install; the shim imports `research_core` from the release checkout, whose path `prepare.py`
  records.
- Two Python environments exist: Hermes's own interpreter runs the plugin (the stdlib-only base
  of `research_core`), and one *workspace environment* under `$IDE_WORKSPACE/tools/venv` runs
  the `research` CLI, the watcher and every tool that needs the Softmax client. The repository
  root `pyproject.toml` and `uv.lock` define that environment; `prepare.py` builds it.
- Every profile of one sandbox shares one workspace (`IDE_WORKSPACE`): a workspace store, and one
  folder per Coworld with the policy repository, worktrees, documents and the Coworld's store.

## Install

```bash
git clone https://github.com/softmax-universe/ide-distributions.git ~/ide-distributions
hermes profile install ~/ide-distributions/orchestrator --name orchestrator --yes
hermes profile install ~/ide-distributions/researcher --name researcher --yes
# write each profile's .env (distribution.yaml lists the variables; IDE_WORKSPACE must be the
# same path for both), then prepare each profile with Hermes's Python:
<hermes python> ~/.hermes/profiles/orchestrator/scripts/prepare.py \
  --source ~/ide-distributions/orchestrator --hermes $(command -v hermes)
<hermes python> ~/.hermes/profiles/researcher/scripts/prepare.py \
  --source ~/ide-distributions/researcher --hermes $(command -v hermes)
```

Run `prepare.py` again after every `hermes profile update` (which keeps `config.yaml`; prepare
re-applies the keys the machinery relies on and removes retired ones).

## Develop and test

```bash
uv sync                 # the workspace environment plus pytest and ruff (Python 3.12)
uv run ruff check .
uv run pytest -q        # unit and scenario tests; the fake platform needs no network
HERMES_BIN=/path/to/hermes uv run pytest -q -m hermes core/tests/hermes   # against a real Hermes
```

CI runs the same inside the hosted Hermes image, and `play/` separately. A daily workflow
refreshes the `coworld` and `softmax-cli` pins in `uv.lock`.

The model tool-call tests in `core/tests/model` call models through OpenRouter and spend real
money, so they run only on request and never in CI. An explicit `-m` replaces the default
`-m 'not hermes and not model'` rather than adding to it, so `uv run pytest -m "not hermes"` with
`OPENROUTER_API_KEY` exported runs these tests too and spends money.

**Direct calls** (`test_write_actions.py`). `IDE_HERMES_SRC` names the pinned Hermes's source
(`/opt/hermes` in the image), whose schema sanitizers shape the tool list as Hermes sends it. Narrow a run to one model and host: the
default is four models on two hosts each, and one such column costs about $2 on Kimi K3.1 Pro.

```bash
OPENROUTER_API_KEY=... IDE_HERMES_SRC=/path/to/hermes-agent \
  IDE_TOOL_CALL_MODELS=moonshotai/kimi-k3.1-pro IDE_TOOL_CALL_HOSTS=shipped \
  uv run pytest -m model core/tests/model/test_write_actions.py
```

It sends one write action with required fields per case (claims, Goals, Tasks, experiments,
directives, settings, notices), for both roles, offering the role's whole tool list as a Hermes
session does, plus cases that start after one and after three refused calls. Each case runs 5
times by default, and the closing table gives every rate its sample size and 95% Wilson interval:
intervals that do not overlap show a real difference, and overlapping ones decide nothing either
way. It also lists calls whose reasoning names
a field the arguments omit. `core/tests/model/write_actions.py` says which write actions are
covered and how a call is judged. `IDE_TOOL_CALL_MODELS`, `IDE_TOOL_CALL_HOSTS`,
`IDE_TOOL_CALL_CASES`, `IDE_TOOL_CALL_REPEATS`, `IDE_TOOL_CALL_PARALLEL`, `IDE_TOOL_CALL_SCHEMAS`
and `IDE_TOOL_CALL_CONTEXT` narrow or change a run (see `test_write_actions.py`).

**Through a real Session** (`test_real_session.py`). A real Hermes gateway serves both profiles
with the fake platform, so the system prompt, tool list, hooks and tools are the real ones. It ends
with `test_the_agreed_rate`: at least 95% of calls valid on the first try, after one refusal and
after three. This is the check to pass before calling the tools reliable. Run it in the pinned
image, from the repository root (about $4 and 10 to 20 minutes at the default 10 repeats):

```bash
out=$(mktemp -d) && chmod 777 "$out"  # the container's user writes here
OPENROUTER_API_KEY=... docker run --rm --user 10000:10000 -e OPENROUTER_API_KEY \
  -e IDE_TOOL_CALL_CONTEXT=/out/context.json -v "$PWD:/src:ro" -v "$out:/out" \
  --entrypoint bash <the digest in image/Dockerfile> \
  /src/core/tests/model/in_image.sh -m model core/tests/model/test_real_session.py
```

`IDE_TOOL_CALL_CONTEXT` is optional and writes the real system prompt and Hermes tool list to that
path. Give the same variable to the direct calls, pointing at `$out/context.json`, to send
them in place of the short stand-in prompt. That is also the only way to measure a refusal inside
one turn: the runs API can only continue a stored history with a new user message, and a model
recovers far more often after one (40/40 after three refusals, against 22/60 inside a turn,
November 4, 2026).

**The other two.** `test_undeclared_fields.py` checks that a model on a closed schema sends a field
only if it is declared, so a provider change is noticed. `test_tool_call_format.py` sends one
instruction per tool, on the shipped models and pins.

The default `pytest` needs no network and covers the judging rules, every `tool.action {fields}`
signature and its example values in the prompts, skills, task types and wakes against the specs
(`core/tests/test_tool_signatures.py`), and every field a validator requires against the schemas
(`core/tests/tools/test_tool_schemas.py`).

Hermes freezes a Session's tool list until a release's boot refreshes it (`docs/hosting.md`,
"Open chats after a release"). To compare a Session's
frozen list with the deployed validator, on a copy of a profile's `state.db`:

```bash
uv run python tools/schema_drift.py /path/to/state.db --role orchestrator [--session <id>]
```

`test_usability.py` measures how easy the research tools and skills are to find, read and use for
Kimi K3.1 Pro, with a session's real system prompt and tool list. It compares a run with
`core/tests/model/usability_baseline.json` to show what a tools or skills change did;
[`docs/reports/tool-usability-baseline-2026-11-04.md`](docs/reports/tool-usability-baseline-2026-11-04.md)
is the first run, how to read it, what it cost and what it took. That baseline predates the test's
realistic read results, so rerun it before comparing. The offline tests of its judging and read
results run in the default `pytest`:

```bash
OPENROUTER_API_KEY=... IDE_HERMES_SRC=/path/to/hermes-agent \
  uv run pytest -m model core/tests/model/test_usability.py -k measure
```

To run the profiles end to end on your Mac against a real Hermes gateway (one command, an
interactive session, or inside the hosted image), see [`docs/local-end-to-end.md`](docs/local-end-to-end.md).

## Hosting

Softmax hosting runs these distributions in its sandboxes. [`docs/hosting.md`](docs/hosting.md)
is the handoff: the image, `prepare.py`, the environment variables, the gateway, sleep and
drain, the bridge's seam, storage and releases. Changes to `core/`, `orchestrator/`,
`researcher/` or the root lock on `main` dispatch Metta's image release workflow
(`.github/workflows/release-hermes.yml`); a nightly workflow runs the read-only platform check.
