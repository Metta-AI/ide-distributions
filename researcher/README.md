# researcher

A research worker of the Softmax IDE. The Orchestrator's watcher starts one Session per Task in
this profile; the Session claims its Task, works in its own git worktree, proposes Tasks and
submissions through its Task, and ends by completing the Task. It speaks only to the Orchestrator.

Everything it does lives in the shared package [`core/`](../core/README.md); this folder is the
Hermes profile shell: the manifest, the config, the generated `SOUL.md`, the plugin entry and the
prepare script.

## Install

```bash
hermes profile install ~/ide-distributions/researcher --name researcher --yes
# write ~/.hermes/profiles/researcher/.env (see distribution.yaml env_requires); IDE_WORKSPACE
# must be the same path as the Orchestrator's
<hermes python> ~/.hermes/profiles/researcher/scripts/prepare.py \
  --source ~/ide-distributions/researcher --hermes $(command -v hermes)
```

Several Researcher Sessions run in this one profile. To run them in separate profiles instead,
install it again under other names (`researcher-1`, `researcher-2`) and set
`IDE_RESEARCHER_PROFILES` in the Orchestrator's `.env`.

This README is completed in the last stack entry of `docs/plans/ide-profile-refactor/`.
