# Coach chat role in the autoresearch profile

Status: built for sections 1, 2 and 4 (plan Task 5, 2026-10-09; README "IDE coach chat";
`src/autoresearch/plugin/coach.py`, `plugin/proposals.py`). Section 3 (bridge changes) and the
shim switch in section 5 are not built. Where the build differs from this text:

- The rendering is appended to the reply by plugin code (`transform_llm_output`), not returned
  for the model to quote, and a proposal is confirmable only after that (plan 3.7).
- `propose_inbound` validates through the profile CLI (`comms inbound --dry-run`), because the
  plugin is stdlib-only and the schemas need `jsonschema`. Payload fields outside the kind's
  schema are refused, so the rendering shows everything that is sent.
- The brief is injected once per session (Hermes keeps injected context in history), not every
  turn.
- `show_card` runs `queue show` only (the shim also read briefing and handoff files).
- Hermes v0.21.6 tool search: the chat allowlist also passes `tool_search`/`tool_describe` and a
  `tool_call` whose calls are all coach tools.
- `status` does not yet report the chat's own spend.
- Verified on the pinned image: hooks fire for multiplexed `/p/<profile>/` runs, and the API
  server passes `/pause-research` to the run
  ([findings.md](../plans/2026-10-09-softmax-integration/findings.md)).

Originally proposed 2026-10-09; the rest of this document is the proposal as written.

## Why this document exists

James decided on 2026-10-09 that autoresearch **replaces** Rohit's `optimizer` profile in the
Softmax-hosted Hermes (EC2 image). In that setup the IDE coach chat talks to whatever profile the
bridge points at: every coach line becomes one Hermes run on `/p/<profile>/`
(metta `packages/observatory-hosted-worker/src/observatory_hosted_worker/fleet.py:149`,
`bridge.py:356-371`). So the IDE chat will talk to **our** profile directly. Somebody in our
profile must play the "Preston role" that the System Design and `doctrine/communication.md:7`
assume ("A Preston-role agent reads them ... It tells the human what matters").

Today the test shim's Preston plays that role from outside the profile
(`autoresearch-shim/src/ar_shim/preston.py`). This document moves that role into the profile.

This reverses one System Design decision. Rev 2 (2026-10-06) took "proto-Preston" out of the
design: "An external agent fills the Preston role" (System Design, revision history). If James
accepts this proposal, the System Design needs a short amendment: the Preston role is now a
chat role shipped inside the distribution, still separate from the research agent.

It also supersedes our earlier question B7 to Rohit
(`docs/integrations/questions-for-rohit.md:382-399`), whose default was "keep chat in the
default profile, ship a guidance file". With replacement there is no separate chat profile.

Evidence base: metta `main` at `b6bd7764ba` (pulled 2026-10-09), Hermes `v0.21.6` source
(`git show v0.21.6:<path>` in `~/coding/tools/hermes-agent-v2026.9.24`), Rohit's reply of
2026-10-09 (checked by his agent against metta `6c01816c83`), and this branch
(`jboggs/autoresearch-v0-impl`) plus the spend-controls merge `e79ca66`
(`jboggs/autoresearch-integration`).

## Recommendation in one paragraph

Run the chat role as a **second persona in the same profile**, selected by Hermes platform.
Kanban workers run as `hermes ... chat -q` with platform `cli`; the bridge's runs come through
the API server with platform `api_server`. Hermes already gives each platform its own toolset
list and its own disabled-skills list. Give `api_server` only a small plugin toolset
(`autoresearch_coach`: read tools plus `propose_inbound`), no terminal, no file tools, no code
execution. Inject the chat brief with the plugin's `pre_llm_call` hook only when the platform is
`api_server`. Keep the existing rule that **no model tool call may ever run `comms inbound`**.
Instead, the chat model only *proposes* an inbound message; the plugin's own code sends it when
the coach's next line is exactly `confirm P-xxxx` in the same session. The human's literal line
is the authentication, not anything a model says. The bridge stays deterministic transport; an
explanation of each outbox message is a best-effort extra run, never a delivery requirement.
The chat gets its own daily cost cap, separate from the research budget.

## 1. What the chat role does, and what it must not do

### Must do

