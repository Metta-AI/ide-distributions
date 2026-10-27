# Softmax IDE primitive inventory

This is a static, read-only inventory of the inspected local sources. It describes contracts and UI concepts, not verified production behavior.

The checkout HEADs were:

- Metta: `2a693748111a3212ba87575bde993b132d3f666a`
- Agent distributions: `6ecce6612ac85405a460f0fc0da210da0692df90`
- Mason’s exploratory client: `cad85056955e8e0be97034a3a9d0b07dfa6dedce`

No files were modified. Neither excluded report directory was opened. Prior memory was used only for orientation; the inventory below comes from the inspected sources.

## Reading the inventory

**Surfaces:** **Softmax** = Softmax IDE; **Cogent** = Cogent IDE; **Play** = beginner page; **API** = platform records, endpoints, and hosted bridge; **Profiles** = agent distributions; **Mason** = exploratory client and gateway.

**Agent requirement:** “Yes” means an agent using that capability must produce, consume, or respect the concept. It does not mean every profile currently implements it. “No” means the inspected contract assigns no direct responsibility to the hosted agent.

Citations use these exact path prefixes:

| Prefix | Absolute source location |
|---|---|
| `S/` | [/Users/jamesboggs/coding/metta/packages/softmax-ide/](/Users/jamesboggs/coding/metta/packages/softmax-ide/) |
| `C/` | [/Users/jamesboggs/coding/metta/web/softmax.com/src/ide/](/Users/jamesboggs/coding/metta/web/softmax.com/src/ide/) |
| `P/` | [/Users/jamesboggs/coding/metta/web/softmax.com/src/app/(paper)/paper/[coworld]/_custom/_onepager/](</Users/jamesboggs/coding/metta/web/softmax.com/src/app/(paper)/paper/[coworld]/_custom/_onepager/>) |
| `K/` | [/Users/jamesboggs/coding/metta/packages/observatory-core/src/observatory_core/cogents/](/Users/jamesboggs/coding/metta/packages/observatory-core/src/observatory_core/cogents/) |
| `A/` | [/Users/jamesboggs/coding/metta/packages/observatory-api/src/observatory_api/routes/app/v2/cogents/](/Users/jamesboggs/coding/metta/packages/observatory-api/src/observatory_api/routes/app/v2/cogents/) |
| `B` | [/Users/jamesboggs/coding/metta/packages/observatory-hosted-worker/src/observatory_hosted_worker/bridge.py](/Users/jamesboggs/coding/metta/packages/observatory-hosted-worker/src/observatory_hosted_worker/bridge.py) |
| `D/` | [/Users/jamesboggs/coding/ide-distributions/](/Users/jamesboggs/coding/ide-distributions/) |
| `M/` | [/Users/jamesboggs/coding/masons-ide-explorations/](/Users/jamesboggs/coding/masons-ide-explorations/) |
| `MS` | [/Users/jamesboggs/coding/masons-ide-explorations/packages/agent-protocol/src/generated/schema.ts](/Users/jamesboggs/coding/masons-ide-explorations/packages/agent-protocol/src/generated/schema.ts) |

**Scope boundaries.** Softmax explicitly retires campaigns, companion identity, shared learning, voice, browser/computer controls, recurring responsibilities, and native recording/control workflows. Historical migrations and compatibility readers do not make those active primitives. Softmax’s old ontology/wiki reader is similarly marked retired. Goals and several build/evaluation tools are feature-gated; their presence below establishes a source contract, not that a deployment enables them. Mason’s contracts are listed as exploratory, without treating them as shared platform guarantees. Sources: `S/docs/system-overview.md:24`, `S/lib/workspace/policy-wiki.ts:1`, `S/lib/tools/catalog.ts:109`.

## 1. Primitive inventory

### Identity, ownership, and scope

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Cogent | Durable player-associated identity whose records and conversations survive individual agent processes. | Cogent, Play, API | **Yes:** operate on the selected cogent’s durable state. | `A/schemas.py:65`; `C/agentDoc.ts:18` |
| Player and owner | The game-playing identity and the user who owns it; cogent creation uses an owned player. | Cogent, Play, API | **Yes:** distinguish the owner from the player and act only within ownership. | `A/schemas.py:65` |
| Coach | The human authority represented by coach credentials, preferences, answers, and chat. | Cogent, API, Profiles | **Yes:** respect coach-only writes and decisions. | `A/schemas.py:19`; `K/bodies.py:317` |
| Registered agent | Named API actor with its own token, activity timestamp, and revocation state. | API | **Yes:** preserve agent attribution and respect revocation. | `A/agents/schemas.py:10`; `A/agents/schemas.py:37` |
| Hosted profile role | Assigned agent role such as orchestrator, researcher, analyst, or verifier. | Softmax, Profiles | **Yes:** obey role-specific authority, including read-only analyst and sole-writer restrictions. | `S/lib/hermes/profiles.ts:3`; `D/ontology/ONTOLOGY.md:101` |
| Game-and-league workspace | Shared workspace containing policy drafts, documents, receipts, and research history for a selected game and league. | Softmax | **Yes:** keep work and evidence in the correct workspace scope. | `S/docs/system-overview.md:5` |
| Chat/session identity | Independent conversation with immutable identity and its own messages and controls. | Softmax, Cogent, API | **Yes:** route replies and continuations to the originating session. | `S/lib/hermes/chat-schema.ts:82`; `K/bodies.py:1118` |
| Visitor identity and conversation | Another coach’s conversation with a cogent, separated from its owner’s chat. | Cogent, API | **Yes:** use the visitor channel and its narrower authority. | `K/registry.py:220`; `K/bodies.py:1124`; `A/schemas.py:19` |
| Visitor access policy | Owner choice of `anyone`, `friends`, or `nobody`; friendship here is tied to shared live leagues. | Cogent, API | **Yes:** respect the actual access rule; absence defaults to nobody in the implementation. | `K/bodies.py:314`; `K/visits.py:19`; `K/visits.py:26` |
| Guild/team relationship | Social and collaboration context exposed through guild, friends, conversations, forums, and teams. | Cogent, API | **Yes:** honor guild participation, sharing, and spending preferences. | `C/tabs.ts:25`; `K/bodies.py:285` |
| Shared-work provenance | Attribution of imported work to a team, skill revision, and author. | Cogent, API | **Yes:** retain provenance when adopting shared work. | `K/bodies.py:237`; `K/bodies.py:262` |
| Public record | A record deliberately exposed for unauthenticated access while publication remains live. | API | **Yes:** distinguish publication from private record creation. | `A/public/schemas.py:8` |
| Canonical research repository | Per-game repository whose main branch is the accepted research record. | Profiles | **Yes:** integrate validated card work through the canonical workflow. | `D/ontology/ONTOLOGY.md:12` |
| Card worktree | Isolated research working area associated with one assigned card. | Profiles | **Yes:** perform card changes there and preserve integration provenance. | `D/ontology/ONTOLOGY.md:16` |
| Typed research identity and reference | Type-prefixed record ID and `ar://` reference resolved at a commit. | Profiles | **Yes:** produce resolvable references with version context. | `D/ontology/ONTOLOGY.md:24` |
| Agent persona | Mason’s persistent agent identity, distinct from a thread, with personality and default-agent selection. | Mason | **Yes:** use the selected agent’s identity and persistent configuration. | `MS:3802` |
| Chat versus worker thread | Conversation/work container explicitly classified as chat or worker. | Mason | **Yes:** distinguish user dialogue from delegated execution. | `MS:1241`; `MS:1413` |
| Thread project and source lineage | Policy/player project attached to a thread, with source from another thread, snapshot, or starter. | Mason | **Yes:** preserve what project and source the work derives from. | `MS:1489`; `MS:1527` |

### Agent configuration and onboarding

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Soul | Cogent identity specification containing name, temperament, voice, mistake handling, and things never done silently. | Cogent, API | **Yes:** follow its behavioral and disclosure instructions. | `K/bodies.py:276` |
| Coach familiarity | Stated context about the user’s experience or familiarity. | Cogent, API | **Yes:** use it when choosing explanations and interaction depth. | `K/bodies.py:317` |
| Reporting cadence | Preference for updates every step, at milestones, or daily. | Cogent, API | **Yes:** match reporting frequency. | `K/bodies.py:294`; `K/bodies.py:317` |
| Reasoning visibility | Preference for full reasoning presentation versus results. | Cogent, API | **Yes:** match the requested explanation format. | `K/bodies.py:295`; `K/bodies.py:320` |
| Preferred opponent | Coach-configured opponent context for practice. | Cogent, API | **Yes:** consult it when selecting relevant evaluations. | `K/bodies.py:323` |
| Guild operating preferences | Guild cadence, daily credits, accepted contacts, sharing, cross-posting, and presence settings. | Cogent, API | **Yes:** honor these separately from ordinary practice settings. | `K/bodies.py:285` |
| Onboarding progression | `create-player → meet-coach → seeding → done`, retaining soul, coach, draft preferences, and transcript. | Cogent, API | **Yes:** complete and persist the appropriate stage. | `K/bodies.py:330`; `K/bodies.py:344` |
| Composer intent | `auto`, `learn`, `do`, `view`, or `research` framing for a request. | Softmax | **Yes:** honor intent and constraints; an opening mode is not a permanent restriction on later explicit requests. | `S/lib/workspace/composer.ts:4`; `S/lib/workspace/composer.ts:27` |
| Ask/Auto permission mode | User-selected interaction mode governing tool approval behavior. | Softmax | **Yes:** use the selected approval boundary. | `S/components/workspace/composer-settings.tsx:43`; `S/lib/tools/enter_league.ts:11` |
| Model and thinking effort | Selected inference model and reasoning-effort setting. | Softmax, Mason | **Yes:** honor admitted configuration rather than silently substituting settings. | `S/components/workspace/composer-settings.tsx:43`; `MS:1325`; `MS:2706` |
| Runtime/provider selection | Thread setting identifying managed/direct runtime and provider. | Mason | **Yes:** follow the thread’s runtime contract. | `MS:1256`; `MS:1325` |
| Alignment and playstyle | Mason persona traits, including alignment and passive/neutral/aggressive style. | Mason | **Yes:** consume these as persona configuration. | `MS:3795`; `MS:3802` |
| Persistent soul themes | Private, evolving self-description associated with a Mason agent. | Mason | **Yes:** use and update it through the dedicated memory tools. | `MS:3802`; `M/packages/agent-gateway/src/tools/memory-tools.ts:54` |
| Voice mode and preset | Mason voice configuration with automatic, disabled, or custom behavior. | Mason | **Yes:** where voice output is used, honor the selected mode. | `MS:3716`; `MS:3740`; `MS:3785` |
| Character appearance and wardrobe | User-visible visual identity and cosmetic ownership. | Play, Mason | **No:** appearance alone does not create a policy-research obligation. | `P/cogent/character.ts:22`; `MS:1389`; `MS:3615` |

