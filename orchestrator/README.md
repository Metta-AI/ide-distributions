# orchestrator

The research lead of the Softmax IDE: the profile the user talks to. It plans research as Goals
and Tasks, answers the Researchers, makes and submits policy Versions, and runs the watcher, a
model-free cron job that keeps the background work moving.

Everything it does lives in the shared package [`core/`](../core/README.md); this folder is the
Hermes profile shell: the manifest, the config, the generated `SOUL.md`, the plugin entry, the
watcher launcher and the prepare script.

## Install

```bash
hermes profile install ~/ide-distributions/orchestrator --name orchestrator --yes
# write ~/.hermes/profiles/orchestrator/.env (see distribution.yaml env_requires)
<hermes python> ~/.hermes/profiles/orchestrator/scripts/prepare.py \
  --source ~/ide-distributions/orchestrator --hermes $(command -v hermes) --activate-supervisor
```

`prepare.py` records where the shared code lives, builds the workspace environment under
`$IDE_WORKSPACE/tools/venv`, re-applies the config keys the machinery relies on, and enables the
`watcher` cron job once. Run it again after every `hermes profile update`.

This README is completed in the last stack entry of `docs/plans/ide-profile-refactor/`.