1. **Explain research messages in plain words.** Outbox messages are written for an agent that
   can read the workspace (`doctrine/communication.md:7`, rule 5 "Point, do not restate"). The
   chat role resolves the `ar://` refs it needs and tells the coach what happened and whether
   they must act. The shim's proven prompt for this is `autoresearch-shim/src/ar_shim/plain.py:52-77`
   (no ids, define terms, findings first, say whether the coach must act).
2. **Answer questions from the workspace.** Messages, message status, refs, cards, records,
   promotion state. Same tool set as the shim (`preston.py:70-142`). Answers cite what the tools
   returned; "I could not find it" instead of guessing (`preston.py:42-43`).
3. **Turn coach requests into inbound messages, with confirmation.** `reply` (answer a
   question), `steer` (direction), `task` (request work). Each is proposed, shown to the coach,
   and sent only after the coach confirms (section 2.4).
4. **Report status.** Paused or running and why, running cards, open questions, today's
   spend against budget, current champion. Two of these have no read command today: pause state
   and spend status. Add one read-only `"$AUTORESEARCH_CLI" status` command (gap, section 5).

### Must not do

- **No research work.** No experiments, builds, uploads, records, KB edits. Enforced by the
  toolset: the chat has no terminal, file, or code tools (section 2.2).
- **No canonical writes** except the confirmed inbound send, which plugin code performs, not the
  model.
- **No Softmax mutations.** No submit, upload, XP request, player switch. The chat has no
  terminal, so it cannot reach `coworld` or the softmax tool's mutating commands at all.
- **No Kanban writes.** Same reason; the existing policy also blocks `kanban_create`/`kanban_link`
  in every session (`src/autoresearch/plugin/policy.py:14-22`).
- **No `seen` messages.** `seen` only marks messages read. The bridge knows deterministically
  which messages it said, so it should send `seen` (or nobody should). Keeping it out of the chat
  removes one inbound kind from the model's reach.

## 2. How it fits Hermes

### 2.1 Facts that make one profile work

| Fact | Evidence |
|---|---|
| Bridge runs arrive on the API server with platform `api_server`; toolsets come from `platform_toolsets.api_server` of the profile config | Hermes `gateway/platforms/api_server.py:2374` (`_get_platform_tools(user_config, "api_server")`), `:2392-2394` |
| The bridge's `instructions` become the run's ephemeral system prompt; they are Rohit's text, not ours | `api_server_runs.py:426`, `:735`; bridge `bridge.py:359-367` |
| API-server turns bind `HERMES_SESSION_PLATFORM=api_server` for the turn | `api_server.py:4098-4115` (`_bind_api_server_session`) |
| Kanban workers are `hermes -p <profile> --cli ... --toolsets <cli toolsets> chat -q "work kanban task <id>"`, with `HERMES_KANBAN_TASK` set | `hermes_cli/kanban_db_dispatch.py:2685-2703` (toolsets from `platform_toolsets.cli`), `:2743-2774`, `:2888` |
| `pre_llm_call` receives `platform`, `session_id` and the raw `user_message` | `agent/turn_context.py:796-809` |
| `pre_tool_call` receives `session_id` but not `platform` | `hermes_cli/plugins.py:1971-1990` |
| Plugins can register tools in their own toolset | `hermes_cli/plugins.py:456-484` (`register_tool(name, toolset, ...)`) |
| Plugin toolsets are on for **every** platform unless listed in `known_plugin_toolsets.<platform>` and absent from that platform's list | `hermes_cli/tools_config.py:526-533` |
| Skills can be disabled per platform: `skills.platform_disabled.<platform>` | `agent/skill_utils.py:260-273` |
| Gateway config load fails open to `{}` on any error, which falls back to the platform's default (full) toolset | `gateway/run.py:2866-2878`, `tools_config.py:585-587` |
| The bridge sends one fixed `session_id` per instance, and only coach lines (not visitors) | `bridge.py:368`, `bridge.py:231-233`, `:565` |

SOUL.md is profile-wide, so both personas see it. Today it says "You work on one card at a time"
(`SOUL.md:3-4`), which is wrong for the chat.

### 2.2 Mechanism: per-platform toolset, skills and brief

Recommended configuration, shipped in `config.yaml` and re-applied by bootstrap (C9):

