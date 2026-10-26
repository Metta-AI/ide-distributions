# ide-distributions

Hermes Agent profile distributions for the Softmax IDE. Each top-level folder that holds a
`distribution.yaml` is one distribution, and it installs and tests on its own.

| Folder | What it is |
|---|---|
| [`orchestrator/`](orchestrator/README.md) | The research lead: the IDE chat's agent. Plans research, assigns Kanban cards to researchers, answers them, submits champions and tells the user what happened. |
| [`researcher/`](researcher/README.md) | A research worker (formerly `autoresearch`, moved here from `Metta-AI/ide-mvp` with its full history). Install it once per researcher (`researcher-1`, `researcher-2`, ...). Each card is on one game: a shipped one (`games/`), or one a card made and the orchestrator installed on the host. |
| [`play/`](play/README.md) | Play: the user's own program on an explicitly selected coworld (bundled GotA build support). The `play` profile takes the user's request, has the `builder` profile ([`play/builder/`](play/builder)) change the program through native Kanban, uploads it, practices it and releases it when the user says so. No research machinery. |

Shared code lives once in [`shared/ide_research_core/`](shared/ide_research_core) and is copied
into each distribution's `src/` by [`tools/sync_shared.py`](tools/sync_shared.py); the research-method
skills in [`shared/skills/autoresearch/`](shared/skills/autoresearch) are copied into both research profiles' `skills/`, because
Hermes installs a distribution by copying its one folder. The built-in knowledge
([`ontology/KNOWLEDGE.md`](ontology/KNOWLEDGE.md)), the Softmax vocabulary
([`ontology/VOCABULARY.md`](ontology/VOCABULARY.md), refreshed from metta by `tools/refresh_vocabulary.py`) and the
research ontology ([`ontology/ONTOLOGY.md`](ontology/ONTOLOGY.md)) are embedded by the same script at the end of
each research distribution's `SOUL.md`, so every session of every research profile has it. The copies are
generated: edit `shared/` or `ontology/`, run `python3 tools/sync_shared.py`, and commit the
source and the copies together.

The [orchestrator design](docs/designs/orchestrator-design-2026-10-15.html) (open it in a
browser) describes how they work together on one host: one Kanban board, one bare canonical
git repository per game, the orchestrator between the IDE chat and the researchers.
[Multi-game research](docs/designs/multi-game-research.md) describes how one host works on
several games and how a new game's folder is made (from its GitHub source, adapting
optimizer-seed's mixin method) and installed.
[Implementation notes](docs/designs/orchestrator-implementation-notes.md) record where the
code differs from it.

Softmax hosting runs these distributions in its sandboxes. The metta changes it needs are in
the [hosting handoff](docs/hosting.md).

## Install

Profiles have no default game or league. The frontend supplies current selections as context;
the user can change the subject in conversation. Bootstrap creates neutral records; explicitly
select game support with `research workspace bootstrap --game <name>`. Keep `RESEARCH_GAME`
out of profile `.env` files. Retained research remains history, not a default assignment.

Hermes installs a distribution only from a whole local folder: a git URL must hold
`distribution.yaml` at its root, and there is no syntax for a subfolder or a ref. So clone
this repository and install each folder by path.

Install from a clean checkout in which nothing has run `uv sync` or tests. The installer
copies the folder whole and rejects symlinks, so a `.venv` or `__pycache__` in the source
either fails the install or ships with it. Keep a separate clone for development.

```bash
git clone https://github.com/Metta-AI/ide-distributions.git ~/ide-distributions
hermes profile install ~/ide-distributions/researcher --name researcher-1 --yes
hermes profile install ~/ide-distributions/orchestrator --name orchestrator --yes
```

Each distribution's README covers what follows the install: the profile `.env`, the one-time
bootstrap (`scripts/bootstrap.py`, which syncs the profile venv and creates or clones the
shared canonical workspace) and the host's Kanban dispatcher settings.

## Update

Changes to `orchestrator/` or `researcher/` on `main` immediately dispatch Metta's
[`Deploy Hermes agent`](https://github.com/Metta-AI/metta/actions/workflows/deploy-hermes-agent.yml)
workflow. Metta builds the latest profile trees, qualifies the candidate, and promotes it independently
of backend deployments. Running workspaces keep their current image. Concurrent pushes coalesce in
Metta's existing release queue; the trigger does not pin an older source revision.

The `Release hosted Hermes image` workflow uses the `METTA_RELEASE_DISPATCH_TOKEN` Actions
repository secret: a temporary fine-grained personal token owned by Rohit, restricted to
`Metta-AI/metta` with **Actions: read/write** and required **Metadata: read**.
It expires November 23, 2026; Rohit must rotate or replace it before then. There is no automatic
rotation. Prefer a managed GitHub App or broker-mediated dispatch for long-term operation.
Actions access covers all workflows and artifacts in Metta, not only this release workflow.
Repository secrets can be used by other eligible workflows in this repository; the main-branch
condition on this job does not constrain the credential itself.
The repository's normal `GITHUB_TOKEN` cannot dispatch across repositories.
The dispatch job confirms acceptance; follow the linked Metta workflow for build and qualification results.
Keep Metta's polling fallback until a main-branch push has successfully triggered a release.

`hermes profile update` re-reads the recorded source path, so pull the clone first. The
update keeps the installed `config.yaml`; `--force-config` resets it.

```bash
git -C ~/ide-distributions pull --ff-only
hermes profile update researcher-1
```

## Develop and test

Each distribution has its own `pyproject.toml` and `uv.lock`. CI runs one job per
distribution folder. The tests of the shared package run in `researcher/`, against its copy,
so run the sync script before testing a change under `shared/`.

```bash
cd researcher
uv sync --frozen
uv run pytest -q
```
