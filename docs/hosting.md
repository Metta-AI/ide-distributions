# Hosting research distributions

Metta hosts these distributions. The distribution owns research roles, tools, skills, acceptance,
profile policy, supervisor behavior, and workspace doctrine. Metta owns credentials, runtime
resources, profile installation, storage, dispatcher capacity, and provider capabilities.

## Installation and retained updates

Install researcher profiles before the orchestrator. Each standalone folder contains its shared
code, skills, and generated SOUL knowledge; `tools/sync_shared.py` checks these packaged copies.
The profile source must be a pristine distribution payload, separate from retained profile state.

After installation or native `hermes profile update`, invoke the distribution preparation entrypoint:

```sh
HERMES_HOME=<profile-directory> HERMES_REAL_HOME=<host-home> \
  RESEARCH_WORKSPACE=<profile-workspace> RESEARCH_CANONICAL=<shared-bare-repository> \
  RESEARCH_EPISODE_LIMIT=200 \
  python <profile-directory>/scripts/prepare.py \
  --source <pristine-distribution-directory> --hermes <hermes-executable> \
  --activate-supervisor
```

The ordinary player/API credentials and environment remain supplied by the host. Prepare uses
the profile's locked environment. Hosted images may prebuild that environment and its lock stamp.
No credentials are embedded in distribution sources.

Prepare creates a neutral workspace when absent and reconciles doctrine by full content, including
same-version changes. It publishes through the distribution's workspace operations and merges
canonical history. Conflicting committed or dirty edits defer reconciliation without discarding work.
Existing unresolved conflicts remain untouched. The host can surface
`local/doctrine-migration-warning.txt`; successful reconciliation clears it and its reported marker.

Prepare adds required worker denials and environment passthrough from shipped config while retaining
custom settings. It also adds shipped `skills.external_dirs`, sets the fallback model when none is
set, and fills `agent.max_turns`, `terminal.timeout` and `timeouts.tools` when a retained value is
unset or a known earlier default (`EARLIER_DEFAULTS` in `ide_research_core/prepare.py`); a value
the owner chose is kept. Coordinator tool defaults and the script-only supervisor execution contract also
come from shipped files. Retained schedule, pause state, history, and unrelated jobs remain intact.
The optional `--activate-supervisor` enables supervision once, guarded by `.softmax-prepared`;
it never resumes a deliberately paused retained supervisor. Omit it for an installation that should
remain paused. Preparation creates no research tasks or model calls.

Metta configures shared gateway dispatch capacity and worker profile names after preparation.
The distribution disables automatic task decomposition because its coordinator owns assignment.
Game selection comes from the conversation and task; never supply a default `RESEARCH_GAME`.

## Writable storage

Profile code from the image is read-only. Game tools build into
`$HERMES_HOME/local/game-builds/<game>/` on the workspace disk when their own `tools/.cache` and
`tools/bin` are not writable, so a shipped game needs no image volume. The researcher GotA volumes
still work and can be removed.

## Platform capabilities

Providers own operation schemas, input semantics, limits, effects, status and recovery instructions.
The distribution uses its existing `capabilities` index, native deferred-tool discovery, and
`ide_workspace_schema`/`ide_workspace` client. Do not copy provider protocols into profile prompts
or add a second discovery catalog. Local executable absence does not establish hosted availability.

The runtime may install provider plugins through supported Hermes APIs. Generic discovery,
researcher delegation, and research acceptance remain distribution responsibilities.

## Verification and rollout

Run `researcher/tests/system/test_prepare_system.py` against supported Hermes. It exercises native
installation and retained update, custom configuration, worker policy, same-version doctrine changes,
paused supervisor preservation, and repeat preparation. Development verification also covered clean, dirty, committed-conflict, and unresolved-conflict
workspaces using real Git; commands and results are retained in PR evidence.

Metta's image qualification must also run its retained-state and chat proofs with both repositories'
changes. Land the distribution entrypoint before the Metta caller and pin a distribution commit that
contains it. Older images keep their existing preparation code; no runtime fallback is needed.