```yaml
platform_toolsets:
  api_server: [autoresearch_coach]   # the chat: plugin tools only
  cli: [<today's effective cli list>] # Kanban workers: unchanged
known_plugin_toolsets:
  cli: [autoresearch_coach]          # so the coach toolset stays OFF for workers
skills:
  platform_disabled:
    api_server: [<every research skill>]   # ab-compare, build-upload, ... xp-request
```

- **Toolset `autoresearch_coach`** (plugin-registered): `list_messages`, `get_message`,
  `message_status`, `resolve_ref`, `list_cards`, `show_card`, `find_records`, `show_record`,
  `promotion_status`, `status` (new), `propose_inbound`. Each read tool runs one fixed
  `autoresearch` CLI read command as a subprocess with a fixed argv (no shell), exactly the
  shim's mapping (`preston.py:236-259`). Output is truncated as in the shim (12,000 characters,
  `preston.py:26`).
- **Brief.** `pre_llm_call` already exists for card briefings (`plugin/adapters.py:218-247`). Add
  a branch: when `platform == "api_server"`, return the chat brief (a file such as
  `doctrine/coach-chat.md`, about 600-900 tokens). Hermes injects it into the user message, not
  the system prompt (`turn_context.py:790-792`). Whether it is injected only for this API call
  or stays in history must be checked; if it stays in history, inject on the first turn and
  after each compression only.
- **SOUL.md** becomes role-neutral in its first lines ("You are part of the autoresearch agent.
  In a Kanban card you do research; in the IDE chat you are the coach's interface; your brief
  says which."). The rest stays.
- **Defense in depth.** Because config load fails open to the full default toolset, the plugin's
  `pre_tool_call` must also enforce the allowlist: in a session the plugin recorded as
  `api_server` (from `pre_llm_call`, keyed by `session_id`), block every tool outside
  `autoresearch_coach`. The plugin tools' handlers also refuse to run unless their session is
  recorded as `api_server`.

Alternatives considered:

| Option | Why not (or not yet) |
|---|---|
| Give the chat `terminal` and allowlist read-only CLI commands in the policy | The policy is a best-effort parser (`policy.py:47-73` "Residual risk"). Read tools with fixed argv need no parser and cannot be talked into a write. |
| Chat brief in the bridge's `instructions` | That text is Rohit's and generic (`bridge.py:359-367`); our role would depend on a bridge release. A per-profile `instructions` hook in the bridge is a reasonable later option. |
| `AGENTS.md` chosen by working directory (chat cwd vs worktree) | Works in principle (Hermes walks git root to cwd, `agent/prompt_builder.py:1722-1732`), but Rohit's runtime sets `terminal.cwd` to the policy checkout (`runtime.py:60`), and it ties persona to a path nobody owns. |
| A separate chat profile beside autoresearch | James chose replacement. It also needs a second profile installed and the bridge routed to it; Rohit's reply says chat guidance belongs in the selected profile "under today's routing". |

### 2.3 How the plugin tells the chat session from a Kanban worker

- A worker's turn has `platform="cli"` and `HERMES_KANBAN_TASK` in its process
  (`kanban_db_dispatch.py:2888`); the plugin already uses that variable (`runctx.py:22-26`).
- The chat's turn has `platform="api_server"`, delivered to `pre_llm_call` by Hermes itself, and
  runs inside the gateway process, which has no Kanban variables.
- The plugin keeps an in-memory map `session_id -> platform`, filled in `pre_llm_call`, read in
  `pre_tool_call`. Nothing on disk, so nothing a worker can write.

Open check: the plugin must load and fire for multiplexed `/p/<profile>/` runs in the
`-p default` gateway (`runtime.py:129`, `:146`; plugin scopes are per Hermes home,
`hermes_cli/plugins.py:1156-1161`). If it does not, the chat has no tools at all (it fails
closed), but the role does not work. One integration test settles it.

### 2.4 The inbound exception: confirm by the coach's own words, sent by plugin code

Today the plugin blocks `comms inbound` in every agent session (`policy.py:23-29`, `:446-451`)
and the CLI refuses it when worker variables are present (`src/autoresearch/comms/cli.py:170-184`).
**Both rules stay unchanged.** No model tool call gets an exception.

The threat that matters: research workers write outbox text, the chat reads it, so a worker's
words (or a prompt injection in a replay or opponent name copied into a record) reach the chat
model. If the chat model could send inbound messages, a worker could steer itself with the
human's authority (C11; System Design §7: inbound "carries the full authority of the human").

Flow:

1. Coach: "tell it to stop working on kill-steals for now".
2. Chat model calls `propose_inbound(kind="steer", payload={...}, summary="...")`. The plugin
   validates the payload against the kind's schema (`schemas/comms/inbound/steer.json`), stores
   the proposal **in plugin memory**, bound to this `session_id`, with a 15-minute expiry, and
   returns a fixed rendering: `Proposal P-7f3a (steer, urgency now): "<text>". Reply "confirm P-7f3a" to send.`
3. Chat replies to the coach with that rendering.
4. Coach types `confirm P-7f3a`. The bridge passes the line unchanged as the run input
   (`bridge.py:358`).
5. In `pre_llm_call` (platform `api_server`), the plugin matches `user_message` exactly against
   `^confirm P-[0-9a-f]{4,}$`, finds the pending proposal **for this session_id**, and runs
   `autoresearch comms inbound <kind> --json <payload> --author coach:<player id>` itself
   (the bridge's author form, `bridge.py:430`; the CLI validates, commits and pushes,
   `comms/inbound.py:103-126`). It then injects a context line: "Sent as I-...; tell the coach."
6. The model reports it. `cancel P-7f3a` drops the proposal the same way.

Why a Kanban worker cannot use this:

- A worker's turns are `platform="cli"` and its input is `work kanban task <id>`; the confirm
  branch never runs there.
- Proposals live only in gateway memory; there is no file to forge.
- Proposals are bound to the session they were made in. The coach's lines arrive only in the
  bridge's one chat session. A proposal made anywhere else (for example in an explanation run,
  section 3) can never be confirmed.