### Goals, work units, and coordination

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Intention | Ordered high-level statement of what the coach wants. | Cogent, API | **Yes:** connect lower-level work to stated intentions. | `K/bodies.py:355` |
| Focus goal | Ordered goal linked to one or more intentions. | Cogent, API | **Yes:** preserve goal-to-intention links. | `K/bodies.py:361` |
| Cogent task | Predicted change with completion criteria, origin, optional edit/test, steps, and outcome. | Cogent, API | **Yes:** fill `prediction` and `doneWhen`, not just a title. | `K/bodies.py:251` |
| Task queue placement | Independent `queued/active/done` placement and ordering of a task. | Cogent, API | **Yes:** update queue placement independently of task outcome. | `K/bodies.py:368` |
| Task outcome lifecycle | `queued`, `active`, `paused`, `kept`, `dropped`, `failed`, or `cancelled`. | Cogent, API | **Yes:** report the substantive outcome accurately. | `K/bodies.py:267` |
| Task verification | Explicit verification result, note, and timestamp. | Cogent, API | **Yes:** distinguish verified completion from a claimed outcome. | `K/bodies.py:245`; `K/bodies.py:265` |
| Proposed task edit | Structured change to skill settings/body, rule ordering, or files. | Cogent, API | **Yes:** identify what the task proposes to change. | `K/bodies.py:180`; `K/bodies.py:214` |
| Task step and ladder rung | Progress through implement/reflect and compile, drill, smoke, tryout, screen, battery, or submit. | Cogent, API | **Yes:** emit named step status and supporting artifacts. | `K/bodies.py:216`; `K/bodies.py:219` |
| Follow-up task | Reopened work with a new prediction, completion condition, and goal links. | Cogent, API | **Yes:** preserve the distinction between old work and its follow-up. | `K/bodies.py:380`; `K/bodies.py:429` |
| Focus review | Recorded review of focus, completed-task count, explanatory card, and changes. | Cogent, API | **Yes:** persist explicit refocusing decisions. | `K/bodies.py:616` |
| Softmax sustained goal | Objective with verification, constraints, duration, deadline, and next check. | Softmax, gated | **Yes:** respect the declared finish condition, constraints, and time boundary. | `S/lib/hermes/goal-schema.ts:14`; `S/lib/hermes/goal-schema.ts:25` |
| Native goal lifecycle | Hermes goal status, turn budget, wait time, and consecutive failure counts. | Softmax, gated | **Yes:** respect native completion and stopping conditions. | `S/lib/hermes/goal-schema.ts:3` |
| Research task | Delegated analysis, edit, or verification work with dependencies, assignee, and workspace/session context. | Softmax | **Yes:** obey role, dependency, and scope boundaries. | `S/lib/hermes/research-schema.ts:3`; `S/lib/tools/research_tasks.ts:6` |
| Research task outcome | `done`, `partial`, `dropped`, `failed`, or `blocked`, distinct from execution status. | Softmax, Profiles | **Yes:** a finished task must not imply successful or verified research. | `S/lib/hermes/research-schema.ts:3`; `D/ontology/ONTOLOGY.md:39` |
| Dependency and single-writer discipline | Coordination rule separating parallel reading from serialized shared-directory edits. | Softmax | **Yes:** wait for dependencies and avoid concurrent writers. | `S/lib/tools/research_tasks.ts:6`; `S/docs/system-overview.md:24` |
| Kanban card | Bounded assignment to a researcher; research truth lives in integrated records, not the card. | Profiles | **Yes:** keep durable findings in records and keep card scope bounded. | `D/ontology/ONTOLOGY.md:70` |
| Card kind | Investigation, scouting, experimentation, review, maintenance, planning, or repair. | Profiles | **Yes:** perform the work appropriate to the assigned kind. | `D/ontology/ONTOLOGY.md:74` |
| Card execution limits | Assignment metadata including priority, skills, runtime, failure limits, parents, and replacement lineage. | Profiles | **Yes:** honor capacity, dependency, and retry boundaries. | `D/orchestrator/src/orchestrator/board.py:82` |
| Handoff | Completion summary plus outcome, journal, changed records, merge evidence, waiting dependency, and proposed next work. | Profiles | **Yes:** emit machine-readable handoff metadata as well as prose. | `D/ontology/ONTOLOGY.md:84` |
| Card comments and steering | Typed mid-card communication: note, question, suggestion, help request, or highlight. | Profiles | **Yes:** use the correct channel; orchestrator steering carries user authority. | `D/ontology/ONTOLOGY.md:91` |
| Research plan | Orchestrator-owned priorities and open threads. | Profiles | **Yes:** researchers must not independently take over plan ownership. | `D/ontology/ONTOLOGY.md:49`; `D/ontology/ONTOLOGY.md:101` |
| Delegated worker task | Self-contained work sent to a chosen worker, with queue position and resulting worker/run identity. | Mason | **Yes:** supply sufficient task context and track the delegation. | `MS:4070`; `M/packages/agent-gateway/src/tools/collaboration-tools.ts:7` |
| Worker capacity slot | Included or purchased allocation consumed by a worker thread. | Mason | **Yes:** respect available capacity and explicit stop/archive behavior. | `MS:1360`; `MS:2670`; `M/packages/agent-gateway/src/tools/collaboration-tools.ts:60` |
| Measured optimization goal | Target attached to a neuron, with baseline/current value and `running/met/paused/stopped/impossible` state. | Mason | **Yes:** work against the chosen measurement and target direction. | `MS:4366`; `MS:4403`; `MS:4442` |
| Goal round | One optimization iteration retaining summary, version, measured value, and timestamps. | Mason | **Yes:** record progress per round rather than presenting an unstructured loop. | `MS:4405` |
| Goal activity and blocker | Current queued/working/waiting-on-user/waiting-on-results/idle condition beneath goal status. | Mason | **Yes:** report why a goal is currently unable to advance. | `MS:4426` |

