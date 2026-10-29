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
  a plugin shim, scripts and (Orchestrator) the `watcher` cron job. Hermes copies the folder on
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
git clone https://github.com/Metta-AI/ide-distributions.git ~/ide-distributions
hermes profile install ~/ide-distributions/orchestrator --name orchestrator --yes
hermes profile install ~/ide-distributions/researcher --name researcher --yes
# write each profile's .env (distribution.yaml lists the variables; IDE_WORKSPACE must be the
# same path for both), then prepare each profile with Hermes's Python:
<hermes python> ~/.hermes/profiles/orchestrator/scripts/prepare.py \
  --source ~/ide-distributions/orchestrator --hermes $(command -v hermes) --activate-supervisor
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

## Hosting

Softmax hosting runs these distributions in its sandboxes. [`docs/hosting.md`](docs/hosting.md)
is the handoff: the image, `prepare.py`, the environment variables, the gateway, sleep and
drain, the bridge's seam, storage and releases. Changes to `core/`, `orchestrator/`,
`researcher/` or the root lock on `main` dispatch Metta's image release workflow
(`.github/workflows/release-hermes.yml`); a nightly workflow runs the read-only platform check.