- The confirm token is exact-match on the coach's whole line. Outbox text never becomes run
  input (section 3 requires the bridge to pass only a seq number), so research text cannot type
  "confirm" for the coach.

Residual risks, stated plainly:

- **One UID.** Rohit confirms one sandbox UID for gateway, bridge and workers, no separate
  writer boundary, and "This reply does not approve treating model-tool restrictions as
  isolation." Any same-UID process can still write `comms/inbox/` directly, or call the local API
  server if it reads `API_SERVER_KEY` from the profile `.env`. This design does not make that
  worse and does not fix it; the fix is a separate bridge UID plus `AUTORESEARCH_INBOX_WRITERS`
  (`docs/integrations/softmax-ide-bridge-compat.md` "Trust").
- **The coach sees the proposal through the model.** The model could misdescribe the payload
  in its own words. Mitigation now: the brief requires quoting the plugin's fixed rendering
  verbatim. Real fix later: the bridge says the proposal text itself (section 3).
- **Hermes approval gate is not usable.** A plugin can ask for human approval
  (`pre_tool_call` returning `approve`, `plugins.py:1976-1980`), but the bridge stops any run
  that waits for approval (`bridge.py:393-396`). Asking Rohit to surface approvals in the IDE is
  the alternative to the typed-confirm design.

Tasks need one more decision. The IDE already has a deterministic task path: a coach task with a
research `area` becomes an inbound `task` and its acknowledgement closes the IDE task
(`bridge.py:425-470`, `:504-536`). A chat-proposed `task` would not appear in the IDE task list.
Recommended: the chat proposes `reply` and `steer`; for work requests it points the coach to the
IDE's research task button, and may propose an inbound `task` only when the coach insists.

## 3. Interaction with the bridge

Today the bridge says every outbox message verbatim as `<title>\n\n<body>`, and a `note` in reply
to a forwarded task closes that IDE task as `done`/`kept` with the note as outcome
(`bridge.py:472-536`, `TaskCompletion` at `:172-175`). Rohit's agent recommends "deterministic
transport in the bridge and explanation in the chat agent, without making an extra model call a
delivery requirement." We agree.

### 3.1 Outbox explanation

| Option | Pros | Cons |
|---|---|---|
| A. Bridge says raw text; coach asks the chat when confused | No bridge change; zero extra spend | Coach sees agent-facing text with ids (`softmax-ide-bridge-compat.md:147-162` shows real examples) |
| B. Bridge says raw text, then submits a best-effort explanation run and says the result as a follow-up line with the same `goal_id` | Delivery stays deterministic; coach gets plain text quickly | One model call per message; second line in chat |
| C. Bridge routes every message through the chat before saying it | One clean line | A model call becomes a delivery requirement; rejected by Rohit's agent and by us |