### Controls, questions, approvals, and consent

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Practice Run/Pause/Stop | Controls whose UI meanings distinguish normal operation, finishing the current step, and cancellation. | Cogent, API | **Yes:** read controls before each step and obey their distinct semantics. | `K/bodies.py:661`; `C/views/ControlsView.tsx:21` |
| Hosting permission | Switch permitting hosted practice; disabled means local-only operation in Cogent. | Cogent, API | **Yes:** do not launch hosted practice when disabled. | `K/bodies.py:661`; `C/views/ControlsView.tsx:21` |
| Next-task override | User-selected next item that changes work priority. | Cogent, API | **Yes:** consume it before autonomously choosing the next task. | `K/bodies.py:661`; `C/views/ControlsView.tsx:21` |
| Chat Stop | Cancellation scoped to an individual chat’s native activity. | Softmax | **Yes:** stop that conversation’s work without conflating it with every other scope. | `S/docs/system-overview.md:5`; `S/docs/goal-completion.md:7` |
| Account Pause and drain | Account-wide fence on model calls and IDE effects, with verified drain before resumption. | Softmax | **Yes:** stop admitting effects and respect the confirmed paused state. | `S/docs/system-overview.md:24`; `S/lib/hermes/control.ts:37`; `S/lib/hermes/control.ts:147` |
| Child-task cancellation | Cancellation of selected research work and its dependent work. | Softmax | **Yes:** track confirmation; stopping a child does not establish that separate hosted operations stopped. | `S/lib/tools/research_tasks.ts:46`; `S/docs/goal-completion.md:7` |
| Structured user question | Persisted request for human input with named questions and choices. | Softmax, Play, Mason | **Yes:** ask through the structured channel and consume the actual answer. | `S/lib/hermes/input-schema.ts:3`; `P/cogent/transport.ts:59`; `MS:2093` |
| Pending-input lifecycle | An unresolved question remains pending until a human response or explicit stop/cancellation. | Softmax, Mason | **Yes:** do not treat background turns or elapsed time as answers. | `S/lib/hermes/input-schema.ts:20`; `MS:2093` |
| Coach ask-before rules | Policy naming actions, or a per-action spending threshold, that require permission. | Cogent, API | **Yes:** check rules for submit, spend, join-team, public-post, and share. | `K/bodies.py:297`; `K/bodies.py:317` |
| Single-use permit | Server-issued approval for an exact action/reference and, where relevant, amount. | API, Profiles | **Yes:** use it for the matching act; approval is consumed by one act. | `K/bodies.py:398`; `A/permits/schemas.py:29`; `A/permits/schemas.py:65` |
| Version-specific submission ask | Request to submit a specified task’s policy version. | Cogent, API | **Yes:** submit the approved version rather than the latest available version. | `K/bodies.py:387` |
| Task-management proposal | Ask to remove, reorder, or reopen work, or to create intentions and goals. | Cogent, API | **Yes:** preserve the proposal and answer instead of silently restructuring user priorities. | `K/bodies.py:411`; `K/bodies.py:419`; `K/bodies.py:435` |
| Exact native-command approval | Approval or denial addressed to one pending native run/request. | API bridge | **Yes:** match the exact pending request; it is not session-wide permission. | `B:894` |
| Tool-call approval lifecycle | Exact tool invocation moving through approval-required, ready, running, completed, or denied. | Softmax | **Yes:** execute only the approved invocation and retain its state. | `S/database/migrations/20261014120000_hermes_tool_calls.sql:4` |
| Play release consent | Informed consent in the current conversation to release one exact uploaded version. | Play, Profiles | **Yes:** do not infer release consent from building, practice, or general encouragement. | `D/play/SOUL.md:25`; `D/play/src/play/program.py:270` |
| Setup relocation confirmation | Confirmation before bringing a cogent to a different game context and abandoning existing work. | Play | **Yes:** respect the confirmed destination and outstanding-work boundary. | `P/setup.ts:140` |
| Risk tier | Mason classification of tools as read, write, or consequential. | Mason | **Yes:** route actions through the corresponding approval behavior. | `MS:1243` |
| Pre-approval | Time- and budget-bounded authorization matching tool patterns. | Mason | **Yes:** enforce pattern, expiry, and spending limits. | `MS:1309` |
| Auto-mode boundary | Automation allowance for selected work that still excludes league/champion actions from automatic approval. | Mason | **Yes:** preserve always-ask actions despite auto mode or pre-approval. | `M/packages/agent-gateway/src/agents/auto-mode.ts:6`; `M/packages/agent-gateway/src/agents/auto-mode.ts:24` |
| Approval decision record | Exact tool-call approval with cost, preview, expiry, resolver, decision, and note. | Mason | **Yes:** distinguish pending, allowed, denied, expired, and cancelled decisions. | `MS:2000`; `MS:2032` |
| Message queue versus steer | User choice to queue input or inject direction into ongoing work. | Mason | **Yes:** handle delivery according to the requested mode. | `MS:1685` |

### Game contracts, policy construction, and release

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Published game contract | Versioned description of game identity, source, runtime, player constraints, and documentation. | Softmax | **Yes:** use the actual published contract before editing or evaluating policy. | `S/lib/game-contract-model.ts:18`; `S/docs/system-overview.md:9` |
| League selection | Explicit target league associated with a cogent/workspace. | Softmax, Cogent, Play, API | **Yes:** refuse or correct operations aimed at another league. | `K/bodies.py:670`; `P/setup.ts:64` |
| League standing and division | Competitive placement, division, and leaderboard context. | Cogent, Play | **Yes:** distinguish observed standing from private practice results. | `C/types.ts:42`; `P/cogent/transport.ts:13` |
| Round and membership history | League participation and champion/change history over rounds. | Cogent | **Yes:** preserve temporal context when explaining league performance. | `C/types.ts:42`; `C/tabs.ts:25` |
| Policy format and runtime kind | Whether a policy is a file or container and how the game executes it. | Softmax, Play, Profiles | **Yes:** produce the supported artifact; Play’s implemented profile is GotA BASIC-file-only. | `S/lib/game-contract-model.ts:18`; `P/setup.ts:54`; `D/play/README.md:3` |
| Starter policy | Published starting implementation or image with source/run provenance. | Softmax, Play, Mason | **Yes:** verify and preserve the actual starter used. | `S/lib/game-contract-model.ts:18`; `D/play/README.md:24`; `MS:1511` |
| Practice variant | Published configuration variant and schema for a supported practice setup. | Softmax | **Yes:** select a valid variant and preserve it in evaluation provenance. | `S/lib/game-contract-model.ts:18`; `S/lib/tools/policy_practice.ts:10` |
| Optimizer reference | Contract-provided repository/run information for an optimizer, without implied execution authorization. | Softmax | **Yes:** distinguish a reference from permission to run it. | `S/lib/game-contract-model.ts:18` |
| Semantic policy document | Structured “How I play” representation of beliefs, skills/settings, rules, and questions. | Cogent, API | **Yes:** maintain the expected structured document, not arbitrary prose. | `C/lib/semantic.ts:9`; `C/lib/semantic.ts:75` |
| Ordered policy rule | Enabled or disabled ordered rule referring to a skill or rule text. | Cogent, API | **Yes:** preserve ordering, references, and disabled state. | `K/bodies.py:192`; `C/lib/semantic.ts:9` |
| Policy skill | Reusable policy behavior with declarations, reads/writes, persistent state, parameters, evidence, and optional drill. | Cogent, API | **Yes:** maintain both executable behavior and its declared contract. | `K/bodies.py:539` |
| Skill parameter | Tunable value with default, bounds, and explanatory help. | Cogent, API | **Yes:** use declared parameter semantics and limits. | `K/bodies.py:532` |
| Glue code | Supporting BASIC code outside individual policy skills. | Cogent, API | **Yes:** include the correct glue revision in compilation provenance. | `K/bodies.py:554`; `K/bodies.py:563` |
| Compiled policy version | Versioned BASIC output with semantic hash, skill settings/hashes, rule mapping, and source revisions. | Cogent, API | **Yes:** preserve the relationship between source, settings, and compiled bytes. | `K/bodies.py:563`; `K/bodies.py:569` |
| Current versus playing version | Latest/current policy and the version actually competing. | Cogent, API | **Yes:** do not equate editing or uploading with league deployment. | `K/bodies.py:603` |
| Immutable source revision | Complete source snapshot with parent, file manifest, hashes, and build-file provenance. | Softmax | **Yes:** save an exact revision; stale parent references are refused. | `S/lib/container-policy/store.ts:24`; `S/lib/tools/container_policy_source.ts:58` |
| Build operation | Asynchronous construction of the exact source revision, with explicit build and cleanup lifecycle. | Softmax, gated | **Yes:** track the original operation and require verified output and builder shutdown. | `S/lib/container-policy/store.ts:73`; `S/lib/tools/container_policy_build.ts:12` |
| Uploaded policy reference | Platform identity and version for a retained policy artifact. | Cogent, API, Profiles | **Yes:** distinguish local version, upload ID, and platform version reference. | `K/bodies.py:635`; `D/play/README.md:24` |
| Candidate binding | Exact connection between a research version and its uploaded platform policy version. | Profiles | **Yes:** evaluate and promote the bound candidate, not a mutable name. | `D/shared/ide_research_core/schemas/entity/experiment.json:88`; `D/ontology/ONTOLOGY.md:57` |
| Champion | Policy actually shown competing for the player in the league. | Profiles | **Yes:** use the actual champion as the experiment baseline. | `D/ontology/ONTOLOGY.md:55` |
| Champion conflict | Disagreement between the league’s champion and the research loop’s recorded champion. | Profiles | **Yes:** pause baseline-dependent research pending human reconciliation. | `D/ontology/ONTOLOGY.md:62` |
| Promotion transaction | Orchestrator-owned release progressing through intent, submitted, recorded, and published, or abortion/rejection. | Profiles | **Yes:** preserve transaction stages, exact candidate, and single-promotion ownership. | `D/ontology/ONTOLOGY.md:58` |
| Bootstrap or accepted promotion | Explicitly classified initial starter release or human acceptance of an already-current champion. | Profiles | **Yes:** do not describe these as experiment-proven improvement. | `D/ontology/ONTOLOGY.md:59` |
| League entry | Submission of an exact account-owned uploaded version to the selected league. | Softmax, Profiles | **Yes:** satisfy that surface’s consent boundary and reconcile uncertain submission results. | `S/lib/tools/enter_league.ts:11`; `D/play/SOUL.md:25` |
| Engine compatibility/update | Engine and league versions, rule/hash identity, update progress, and hosted availability. | Cogent, API | **Yes:** check compatibility and distinguish update failure from policy failure. | `K/bodies.py:678`; `K/bodies.py:693`; `K/bodies.py:698` |

