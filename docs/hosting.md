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

## Retained policy source

The shared `ide_research_core.source_snapshots` module owns immutable source retention.
Use `storage = canonical_repository.parent / "policy-sources"` on the workspace volume
(`/opt/data` in hosted Kubernetes). Neither pointers nor input manifests contain checkout paths.

Publication callers use these operations:

1. Before building, call `stage_sources(storage, repository, commit=..., source_path=...,
   source_hashes=...)`. Paths are repository-relative and hashes describe exact input bytes.
   It returns `SourceSnapshot`; persist its `digest` in the workspace operation receipt.
   Tracked/new/dirty inputs become a retained Git commit. Only ignored inputs are copied separately.
2. Build inside `with build_context(storage, snapshot) as root:`. This disposable tree contains
   the captured inputs, including modes and link metadata. Do not build from the mutable checkout.
3. Persist the artifact digest and original upload operation UUID before contacting the platform.
   Upload/restart reconciliation belongs to the publication runtime, not this source module.
4. After normal upload or read-only operation-key recovery returns the platform UUID, call
   `promote_sources(storage, snapshot=snapshot.digest, version_id=UUID(...), policy_name=...,
   artifact_sha256=...)`. Identical promotion is safe to repeat; a conflicting binding is rejected.

`research workspace source-snapshot` exposes JSON `stage`, `stage_publication`, and `promote`
operations. Researchers may stage inputs; only the coordinator may promote a publication.
`policy-files` discovers, reads and edits by the same platform version UUID without a chat lookup.
Edits and operation replay receipts are saved atomically as a sparse overlay. Published input bytes
never change. For a rebuild from Code edits, call `stage_publication(storage, version_id)` and build
its returned snapshot with `build_context`; using the original snapshot intentionally rebuilds the
original publication, not its edits.

Older completed builds can still upload when their receipts cannot locate source inputs. They emit
an explicit unavailable-source diagnostic instead of guessing an external checkout or registering
false source evidence. Such artifacts need rebuilding to acquire complete retained source.

The source CLI relocation test is local persistence evidence. Image packaging, publication recovery,
and a real Kubernetes pod replacement followed by public-route editing require combined verification.