**Recommended: B, scoped.** Explain only messages that need the coach or close a task:
`question`, `help_request`, `suggestion` with `expects_response`, and task acknowledgements.
Notes and highlights stay raw (A) unless the coach asks. Run explanations in a **separate
session id** (for example `<session_id>:explain`), as our B3 default already said
(`questions-for-rohit.md:333-338`): they do not queue behind coach turns, do not bloat the coach
session, and (section 2.4) cannot produce confirmable proposals. The run input is only
`explain research message <seq>`; the chat reads the message with its own tools. Never paste
message text into run input. When the coach later answers "option 2", the chat finds the open
question with `list_messages`/`message_status`, as the shim does today.

Later, also ask the bridge to say confirm-proposals deterministically (the plugin can expose
pending proposals in a read-only file the bridge relays), which removes the model from what the
coach sees before confirming.

### 3.2 `about.outcome` to IDE task status

IDE task bodies: `state` is `queued | active | done`, `status` is
`queued | active | paused | kept | dropped | failed | cancelled`
(metta `packages/observatory-core/src/observatory_core/cogents/bodies.py:267`, `:371`). Our
runtime acknowledgements carry `about.outcome` in `done | partial | dropped | failed | blocked |
rejected` (`src/autoresearch/comms/runtime_messages.py:34-61`).

| `about.outcome` | IDE `state` / `status` | Note |
|---|---|---|
| `done` | done / kept | |
| `partial` | done / kept, outcome text leads with "Partly done" | No follow-up card is made (`runtime_messages.py:50-52`); the coach decides. Open question: is a dedicated status worth asking for? |
| `dropped` | done / dropped | |
| `failed` | done / failed | |
| `rejected` | done / failed | The task never started (bad refs, invalid file); text says why |
| `blocked` | active / paused | Not terminal. Needs confirmation that a later acknowledgement arrives after unblocking (open question) |
| absent (a model-staged note replying to the task) | no change | Say it in chat only; never close a task on a note without `about.outcome` |

Plus the rules already in `softmax-ide-bridge-compat.md:73-91`: never parse titles, never
reopen a task the coach cancelled (the bridge already skips closed tasks, `bridge.py:485-486`).
The outcome text for the IDE task should be the plain explanation when B produced one in time,
else the raw note.

## 4. Cost and spend caps

**Chat model.** Today the bridge passes the fleet's model on every run (`bridge.py:369`,
`fleet.py:152`), so the chat model is Rohit's host setting, not our `model.default`
(`config.yaml:3-5`, research model `moonshotai/kimi-k3`). The chat needs a fast, cheap,
tool-competent model; the shim used `z-ai/glm-5.3-flash` (`autoresearch-shim/src/ar_shim/config.py:9`).
Recommended: the bridge omits `model`/`provider` for our profile so the profile decides, or the
fleet sets a cheap chat tier. Our profile then needs a way to pick a chat model different from
the research model; whether Hermes offers a per-platform model setting is unverified, and if not,
the bridge's per-run model is the only lever (question for Rohit).

**Per-turn context.** Hermes system prompt plus SOUL.md, about 11 tool schemas (roughly
1.5k tokens), the brief (under 1k), the session history (one long-lived session per instance,
kept bounded by Hermes compression), and tool results capped at 12,000 characters each. Cap tool
rounds per turn as the shim does (8, `preston.py:25`). A typical turn should be a few cents on a
flash-class model; an explanation run with 1-3 ref lookups about the same.

**Spend caps.** The new caps (`e79ca66`, `src/autoresearch/runtime/spend.py`) are per card
(4 runs, about $4) and a daily budget ($25) that pauses dispatch. Two problems with chat:

1. **Accounting.** The daily budget sums every session in the profile's `state.db` whose
   `started_at` is today (`spend.py:239-243` at `e79ca66`). The chat is **one long-lived
   session** (`bridge.py:368`), and Hermes stores cost as a per-session total
   (`hermes_state_common.py:382-420`: `estimated_cost_usd`, `actual_cost_usd`). So after day one
   the chat's spend is invisible to the daily budget, and on day one it is counted whole. Per-day
   chat spend needs snapshots of the session total (or per-message usage, if Hermes records it).