### Evaluation, measurements, and evidence quality

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Evaluation run | Recorded drill, smoke, tryout, screen, battery, testcase, or scrimmage with outcome and provenance. | Cogent, API | **Yes:** record kind, execution location, candidate/control, results, and limitations. | `K/bodies.py:756` |
| Local versus hosted execution | Explicit location of evaluation, with different permissions and capabilities. | Cogent, API | **Yes:** respect hosting controls and report where evidence came from. | `K/bodies.py:756`; `K/bodies.py:661` |
| Private practice episode | Private self-play check of an uploaded policy in a supported variant. | Softmax, Play, Profiles | **Yes:** treat completion as runtime evidence, not improvement or league entry. | `S/lib/tools/policy_practice.ts:10`; `D/play/README.md:24` |
| Baseline–candidate comparison | Controlled comparison of exact uploaded versions under a frozen setup. | Softmax, Profiles | **Yes:** preserve baseline, candidate, seed, focal seat, variant, and limits of the comparison. | `S/lib/tools/container_policy_compare.ts:9`; `D/ontology/ONTOLOGY.md:33` |
| XP/experience request | Hosted evaluation request with its own durable platform identity. | Softmax, API, Profiles, Mason | **Yes:** follow the original request rather than launching a duplicate when observation is delayed. | `D/ontology/ONTOLOGY.md:64`; `S/lib/workspace/xp-panel.ts:4`; `M/apps/web/src/policies/tabs/loop.ts:31` |
| Operation receipt | Local operation identity and retained result, distinct from a possibly absent remote request ID. | Softmax | **Yes:** reconcile local and remote identities and preserve costs/status. | `S/lib/workspace/xp-panel.ts:4` |
| Episode/game result | One played game with seed, side, arm, score, outcome, terminal tick, and artifact identity. | Cogent, API | **Yes:** preserve per-game facts rather than only an aggregate score. | `K/bodies.py:715` |
| Evaluation arm | Candidate/control or me/previous/off labeling used to interpret a game or drill. | Cogent, API, Profiles | **Yes:** attribute every observation to its correct arm. | `K/bodies.py:715`; `D/ontology/ONTOLOGY.md:64` |
| Seed, side, and lineup | Reproducible arrangement of random seed, focal participant, teams, and opponents. | Cogent, API, Profiles | **Yes:** freeze or record the arrangement used. | `K/bodies.py:888`; `D/shared/ide_research_core/schemas/evaluation_design.json:33` |
| Test case | Narrow reproducible situation identified by seed, tick, side, and note. | Cogent, API | **Yes:** preserve the situation a task is intended to fix. | `K/bodies.py:228`; `K/bodies.py:259` |
| Drill scene | Controlled policy test with ticks, seeds, arena, hearts, and cast configuration. | Cogent, API | **Yes:** construct the declared scenario and identify isolated versus full-policy testing. | `K/bodies.py:487`; `K/bodies.py:497` |
| Drill measure | Named observation such as first occurrence, count, duration, final value, or path, with direction and target. | Cogent, API | **Yes:** produce the defined measurement and assess it against the target. | `K/bodies.py:507` |
| Drill highlight specification | Rule selecting events for presentation, including lead/tail, follow target, and camera. | Cogent, API | **Yes:** retain the relationship between the trigger and highlighted evidence. | `K/bodies.py:515` |
| Drill report | Structured per-arm measures, key events, firing/fault data, regressions, and verdict. | Cogent, API | **Yes:** report fault, never-fired, or missed-target outcomes explicitly. | `K/bodies.py:799`; `K/bodies.py:824` |
| Battery plan | Retained multi-request evaluation plan binding versions, opponents, seeds, slot layout, and request identities. | Cogent, API | **Yes:** execute/recover the saved plan rather than inventing a replacement batch. | `K/bodies.py:877`; `K/bodies.py:888` |
| Battery episode record | Per-request episode status, scores, fault, replay, cost, and version within a battery. | Cogent, API | **Yes:** reconcile each request and retain partial results. | `K/bodies.py:904`; `K/bodies.py:922` |
| Open-battery pointer | Durable identity of an unfinished battery that can be resumed. | Cogent, API | **Yes:** inspect it before launching overlapping evaluation. | `K/bodies.py:656` |
| Baseline book | Stored opponent baseline observations, including score, mirror condition, replay, and time. | Cogent, API | **Yes:** retain baseline provenance and context. | `K/bodies.py:645` |
| Per-seed delta | Paired outcome difference retained for individual seeds. | Cogent, API | **Yes:** preserve pairing rather than reporting only unrelated averages. | `K/bodies.py:729`; `K/bodies.py:756` |
| Dropped game | Explicitly excluded game with seed, arrangement, arm, and reason. | Cogent, API | **Yes:** disclose exclusions and avoid silently shrinking the cohort. | `K/bodies.py:734` |
| Early-stop record | Statement of how many planned seeds were completed before stopping. | Cogent, API | **Yes:** distinguish partial evaluation from the planned full sample. | `K/bodies.py:741` |
| Policy fault/error | Failure attributed to policy execution, separate from ordinary score or loss. | Cogent, API | **Yes:** retain policy errors and faults in evaluation results. | `K/bodies.py:756`; `K/bodies.py:824` |
| Self-assessment/take | Agent interpretation of a run or current condition, separate from measured results. | Cogent, API | **Yes:** label interpretation and connect it to evidence. | `K/bodies.py:608`; `K/bodies.py:756` |
| Frozen evaluation design | Predeclared metrics, guardrails, effects, sample rule, noise revision, and lineups. | Profiles | **Yes:** freeze the decision rule and judge results against it. | `D/ontology/ONTOLOGY.md:42`; `D/shared/ide_research_core/schemas/evaluation_design.json:6` |
| Primary metric and guardrail | Intended benefit and constraints that determine whether a candidate is acceptable. | Profiles | **Yes:** measure both improvement and protected behavior/performance. | `D/shared/ide_research_core/schemas/evaluation_design.json:6`; `D/shared/ide_research_core/schemas/entity/topic.json:29` |
| Metric noise record | Version-specific spread estimates, including paired-difference spread and sample counts. | Profiles | **Yes:** use the recorded noise revision when designing an experiment. | `D/ontology/ONTOLOGY.md:45`; `D/shared/ide_research_core/schemas/metric-noise.json:6` |
| One-time experiment decision | `promote`, `hold`, `refute`, or `invalid`, assigned under the frozen rule. | Profiles | **Yes:** preserve decision immutability and its evidence. | `D/ontology/ONTOLOGY.md:42` |
| Statistical change classification | Mason comparison reporting difference, uncertainty, “real/luck/unknown,” and needed samples. | Mason | **Yes:** supply suitable samples and preserve uncertainty; the label is not itself causal proof. | `M/packages/agent-protocol/src/stats.ts:14`; `M/packages/agent-protocol/src/stats.ts:65` |
| Comparison comparability | Constraints on comparing runs with different opponents, variants, or episode counts. | Mason | **Yes:** disclose mismatches instead of presenting them as controlled improvement. | `M/packages/agent-gateway/src/tools/training.ts:225` |

### Research records and durable learning

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Research topic | Objective with directed metrics, guardrails, origin, and open/active/achieved/closed state. | Profiles | **Yes:** connect experiments and claims to the intended objective. | `D/ontology/ONTOLOGY.md:31` |
| Hypothesis | Mechanism, prediction, and intervention lever with explicit testing state. | Profiles | **Yes:** state what is predicted and what would test it. | `D/ontology/ONTOLOGY.md:32` |
| Preregistered experiment | Test of one change against the champion, with lifecycle through building, evaluation, waiting, and review. | Profiles | **Yes:** preserve the design and distinguish waiting-for-credits/permit from failure. | `D/ontology/ONTOLOGY.md:33`; `D/shared/ide_research_core/schemas/entity/experiment.json:4` |
| Immutable result | Measured outcome with confidence, exact versions, game context, episode count, evidence, and validity. | Profiles | **Yes:** retain the observation separately from later interpretation. | `D/ontology/ONTOLOGY.md:34`; `D/shared/ide_research_core/schemas/entity/result.json:6` |
| Research version | Build record with provenance and upload/submission events. | Profiles | **Yes:** distinguish research identity from the platform’s uploaded version. | `D/ontology/ONTOLOGY.md:35` |
| Claim | Checkable assertion with kind, confidence, evidence, revision condition, and epistemic state. | Profiles | **Yes:** maintain hypothesis/belief/verified/stale/superseded/refuted distinctions. | `D/ontology/ONTOLOGY.md:36`; `D/shared/ide_research_core/schemas/entity/claim.json:6` |
| Evidence artifact | Immutable hash-addressed replay, result, log, artifact, snapshot, or game source. | Profiles | **Yes:** cite retained evidence rather than unsupported narrative. | `D/ontology/ONTOLOGY.md:38` |
| Evidence grade and stance | Assessment of evidence quality, directness, source grouping, and support/contradiction/resolution. | Profiles | **Yes:** preserve how evidence bears on a claim, not just its URL. | `D/shared/ide_research_core/schemas/entity/claim.json:92`; `D/shared/ide_research_core/schemas/entity/claim.json:100` |
| Revision condition and closed lever | Condition under which a rejected mechanism may be reconsidered. | Profiles | **Yes:** avoid repeatedly trying a refuted intervention without qualifying new evidence. | `D/ontology/ONTOLOGY.md:45`; `D/shared/ide_research_core/schemas/entity/claim.json:6` |
| Knowledge hub | Present-tense synthesis in which each sentence cites claims. | Profiles | **Yes:** keep synthesis traceable to explicit claims. | `D/ontology/ONTOLOGY.md:37` |
| Card journal | Record of one card run with decisions and explicit outcome. | Profiles | **Yes:** record decisions even when work is partial, dropped, or blocked. | `D/ontology/ONTOLOGY.md:39` |
| Method lesson | Research-process learning classified tentative or adopted. | Profiles | **Yes:** separate provisional advice from accepted method changes. | `D/ontology/ONTOLOGY.md:40` |
| Champion and candidate strategies | Separate strategy descriptions for deployed behavior and the proposed change. | Profiles | **Yes:** keep proposed strategy from silently replacing champion truth. | `D/ontology/ONTOLOGY.md:49` |
| Research journey checkpoint | Structured attempt update with status, hypothesis, finding, next step, and evidence. | Softmax | **Yes:** write explicit checkpoints; prose alone does not establish journey state. | `S/lib/workspace/research-journey.ts:4`; `S/lib/workspace/research-journey.ts:17`; `S/lib/workspace/research-journey.ts:75` |
| Research attempt identity | Stable identity connecting checkpoints and operations for one attempted change. | Softmax | **Yes:** preserve attempt lineage across updates and evidence. | `S/lib/workspace/research-journey.ts:17` |
| Scoped research recall | Searchable research journal scoped by student, session, and league. | Softmax | **Yes:** recall the correct scope and supply conversation identity where required. | `S/lib/research/journal.ts:5`; `S/lib/research/journal.ts:63` |
| Saved finding | Immutable finding tied to a source event and its digest. | Softmax | **Yes:** retain the event evidence behind the saved conclusion. | `S/lib/workspace/saved-finding.ts:4` |
| Packaged research method | Reference procedure for workcards, evidence, diagnosis, experiments, comparisons, lessons, or handoffs. | Softmax | **Yes:** use it as guidance, not independent execution authority. | `S/lib/research/methods.ts:12`; `S/lib/research/methods.ts:88` |
| Learned procedure | Versioned method with applicability, steps, stopping conditions, limits, evidence, and active/inactive state. | Softmax | **Yes:** activate the exact supported revision and respect its limits. | `S/lib/research/procedures.ts:6`; `S/lib/research/procedures.ts:59` |
| Discovery finding | Mason assertion with detail, edge, confidence, evidence, neuron links, and optional retraction reason. | Mason | **Yes:** provide evidence and distinguish retraction from disappearance. | `MS:4139` |
| Neuron | Mason’s named measurable building block, defined by measurement method, unit, and direction. | Mason | **Yes:** define what is measured before treating a number as progress. | `MS:4205` |
| Neuron reading | Versioned numeric observation with sample count, source, time, and optional spread. | Mason | **Yes:** attach measurement provenance and sample information. | `MS:4185` |
| Neuron link | Claimed directional relationship between two measured constructs. | Mason | **Yes:** provide explanation, confidence, evidence, and retraction state. | `MS:4700` |
| Discovery note | Evidence-free idea or steering note that can be deleted/restored and is read before research. | Mason | **Yes:** consume user steering without misclassifying it as a finding. | `MS:4630` |
| Researcher focus/queue | Discovery-facing view of a worker’s research queue, current focus, outcome, and goal. | Mason | **Yes:** keep work visibility aligned with actual execution. | `MS:4257` |

