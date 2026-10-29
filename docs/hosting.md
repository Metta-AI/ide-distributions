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
Existing unresolved conflicts remain untouched. Prepare also publishes current doctrine to every
existing `canonical-<game>.git` beside `RESEARCH_CANONICAL` through a temporary clone, so new cards
(branched from fetched `origin/main`) get it; retained `workspace-<game>` clones are never modified.
The host can surface
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

## Platform capabilities

Providers own operation schemas, input semantics, limits, effects, status and recovery instructions.
The distribution uses its existing `capabilities` index and native deferred-tool discovery. Do not copy provider protocols into profile prompts
or add a second discovery catalog. Local executable absence does not establish hosted availability.

The runtime may install provider plugins through supported Hermes APIs. Generic discovery,
researcher delegation, and research acceptance remain distribution responsibilities.

## Verification and rollout

Run `researcher/tests/system/test_prepare_system.py` against supported Hermes. It exercises native
installation and retained update, custom configuration, worker policy, same-version doctrine changes,
paused supervisor preservation, and repeat preparation. Development verification also covered clean, dirty, committed-conflict, and unresolved-conflict
workspaces using real Git; commands and results are retained in PR evidence.

Metta's image qualification must also run its retained-state and chat proofs with both repositories'
changes. Normal release resolution records immutable distribution inputs. Follow the instruction
ownership sequence below when adopting the platform skill contract.

## Instruction ownership

Metta publishes `softmax-host` through native skill discovery during profile installation and every
retained startup. It owns resource allocations, platform storage/lifecycle and CLI authentication.
The distribution discovers that contract; it keeps local host observations, research wrappers,
canonical policy repositories, worktrees, identity isolation, receipts and research acceptance.
Workspace operation schemas own scope, revisions, conflicts and retries. Research prompts say when
and why to use them without copying their protocol. Expert-consultation strategy is a shared research
skill; the advisory service owns available models, costs, limits and execution authority.

Deploy the platform skill publisher before adopting distributions that refer to it. Qualify fresh
and retained coordinator/researcher skill discovery, preserved configuration and policy repository
flows. This does not require changing credentials or reconnecting the user's repositories.

## Intermediate profile without IDE callbacks

The research profiles no longer expose `ide_workspace`, `ide_workspace_schema`, `ide_say`,
`ide_task_update`, or `attachment_download`. They do not poll cogent permit records.
The workspace relay must deliver native session IDs and user context without requiring IDE rows.
Do not configure an IDE MCP endpoint to restore these operations. Browser presentation and
source operations remain served by the existing workspace CLI; this removal does not delete them.

The current supervisor and native inbox own task follow-up. Native goal records are retained,
but no profile replacement for the IDE goal runner is introduced. Synchronous build/upload
completion uses native run/task results; asynchronous publication waits use the retained
workspace-local publication receipts described below.

Release verification must exercise chat, delegation, XP/credit waits and submission completion
without an IDE database or cogent callback. Validate native inbox recovery separately from the
transport fake used by the profile tests. Platform authentication, inference, evaluation jobs,
policy uploads and submissions remain external services; their refusals are still enforced.

### Publication recovery

The workspace saves exact upload inputs and the eventual policy UUID in
`policy-publications/<local-operation-id>.json` beside its canonical repository.
Retries query existing platform publication rows; they never repeat an ambiguous upload.
`research workspace publication --operation-id UUID` performs the same read-only recovery
when the original artifact or worker checkout is gone. Missing results remain unresolved.
There is no polling process, platform receipt table, or source-retention dependency.

Requires the released Coworld `policy-lookup` command and its platform lookup endpoint.
Deploy that endpoint, release Coworld, then update the profile locks before shipping this runtime.