2. **Coupling.** If chat spend did count, a busy coach conversation could pause research.

Recommended: exclude `source = 'api_server'` sessions from the research daily budget, and give the
chat its own soft cap (`AUTORESEARCH_CHAT_DAILY_USD`, default $3, the shim's chat budget,
`preston.py:204`). The plugin cannot stop a model call from `pre_llm_call`, so enforcement is:
over the cap, the coach tools refuse (except `status`) and the brief tells the model to say the
chat budget is spent. The hard ceiling for both is the inference key's own cap, which Rohit's
API requires explicitly (`HERMES_INFERENCE_BUDGET_USD`); set the research daily budget plus the
chat cap below it.

## 5. Migration from the shim

Moves into the profile:

| From the shim | To the profile |
|---|---|
| Read tools (`preston.py:70-130`, `:236-259`) | Plugin toolset `autoresearch_coach`, same names and argv |
| `propose_inbound` + confirm route (`preston.py:261-275`, `:389-404`) | Plugin proposal store and the typed-confirm send (section 2.4) |
| System prompt (`preston.py:29-55`) | `doctrine/coach-chat.md`, injected by `pre_llm_call` for `api_server` |
| Plain-feed prompt (`plain.py:52-77`) | Brief section "explaining a research message", used by explanation runs |
| Chat budget (`preston.py:303-307`) | `AUTORESEARCH_CHAT_DAILY_USD`, enforced by the plugin |

New in the profile: `"$AUTORESEARCH_CLI" status` (pause state and reason, today's research and
chat spend against budgets, running cards, open questions, champion), `config.yaml` rows from
section 2.2, a role-neutral SOUL.md opening, and tests: worker turn cannot see or call coach
tools; confirm works only in the proposing `api_server` session; a non-exact confirm line does
nothing; config fail-open still leaves the chat with coach tools only.

The shim keeps its web console (feed, chat pane, confirm buttons) and local deployment, but its
chat becomes a thin client of the real profile: it calls the local Hermes API server
`/p/autoresearch/v1/runs` with one session id, the same way the bridge does, and its Confirm
button sends the line `confirm P-xxxx`. Then local tests exercise the code that runs in
production. `preston.py` and the LLM half of `plain.py` are deleted after that switch; the
feed's cached plain renderings come from explanation runs.

Outside our repo (Rohit's runtime): install our pinned distribution instead of
`rohit-optimizer` (`runtime.py:38-63`), route `/p/autoresearch/` (`fleet.py:149`), set the
bridge `workspace` (`fleet.py:156`, currently `None`, so relay is off), write our `.env` keys,
and the bridge changes in section 3.

## 6. Open questions

For James:

1. Accept the System Design amendment: the Preston role ships inside the distribution as a chat
   persona, still separate from research? (Reverses the rev 2 "external agent" decision.)
2. Typed `confirm P-xxxx` acceptable as the coach's confirmation UX in the IDE, until the bridge
   can show buttons or approvals?
3. Chat proposes `reply` and `steer` only, and sends work requests to the IDE task button; or
   also `task`?
4. Chat daily cap default ($3) and whether chat spend should ever pause research (recommended:
   never).
5. `partial` maps to done/kept with "Partly done" text, or ask the IDE for a needs-attention
   status?

For Rohit:

1. Profile name: install as `autoresearch` and route `/p/autoresearch/` (our board, cron and docs
   use that name), or keep the `optimizer` name?
2. Can the bridge omit `model`/`provider` for our profile, or use a cheap chat tier? Is there a
   per-platform model setting in your Hermes image?
3. Explanation runs (section 3.1 option B) in a separate session id, input limited to the seq:
   acceptable? Which kinds?
4. `about.outcome` mapping in section 3.2, including `blocked` as active/paused and "no close
   without `about.outcome`".
5. Should the bridge send `seen` after it says a message?
6. Later: can the bridge say pending chat proposals itself (deterministic text before confirm),
   or surface Hermes approvals instead of stopping runs that wait for one (`bridge.py:393-396`)?
7. Confirm on your image digest (`sha256:9774f4f3...`): our plugin's hooks fire for multiplexed
   `/p/<profile>/` runs in the `-p default` gateway, and `platform_toolsets.api_server` is read
   from the routed profile's config.