### Memory, interpretation, and history

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Daily journal | Dated collection of journal entries. | Cogent, API | **Yes:** preserve a durable chronological account when using this collection. | `K/bodies.py:956`; `K/bodies.py:961` |
| Memory evidence | Dated textual evidence attached to remembered knowledge. | Cogent, API | **Yes:** retain when and why something was learned. | `K/bodies.py:967` |
| Derived experiment memory | View assembled from task prediction, area, verdict, statistics, run, and take. | Cogent | **Yes:** populate source records correctly; this is derived rather than an independent prose invention. | `C/memory/types.ts:15`; `C/memory/model.ts:2` |
| Derived claim/belief state | Remembered claim with citations, dependencies, replacements, scope, and held/live/closed/rule classification. | Cogent | **Yes:** preserve lineage and avoid treating outdated claims as current. | `C/memory/types.ts:36`; `C/memory/types.ts:128` |
| Research question | Open or answered knowledge question with provenance, attempts, and active work. | Cogent | **Yes:** distinguish uncertainty under investigation from a question awaiting user input. | `C/memory/types.ts:53` |
| Research area | Organization of live, open, closed, and untried research. | Cogent | **Yes:** preserve area context when linking tasks and knowledge. | `C/memory/types.ts:68` |
| Strategy drift | Difference between learned conclusions and the currently expressed strategy. | Cogent | **Yes:** recognize and reconcile disagreement instead of silently assuming consistency. | `C/memory/types.ts:80` |
| Instrument trust | `trusted`, `limited`, `unverified`, or `discredited` status of a measurement instrument. | Cogent, API | **Yes:** qualify findings according to the trustworthiness of their measurement. | `K/bodies.py:973`; `C/memory/types.ts:36` |
| Knowledge tombstone | Retired belief/question retained with reason, time, and possible restoration identity. | Cogent, API | **Yes:** preserve retirement history and explicit restoration. | `K/bodies.py:987` |
| Conversation digest | Compacted chat history with covered range, source hash, producer, and edit metadata. | Cogent, API | **Yes:** use the digest’s coverage and provenance when recovering context. | `K/bodies.py:1000`; `C/agentDoc.ts:89` |
| Chat edit/forget/remember history | User corrections and memory exclusions retained alongside edit history. | Cogent, API | **Yes:** honor corrected or forgotten content when reconstructing context. | `K/bodies.py:1016`; `C/agentDoc.ts:89` |
| Agent-wide memory | Mason memory shared across an agent’s threads, with bounded content and revision hash. | Mason | **Yes:** consolidate and update through the memory contract. | `MS:3856`; `M/packages/agent-gateway/src/tools/memory-tools.ts:6` |
| Optimizer notebook | Mason’s persistent structured notebook for context, experiments, versions, decisions, instruments, preferences, and closed levers. | Mason | **Yes:** maintain the relevant notebook entries across work. | `M/packages/agent-gateway/src/agents/notebook.ts:1`; `M/packages/agent-gateway/src/agents/notebook.ts:48` |
| Notebook archive and lesson review | Explicit archival and periodic learning process instead of deleting old entries. | Mason | **Yes:** retain history and review accumulated lessons. | `M/packages/agent-gateway/src/agents/notebook.ts:19`; `M/packages/agent-gateway/src/tools/notebook-tools.ts:98` |
| Agent instruction skill | Loadable agent procedure returning instructions and loop steps. | Mason | **Yes:** load and follow the applicable agent skill. | `M/packages/agent-gateway/src/tools/skill-tools.ts:6` |

### User-visible output, presentation, and context

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Chat message | Attributed conversation entry carrying text and optional task, stage, viewer, aside, suggestions, or images. | Cogent, API | **Yes:** publish into the correct role and session. | `K/bodies.py:1124` |
| Aside | Secondary message content distinct from the main utterance. | Cogent, API | **Yes:** use the dedicated field when producing an aside. | `K/bodies.py:1133`; `A/loop/schemas.py:12` |
| Suggested replies | Agent-provided choices offered as possible user responses. | Cogent, Play, API | **Yes:** provide appropriate suggestions without treating them as answers. | `K/bodies.py:1135`; `P/cogent/transports/softmax.ts:314` |
| Draft/thinking/progress presence | Live agent status, current activity, progress, and draft output. | Cogent, Play, API | **Yes:** publish truthful transient activity rather than permanent completion claims. | `A/presence/schemas.py:84`; `P/cogent/transport.ts:69` |
| Image attachment | Bounded image artifact attached to permitted chat roles. | Cogent, API | **Yes:** consume image context and obey attachment/role constraints. | `K/bodies.py:1049`; `K/bodies.py:1136` |
| Structured event stream | Task, step, progress, scorecard, belief, credits, blocked, speech, and stage events. | Cogent, API | **Yes:** emit events with the corresponding structured payload. | `K/bodies.py:1140`; `K/bodies.py:1197` |
| Milestone announcement | Speech event explicitly marked as a milestone. | Cogent, API | **Yes:** distinguish milestone reporting from ordinary chat. | `K/bodies.py:1185` |
| Feed card | Dated titled output with body, fields, note/take, icon, and publication flag. | Cogent, API | **Yes:** produce the card’s structured content and visibility correctly. | `K/bodies.py:930` |
| Content template | Retained frontmatter/body template for creating structured content. | Cogent, API | **Yes:** use the expected template structure where applicable. | `K/bodies.py:945` |
| Stage/show request | Agent or coach request to display a policy, practice, matches, journal, strategy, replay, comparison, HTML, file, or tab. | Cogent, API | **Yes:** use structured presentation requests rather than relying on prose navigation instructions. | `K/bodies.py:89`; `K/bodies.py:950`; `A/loop/schemas.py:52` |
| Replay cue | Presentation target specifying run/file, tick or range, speed, title, and camera. | Cogent, API | **Yes:** identify the actual evidence location. | `K/bodies.py:110` |
| Replay camera/shot | Area, follow, or action camera configuration. | Cogent, API | **Yes:** provide valid framing when directing a replay. | `K/bodies.py:62`; `K/bodies.py:81`; `K/bodies.py:1033` |
| Replay comparison | Side-by-side replay presentation tied to two run/files and shared seed/tick context. | Cogent, API | **Yes:** compare the intended paired evidence. | `K/bodies.py:123` |
| Moment/key event | Named event in a replay with factual position/timing and explanatory context. | Cogent, API | **Yes:** separate event facts from commentary. | `K/bodies.py:810`; `K/bodies.py:847` |
| Replay annotation | Coach/user note attached to a replay tick/range and view. | Cogent, API | **Yes:** consume and preserve the note’s author and location. | `K/bodies.py:863` |
| Coach viewing context | Current page, anchor, heading, tab, replay position, selection, and camera. | Cogent, Play, API | **Yes:** use it to interpret references such as “this” or “here”; viewing alone need not wake the agent. | `A/presence/schemas.py:18`; `A/presence/schemas.py:49`; `P/cogent/transport.ts:82` |
| Saved view/document | Titled persistent document composed of structured text, table, chart, steps, or win-plan blocks. | Softmax | **Yes:** call the rendering tool when a view is requested; prose alone does not create it. | `S/lib/views/model.ts:25`; `S/lib/views/request.ts:5` |
| Evidence-backed win plan | Presentation of observation, proposed change, expected effect, test, success criterion, and evidence. | Softmax | **Yes:** distinguish hypotheses from demonstrated effects. | `S/lib/views/model.ts:33`; `S/lib/tools/create_view.ts:8` |
| Chart specification | Data-backed comparison, trend, scatter, distribution, or matrix presentation. | Softmax | **Yes:** provide real data, units, sample context, and uncertainty. | `S/lib/tools/select_tufte_chart.ts:6` |
| Workspace panel | Named shared canvas destination such as policy, experiments, journey, workboard, guide, replay, or research. | Softmax | **Yes:** target the appropriate panel when presenting work. | `S/lib/workspace/session-panels.ts:8` |
| Presentation pin/history/pending state | User control over current and pinned views, prior views, and incoming presentation requests. | Softmax | **Yes:** respect pinning and request-token freshness. | `S/lib/workspace/presentation.ts:26`; `S/lib/workspace/presentation.ts:37` |
| Declarative micro-app | Saved interactive composition of data, inputs, choices, checks, and constrained navigation/actions. | Softmax | **Yes:** author the allowed declarative format; do not introduce executable legacy prompt actions. | `S/lib/workspace/micro-app.ts:7`; `S/lib/workspace/micro-app.ts:44`; `S/lib/workspace/micro-app.ts:82` |
| Derived report | Report calculated from durable records rather than separately generated narrative. | Cogent | **Yes:** maintain accurate underlying records; do not treat report display as new evidence. | `C/reports/pages.ts:1`; `C/reports/pages.ts:64` |
| Play setup progression | Sign-in, cogent, joins, program, practices, and release stages derived from actual signals. | Play | **Yes:** supply real progress and artifacts; do not advance stages through reassuring prose. | `P/setup.ts:7`; `P/setup.ts:108`; `P/setup.ts:158` |
| Play handoff/blockage marker | Recognized `HANDOFF:` message that exposes an unresolved handoff to the user. | Play, Profiles | **Yes:** communicate actionable handoffs in the expected form. | `P/setupLive.ts:165` |
| Play mind/work summary | Waiting/working/resting state, current snag, ordered queue, waiting jobs, results, and worker occupancy. | Play | **Yes:** expose truthful work state, including snags and pending results. | `P/cogent/mind/types.ts:9`; `P/cogent/mind/types.ts:38`; `P/cogent/mind/types.ts:63` |
| Page facts | Facts about the visible page supplied to the agent as context. | Mason | **Yes:** use the current page’s facts when answering or acting. | `M/apps/web/src/agent-context/facts.ts:3` |
| Page customization | User/agent-requested layout built from approved components and live data bindings. | Mason | **Yes:** produce valid component specifications and preserve reset behavior. | `M/packages/agent-gateway/src/tools/page-tools.ts:24`; `M/packages/agent-gateway/src/tools/page-tools.ts:39` |
| Published custom tab | Named sandboxed HTML output with build/change/fork provenance and persistent request status. | Mason | **Yes:** publish the requested artifact and report failed builds accurately. | `M/packages/agent-gateway/src/tools/tab-tools.ts:40`; `M/apps/web/src/policies/tabs/custom-tab.ts:21` |
| Measurement representation | Saved line/bar presentation with axes, series, source, construction description, and goal/thread links. | Mason | **Yes:** preserve how the visualization was derived. | `MS:4557` |
| Replay tour | Agent-produced sequence of narrated replay moments with ticks and expression. | Mason | **Yes:** ground each moment in the selected episode/request. | `M/packages/agent-gateway/src/tools/replay-tools.ts:15` |
| Reply mood/expression | Agent-selected voice/waveform expression associated with a chat reply. | Mason | **Yes:** use the mood tool when required by that chat output contract. | `M/packages/agent-gateway/src/tools/mood-tools.ts:10` |
| Tab pinning/order/icon preference | Device-persisted arrangement of user-visible tabs. | Mason | **No:** arrangement itself is a client preference, not research state. | `M/docs/design/ui-principles.md:200` |

### Runtime state, recovery, and durable API contracts

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Revisioned record | Typed durable body with author, revision, change message, and deletion history. | API | **Yes:** write valid bodies and preserve author/revision semantics. | `K/bodies.py:48`; `A/schemas.py:94`; `A/records/schemas.py:18` |
| Optimistic revision check | Required base revision preventing updates over unseen changes. | API | **Yes:** reread and reconcile conflicts rather than overwrite. | `A/records/schemas.py:8`; `A/records/schemas.py:18` |
| Atomic record batch | Multiple record mutations accepted or rejected together. | API | **Yes:** use the batch contract when linked changes must remain consistent. | `A/records/schemas.py:84` |
| Soft deletion/history | Removal from current state while retaining historical revisions. | API | **Yes:** distinguish deletion from destruction of provenance. | `A/records/schemas.py:75` |
| Idempotent action identity | Stable key for one exact request; reuse with changed content is refused. | API, Profiles | **Yes:** reuse the original key after uncertainty and never repurpose it. | `A/schemas.py:32`; `D/orchestrator/src/orchestrator/ide.py:36` |
| Exclusive agent lease | Time-limited named-process ownership with holder and renewal. | API | **Yes:** acquire and renew ownership before exclusive work. | `A/leases/schemas.py:12` |
| Content-addressed artifact | Retained BASIC, replay, bot, or image bytes identified by hash and completion state. | API | **Yes:** verify storage completion and preserve content identity. | `A/artifacts/schemas.py:16`; `A/artifacts/schemas.py:41` |
| Retained tool output | Large tool result addressable by output ID and readable in scoped character ranges. | Softmax | **Yes:** page the retained result instead of rerunning its originating action. | `S/lib/tools/read_tool_output.ts:6` |
| Native run observation | Bridge-published native run state explicitly lacking authorization to repeat effects. | API, Cogent | **Yes:** use it as observation only. | `K/bodies.py:1105`; `B:1855` |
| Native research observation | Imported task summary, workspace/session, checkpoint, and artifact metadata. | API, Cogent | **Yes:** retain observed lineage and distinguish it from canonical research records. | `K/bodies.py:1078`; `K/bodies.py:1093`; `B:1892` |
| Chat recovery state | Working/completed/reconciling/recovered/interrupted/outcome-unknown/waiting-for-approval status tied to original identities. | API | **Yes:** reconcile uncertain work before admitting repetition. | `K/bodies.py:591` |
| Native chat run lifecycle | Queued, running, waiting for approval, stopping, completed, cancelled, failed, or interrupted execution. | Softmax | **Yes:** distinguish lifecycle completion from task outcome. | `S/lib/hermes/chat-schema.ts:28` |
| Continuation admission | Waiting/ready/admitted/blocked continuation with next-check timing. | Softmax | **Yes:** resume only the admitted continuation in the original context. | `S/lib/hermes/chat-schema.ts:105`; `S/lib/tools/research_tasks.ts:31` |
| Original-session lineage | Link from delegated/background work back to its initiating conversation. | Softmax, API bridge | **Yes:** deliver completion to the original chat and retain source-session metadata. | `S/docs/system-overview.md:24`; `B:1036` |
| Native wait lifecycle | Waiting/admitting/running/completed/cancelled/failed/outcome-unknown state for deferred work. | API bridge | **Yes:** distinguish waiting from execution and uncertainty from failure. | `B:419` |
| Outbox response envelope | Durable response with sequence, kind, title/body, reply target, metadata, and outcome. | API bridge | **Yes:** preserve reply and outcome attribution. | `B:310` |
| Task acknowledgement | Revision-checked update recording task state, status, and outcome. | API bridge | **Yes:** retry the same acknowledged effect safely after a lost response. | `B:401`; `B:1587` |
| Owner gateway request/response | Bounded immutable request plus retained response, with expiry and idempotency. | API | **Yes:** respect allowed operations, scope, and expiry. | `K/hermes_gateway.py:41`; `K/hermes_gateway.py:67` |
| Session-scoped native control | List/cancel/wait/unwait/chat controls bounded to a session/workspace. | API | **Yes:** target the correct session and original task. | `K/hermes_gateway.py:11` |
| Hosted workspace lifecycle | Instance/provisioning state with stop/reset/wake capabilities and bridge freshness. | API, Play | **Yes:** do not equate an existing instance with a currently connected agent. | `A/hermes/schemas.py:20`; `A/hermes/schemas.py:41`; `P/setup.ts:15` |
| Approved runtime specification | Admitted model, CPU, memory, and disk configuration. | API | **Yes:** stay within the approved runtime configuration. | `A/hermes/schemas.py:69` |
| Research capability presence | Availability advertisement valid only with live bridge/agent presence. | API | **Yes:** avoid claiming current capability from a stale stored record. | `K/bodies.py:583`; `B:682` |
| Loop wait/cursor | Waiting API that returns new chat and answers while retaining a continuation cursor. | API | **Yes:** preserve cursor-based consumption and answer identity. | `A/loop/schemas.py:85` |
| Tool fault | Broken CLI or binding that is a platform/tooling problem, not research evidence. | Profiles | **Yes:** report the fault and do not retry around the broken boundary. | `D/ontology/ONTOLOGY.md:97` |

### Budgets, costs, and entitlements

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Coach daily credit budget | Daily permitted platform spend governed by coach rules. | Cogent, API, Profiles | **Yes:** check remaining credits and honor over-cap refusal; the API uses a UTC day. | `K/bodies.py:317`; `A/permits/schemas.py:10`; `D/play/SOUL.md:39` |
| Guild credit budget | Separate daily allocation for guild activity. | Cogent, API | **Yes:** respect the guild-specific allowance. | `K/bodies.py:285` |
| Server spend ledger | Authoritative nonnegative credit entries associated with an action/request. | API, Cogent | **Yes:** consume server accounting; agents do not manufacture ledger entries. | `K/bodies.py:1211` |
| Cost book | Request-level costs reconciled from usage/episodes with conversion and update metadata. | Cogent, API | **Yes:** report costs from retained accounting evidence. | `K/bodies.py:623`; `K/bodies.py:629` |
| Softmax message allowance | Inference allowance reserved and settled against usage, resetting at midnight Pacific. | Softmax | **Yes:** respect admission and settlement, including reset semantics. | `S/lib/credits.ts:6`; `S/lib/credits.ts:10`; `S/lib/credits.ts:40` |
| Inference spending lease | Expiring, renewable USD allowance governing hosted inference. | API | **Yes:** renew and stay within the admitted allowance. | `A/hermes/schemas.py:62` |
| Build/evaluation accounting hold | Reserved charge associated with an operation, explicitly not an infrastructure spending cap. | Softmax | **Yes:** distinguish reserved accounting from a guaranteed maximum cost. | `S/lib/tools/container_policy_build.ts:12`; `S/lib/tools/container_policy_compare.ts:9` |
| Expert consultation | Separately charged model advice whose answer still requires verification. | Softmax | **Yes:** retain billing ownership, verify advice, and reconcile uncertain calls before retrying. | `S/lib/tools/consult_expert.ts:9`; `S/lib/tools/consultation-budget.ts:4` |
| Turn/failure/concurrency limit | Bounds on autonomous turns, failures, active tasks, or profile occupancy. | Softmax, Profiles | **Yes:** respect each independent limit. | `S/lib/hermes/profiles.ts:45`; `S/lib/hermes/goal-schema.ts:3`; `D/orchestrator/src/orchestrator/board.py:82` |
| Thread daily spending limit | Mason budget with current spending, remaining amount, and reset time. | Mason | **Yes:** stop or pause according to the budget state. | `MS:1279`; `MS:1325` |
| Usage meter | Itemized tokens/cache, sandbox, CPU/GPU, platform, or storage consumption. | Mason | **Yes:** preserve usage attribution; do not equate all meters with one charge. | `MS:1258`; `MS:2828` |
| Subscription entitlements | Allowed models, budgets, concurrency, GPU resources, time, and storage. | Mason | **Yes:** obey resource and capability limits. | `MS:2690`; `MS:2706`; `MS:2719` |
| Purchase | Checkout-backed acquisition with pending/completed/cancelled/expired/failed lifecycle and grants. | Mason | **No:** purchase lifecycle belongs to the client/service; agents must respect resulting entitlements. | `MS:2789` |

### Mason’s execution and artifact contracts

| Primitive | Definition | Surfaces/components | Hosted-agent requirement | Citations |
|---|---|---|---|---|
| Thread lifecycle | Idle, queued, running, awaiting approval, waiting on job/user, budget-paused, or archived. | Mason | **Yes:** expose the actual waiting or stopped condition. | `MS:1307`; `MS:1413` |
| Chat fallback work | Busy state when a chat thread performs work because no worker is available. | Mason | **Yes:** make execution ownership visible even without a dedicated worker. | `MS:1477` |
| Execution run | One agent execution with settings snapshot, trigger, usage, timing, status, and error. | Mason | **Yes:** preserve the settings and cause of each run. | `MS:1872`; `MS:1957` |
| Run trigger | Message, approval, job, timer, or hook that initiated execution. | Mason | **Yes:** distinguish a resumed/background trigger from a fresh user instruction. | `MS:1885` |
| Run wait | Explicit dependency on jobs or a future time. | Mason | **Yes:** retain what execution is waiting for. | `MS:1937` |
| Hook | Armed/fired/expired/cancelled subscription to an experience request, counterfactual evaluation, or external event. | Mason | **Yes:** preserve trigger source, instruction, expiry, and resulting run. | `MS:3887`; `MS:3902`; `MS:3920` |
| External event | Structured platform event with status, summary, and data that can fire a hook. | Mason | **Yes:** interpret the event without treating it as unrelated human authorization. | `MS:3980` |
| Tool-call/result message | Structured invocation and returned result within thread history. | Mason | **Yes:** preserve invocation/result pairing and execution state. | `MS:1746`; `MS:1785` |
| Message content block | Text or thinking block retained within the conversation protocol. | Mason | **Yes:** emit content in the correct block type. | `MS:1593`; `MS:1603` |
| Uploaded/workspace attachment | Message-associated file selected from upload or workspace storage. | Mason | **Yes:** preserve file identity and source. | `MS:1617` |
| Compute job | Training, evaluation, build, episodes, or custom job with queued-to-terminal lifecycle. | Mason | **Yes:** submit and monitor the correct job kind and status. | `MS:2161`; `MS:2166`; `MS:2403` |
| Job specification | Image, command, environment, resources, timeout, checkpoints, milestones, metrics, and artifacts. | Mason | **Yes:** provide a complete executable specification within entitlements. | `MS:2168`; `MS:2300` |
| Compute resource request | CPU, memory, GPU type/count, and spot preference. | Mason | **Yes:** request supported resources and account for interruption risk. | `MS:2210` |
| Job progress | Step, total, fraction, and message reported during execution. | Mason | **Yes:** report meaningful progress rather than fabricated completion estimates. | `MS:2329` |
| Compute checkpoint | Retained training/work artifact with step, metrics, size, and restore configuration. | Mason | **Yes:** distinguish an actual restorable artifact from a narrative update. | `MS:2230`; `MS:2340` |
| Job milestone | Threshold, interval, or checkpoint rule and its recorded hit. | Mason | **Yes:** configure and report the actual event that satisfied the rule. | `MS:2247`; `MS:2364` |
| Metric sample | Named numeric value at a step and timestamp. | Mason | **Yes:** emit consistent measurements suitable for plotting and evaluation. | `MS:2485` |
| Job failure classification | Structured nonzero exit, OOM, build failure, preemption, timeout, cancellation, or internal failure. | Mason | **Yes:** report the actual failure class. | `MS:2384` |
| Job log channel | Timestamped stdout, stderr, or system output. | Mason | **Yes:** preserve channel and execution attribution. | `MS:2465` |
| Job artifact | Checkpoint, output, log, or tool-output object with hash and expiring download access. | Mason | **Yes:** retain artifact identity and account for access expiration. | `MS:2504` |
| Workspace file revision | File content with hash and base-hash conflict checking. | Mason | **Yes:** avoid overwriting unseen file changes. | `MS:2560`; `MS:2583` |
| Workspace snapshot | Immutable captured file tree with pending/ready/failed state and inventory. | Mason | **Yes:** wait for a ready snapshot and preserve its provenance. | `MS:2642` |
| Policy draft response | Generated full policy source plus summary and generating model. | Mason | **Yes:** return the complete requested source and its metadata. | `MS:4765`; `MS:4783` |

## 2. Synonyms and collisions

These are the places where a shared word would be particularly dangerous in profile instructions or a cross-surface adapter.

| Names | Relationship or collision | Consequence for agent profiles | Citations |
|---|---|---|---|
| **Cogent / player / coach / agent / profile** | These identify different actors: game participant, human authority, API process, and hosted behavioral role. Mason additionally has a persistent agent identity independent of threads. | Instructions should state both the acting identity and the represented player/owner. | `A/schemas.py:19`; `A/schemas.py:65`; `MS:3802` |
| **Profile / soul** | A Cogent soul is a stored identity specification; a Hermes profile is an executable role/distribution; Mason soul themes are evolving private memory. | Do not map these to one interchangeable configuration object. | `K/bodies.py:276`; `S/lib/hermes/profiles.ts:3`; `M/packages/agent-gateway/src/tools/memory-tools.ts:54` |
| **Goal / task / intention / topic** | Cogent’s `Goal` model is what the room calls a task. Focus goals link intentions. Softmax sustained goals manage autonomous execution. Profile topics define research objectives. | Preserve separate records and completion rules. | `K/bodies.py:251`; `K/bodies.py:355`; `D/ontology/ONTOLOGY.md:31` |
| **Goal**, in Mason | A target on a measured neuron, with baseline/current values and optimization rounds. | A narrative “done” does not satisfy a metric target. | `MS:4366`; `MS:4405`; `MS:4442` |
| **Task state / task status / outcome** | Cogent queue placement is separate from substantive status. Softmax research execution status is separate from research outcome. | “Done executing” must not be translated into “kept,” “verified,” or “successful.” | `K/bodies.py:267`; `K/bodies.py:368`; `S/lib/hermes/research-schema.ts:3` |
| **Card** | A feed card is presentation; a Kanban card is an assignment. | Displaying a finding does not create or complete a research assignment. | `K/bodies.py:930`; `D/ontology/ONTOLOGY.md:70` |
| **Run** | Can mean a native inference execution, an evaluation record, or a compute job/run. | Always retain the kind and identifier namespace. | `K/bodies.py:756`; `K/bodies.py:1105`; `MS:1957` |
| **Experiment / XP / practice / training** | A preregistered experiment is a research design; an XP request is hosted execution; practice can be a single runtime check. Mason’s training tool describes private XP episodes, not necessarily weight updates. | Do not infer learning, controlled comparison, or improvement from the word “training” or “experiment.” | `D/ontology/ONTOLOGY.md:33`; `S/lib/tools/policy_practice.ts:10`; `M/packages/agent-gateway/src/tools/training.ts:2` |
| **Skill** | Cogent skill is executable policy behavior; Mason skill is agent instructions; Softmax learned procedure is a versioned research method. | Separate player-code dependencies from agent-process guidance. | `K/bodies.py:539`; `M/packages/agent-gateway/src/tools/skill-tools.ts:6`; `S/lib/research/procedures.ts:6` |
| **Checkpoint** | Softmax journey update, native research observation, and Mason restorable compute artifact are different objects. | Never promise restoration from a narrative or observational checkpoint. | `S/lib/workspace/research-journey.ts:17`; `K/bodies.py:1078`; `MS:2340` |
| **Current / uploaded / candidate / playing / champion** | These describe different points in policy construction and deployment. | Resolve the actual league champion and exact candidate binding before comparison or release. | `K/bodies.py:603`; `D/ontology/ONTOLOGY.md:55`; `D/shared/ide_research_core/schemas/entity/experiment.json:88` |
| **Baseline** | Cogent has an opponent baseline book; profile experiments require the actual champion; Softmax comparison uses an explicitly selected baseline version. | “Use the baseline” is underspecified without context. | `K/bodies.py:645`; `D/ontology/ONTOLOGY.md:55`; `S/lib/tools/container_policy_compare.ts:9` |
| **Claim / belief / finding / note** | Profile claims have evidence and epistemic states; Cogent memory derives claims; Mason findings carry evidence while notes may be evidence-free steering. | Preserve the distinction between observation, interpretation, hypothesis, and instruction. | `D/ontology/ONTOLOGY.md:36`; `C/memory/types.ts:36`; `MS:4630` |
| **Question** | May be a research uncertainty, a pending user-input request, or a permission ask. | Only the relevant human answer can resolve a pending input or approval; experiments may resolve research questions. | `C/memory/types.ts:53`; `S/lib/hermes/input-schema.ts:20`; `K/bodies.py:398` |
| **Approval / permit / Auto** | Platform permits are exact and single-use. Play requires current-conversation consent for the exact release. Mason always asks for league/champion changes. Softmax’s tool describes a conditional Auto promotion path. | **Policy mismatch:** do not export one surface’s approval semantics to another. | `K/bodies.py:398`; `D/play/SOUL.md:25`; `S/lib/tools/enter_league.ts:11` |
| **Pause / Stop / cancel** | Cogent practice Pause finishes a step; Softmax account Pause fences effects and drains; chat Stop and child cancellation have narrower scopes. | Report exactly what stopped and what remains active or uncertain. | `C/views/ControlsView.tsx:21`; `S/docs/system-overview.md:24`; `S/docs/goal-completion.md:7` |
| **Credit budget / inference allowance / accounting hold** | Different currencies, authorities, scopes, and reset rules. Coach credits use UTC days; Softmax message allowance resets Pacific; an operation hold is not a hard infrastructure cap. | Carry budget type, unit, scope, and reset clock explicitly. | `A/permits/schemas.py:10`; `S/lib/credits.ts:6`; `S/lib/tools/container_policy_build.ts:12` |
| **Workspace** | Softmax means shared game/league product state; profile research uses canonical repositories and per-card worktrees; Mason attaches source lineage and snapshots to threads. | A path or thread ID alone does not establish the same ownership or persistence boundary. | `S/docs/system-overview.md:5`; `D/ontology/ONTOLOGY.md:12`; `MS:1489` |
| **Publish** | Can expose a public API record, publish a custom UI tab, or finish recording a promotion. | These are different effects with different audiences and permissions. | `A/public/schemas.py:8`; `M/packages/agent-gateway/src/tools/tab-tools.ts:40`; `D/ontology/ONTOLOGY.md:58` |
| **Stage / view / tab / micro-app** | Related presentation mechanisms with different schemas, persistence, and executable-content limits. | Use the destination surface’s actual presentation contract. | `K/bodies.py:89`; `S/lib/views/model.ts:25`; `S/lib/workspace/micro-app.ts:7` |
| **Friends** | The visitor policy uses shared live league participation, not necessarily a conventional accepted-friend relationship. | Do not infer visitor authority from a generic social label. | `K/visits.py:19`; `K/visits.py:26` |
| **Visitor default** | The Cogent TypeScript comment says “anyone”; the backend model and access implementation say unset means nobody. | **Source disagreement:** agents should follow the enforced backend behavior. | `C/types.ts:83`; `K/bodies.py:325`; `K/visits.py:3` |
| **No-progress goal round** | Mason’s design prose describes rounds without measurement; implementation checks whether qualifying tool work occurred. | **Documentation/implementation mismatch:** a tool-active round may avoid the implemented no-work condition without producing a measurement. | `M/docs/design/ui-principles.md:304`; `M/packages/agent-gateway/src/agents/goals.ts:17`; `M/packages/agent-gateway/src/agents/goals.ts:469` |

## 3. Likely-overlooked primitives

These fifteen are especially easy to miss in a review centered on policies, experiments, and chat.

| Primitive | Why it is easy to miss—and why it matters for agents | Citations |
|---|---|---|
| **Single-use permit reference** | Looks like approval metadata, but the exact action/reference and consumed state determine whether an effect is authorized. A previous “yes” is not reusable general permission. | `K/bodies.py:398`; `A/permits/schemas.py:65` |
| **Next-task override** | A small control can supersede the agent’s chosen next action. Ignoring it makes an otherwise capable agent appear unresponsive to direct user steering. | `K/bodies.py:661`; `C/views/ControlsView.tsx:21` |
| **Task queue state versus outcome** | Two nearby status fields look redundant. They are not: work can leave the queue without being kept, successful, or verified. | `K/bodies.py:267`; `K/bodies.py:368` |
| **Chat correction, forgetting, and digest coverage** | These live in memory infrastructure rather than headline chat UI. Recovery that ignores them can revive corrected or intentionally forgotten information. | `K/bodies.py:1000`; `K/bodies.py:1016`; `C/agentDoc.ts:89` |
| **Instrument trust** | A measurement can exist while its instrument is limited or discredited. Agents need this to avoid promoting numerically persuasive but invalid evidence. | `K/bodies.py:973`; `C/memory/types.ts:36` |
| **Strategy drift** | The system can know something that its current strategy does not reflect. This is a separate work item from gathering more evidence. | `C/memory/types.ts:80` |
| **Pinned presentation and request freshness** | A correct new view can still be an incorrect UI action if it displaces something the user pinned or responds to an obsolete request. | `S/lib/workspace/presentation.ts:26`; `S/lib/workspace/presentation.ts:37` |
| **Original-session lineage** | Background completion is useful only if it reaches the conversation that requested it. Shared workspace ownership does not identify the right chat. | `S/docs/system-overview.md:24`; `B:1036` |
| **Outcome-unknown recovery** | Lost observations are not proof of failure. Repeating a build, submission, or evaluation can create duplicate effects and spending. | `K/bodies.py:591`; `K/bodies.py:1105`; `S/lib/hermes/chat-schema.ts:5` |
| **Retained tool-output handle** | Large results can be recovered by paging an existing output. An agent that misses this primitive may rerun an expensive or consequential tool just to recover omitted text. | `S/lib/tools/read_tool_output.ts:6` |
| **Candidate binding and champion conflict** | Names like “latest” and “current” conceal exact-version requirements. Agents must detect external champion changes before continuing a baseline-dependent experiment. | `D/shared/ide_research_core/schemas/entity/experiment.json:88`; `D/ontology/ONTOLOGY.md:62` |
| **Noise revision and frozen decision rule** | Often treated as statistical detail, but these are required research inputs. They determine what result can support promotion, refutation, or an invalid judgment. | `D/ontology/ONTOLOGY.md:42`; `D/shared/ide_research_core/schemas/evaluation_design.json:6` |
| **Budget clock and accounting type** | Two “daily” limits reset on different clocks, and reserved amounts are not always caps. Agents need the budget’s precise scope and semantics. | `A/permits/schemas.py:10`; `S/lib/credits.ts:6`; `S/lib/tools/container_policy_build.ts:12` |
| **Structured handoff and Play’s handoff marker** | A plain-language “I’m blocked” is not always enough for the receiving agent or UI. Profiles require typed waiting dependencies; Play extracts a recognizable handoff message. | `D/ontology/ONTOLOGY.md:84`; `P/setupLive.ts:165` |
| **Dismissal versus retraction** | Hiding a neuron/link from the current view and withdrawing its factual claim are different changes. Agents must not confuse presentation cleanup with epistemic correction. | `MS:4205`; `MS:4700` |

