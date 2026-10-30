# The client contract (version 1)

The profiles own one small contract with clients: the IDE, the site, coplay, and any scripted
client. It is published here as three JSON Schema files plus this note. The design calls the
part built so far **the seam** (design §10); the rest of the contract layer (panels, multi-client
sync, the `agent-context` panel) ships later and extends these files without breaking them.

| File | What it describes |
|---|---|
| `context.schema.json` | The typed context block a client sends with each message (A.3, A.4). |
| `events.schema.json` | One row of a store's `events` table as the stream delivers it (A.11). |
| `ui-state.schema.json` | The UI state document the `ui` tool writes per conversation (A.10). |

A client reads the current files from the installed profile with `research context-schema
[--name context|events|ui-state]`; it never hard-codes them.

## The three channels

1. **Context in.** A client sends `session-open` once (its identity, a session context and its
   capabilities), then `{message, context}` for each turn. The bridge validates the context,
   stamps it, and appends it to the Hermes run input as one labelled JSON block.
2. **UI output and state sync.** The `ui` tool and the watcher write UI state and notices into
   the store; every write is an `events` row. The bridge streams the rows to all attached
   clients as Server-Sent Events, so each client renders what it can and all stay in sync.
3. **UI actions and edits back.** Answers to the agent's questions reach the Session at once;
   reactions are feedback for the next turn; user edits go through `store apply`.

Locally `research serve` is the bridge (design A.11, R3). Its routes are the reference for the
hosted bridge:

| Route | Channel | Does |
|---|---|---|
| `POST /session-open` `{client: {id, kind, guide?}, session_context?, capabilities?, actor?}` | 1 | Registers a chat Session; returns `{session_id, client, coworld, contract_version}`. |
| `POST /message` `{session_id, message, context?, actor?}` | 1 | Validates and stamps the block; steers the live run or starts one; returns `{run_id, delivered: start\|steer, problems, seq}`. |
| `POST /ui-action` `{session_id, action, payload, actor?}` | 3 | `answer {ask_id, value}` patches the ask and goes into the run; `reaction {message_id, value}` writes UI state and starts no run. |
| `POST /store/apply` `{actor, op, args, store?}` | 3 | One op of the `store apply` allow-list as `user:<id>`; returns `{record, seq}`. |
| `GET /snapshot?store=workspace\|coworld` | 2 | The store's records, settings, UI state documents and `seq`. |
| `GET /stream?store=…&after=<seq>` | 2 | SSE of the store's events after `seq`. |
| `GET /runs/<run_id>` | 1 | The run's status from the Hermes runs API. |
| `GET /models` | 2 | Each profile's model: `{profiles: [{profile, role, model, provider, fallbacks}]}`. See "Profile models". |
| `GET /models/catalog` | 2 | The models a client may offer: `{source, models: [{id, name, context_length, usd_per_million_input, usd_per_million_output, free}]}`. |
| `POST /models` `{profile, model, provider?, fallbacks?, actor?}` | 3 | Changes one profile's model as the user asked; returns `{profile, previous, changed, verified, seq}`. |
| `GET /health` | — | `{ok, profile, coworld, contract_version}`. |
| `GET /` and `GET /ui` | — | A convenience of `research serve`, not a contract channel: the client page (`research_core/ui/index.html`), which exercises every channel above. |
| `GET /status` | — | A convenience of `research serve`: what `research watcher status` prints (`heartbeat`, `has_work`, `waiting_on`, `paused`). |

Errors are JSON `{ok: false, error, problems}` with an HTTP status: 400 for a malformed or
refused request, 404 for an unknown session, ask or store, 409 for an ask already answered,
503 with `Retry-After: 1` when the gateway cannot take the run now (retry), 502 for any other
gateway failure. The `actor` of a write is `user:<id>` (default `user:local`); a session actor
is refused, because Sessions write through the tools.

## The seam fields and the four stamps

Every field of the context block is optional except `contract_version`. The fields and their
limits are in `context.schema.json`; the ones the seam uses now are `client`, `coworld`,
`mode`, `command`, `view`, `references`, `on_screen` and `client_data`. The block travels in
the run input after the label line `Context v1:`; the profile's turn hook finds the last such
label, validates the block again and renders it for the model.

The bridge stamps four items into each turn (design A.4). `research serve` stamps the first
two into the block; the profile's own turn hook (`plugin/turn.py`) adds the last two, so the
bridge never writes them:

| Stamp | Who | Where |
|---|---|---|
| `client` — the client that opened the Session (`id`, `kind`, `guide`) | bridge | the block; it overrides any `client` the message carried |
| `coworld` — the active Coworld | bridge | the block; a client showing another Coworld is told so in `problems` |
| the changes since this conversation's last turn | turn hook | `[changed since your last turn]` |
| the unread notices | turn hook | `[notices since your last turn]` |

`contract_version` is stamped too, so a block always carries it.

## Limits and validation (A.7)

The whole block is at most 16 KB as JSON and `client_data` at most 8 KB; `mode.name` and
`command.name` at most 40 characters, their `instruction` at most 1,000; `on_screen` at most 20
items. Unknown fields are dropped and named in `problems`; an oversize or malformed field is
refused with a message naming the limit, never truncated; the rest of the block is kept. A bad
block never fails a turn: `/message` answers 200 with `problems`, and the run starts without
the refused parts. Only the user's explicit request outranks a `mode`.

## The client guide skill

`client.guide` names the skill the agent loads for that client (for example `client-coplay`).
It replaces free instruction text, which the bridge drops. Without `ui.ask` in the client's
capabilities the agent asks in chat.

## The stream (A.11)

A client loads `/snapshot` for a store, then opens `/stream?store=…&after=<the snapshot's seq>`;
nothing falls between the two. Each store (the workspace store and each Coworld store) has its
own `seq`, so a client keeps one cursor per store and one connection per store. Frames are
`id: <seq>` and `data: <event JSON>` (the event per `events.schema.json`, plus `store`); a
`: keepalive` comment goes out after ten idle seconds; new rows appear within half a second.
A reconnecting client sends `Last-Event-ID` (or `after`) with the last `seq` it saw. Clients
ignore event kinds and content kinds they do not know.

## Runs per Session

The Hermes gateway does not serialise runs per session, so the bridge does (amendment A6.1):
a message, or a UI answer, for a Session whose run is `running` or `queued` is steered into that
run; otherwise it starts a new run with the same session id, which resumes the conversation's
history. `delivered` in the reply says which happened. A queued run cannot take a steer yet;
the bridge answers 503 with `Retry-After: 1` and the client retries. A reaction never starts a
run: it is a UI state change under `/reactions/<message id>` of the conversation, which the
agent sees at its next turn.

## Profile models

Each profile's model belongs to the profile, not to the host: a new install starts with what the
profile's shipped `config.yaml` names (the Orchestrator: `moonshotai/kimi-k3`, falling back to
`z-ai/glm-5.3`; a Researcher: `recursive/flint-v1.0:free`, falling back to
`moonshotai/kimi-k3`, all through OpenRouter), `prepare.py` never changes it, and only the user
changes it, through the bridge.

- `GET /models` lists every profile of the sandbox (the Orchestrator, then each Researcher
  profile) with its `role` (`orchestrator` or `researcher`), `model`, `provider` and
  `fallbacks` (`[{provider, model}]`, tried in order when the primary fails).
- `GET /models/catalog` is the list a model picker offers: OpenRouter's models that take tool
  calls (the profiles need them), with context length and prices in US dollars per million
  tokens. It is cached for ten minutes; 503 with `Retry-After: 30` when it cannot be read.
- `POST /models` `{profile, model, provider?, fallbacks?, actor?}` changes one profile.
  `model` is an id such as `moonshotai/kimi-k3`; `provider` defaults to the profile's current
  one; `fallbacks`, when present, replaces the list (each entry a model id or `{model,
  provider?}`, at most four; an absent `fallbacks` keeps the list). Only a `user:<id>` actor
  may change a model. An OpenRouter model not on the catalog is refused (400, named in
  `problems`); when the catalog cannot be read the change is made and `verified` is false.
  `changed` lists the config keys written (empty, and no event, when nothing differed); an
  unknown profile is 404.
- A change is a `profile.model_updated` event in the workspace store (subject: the profile;
  payload: the new `{profile, role, model, provider, fallbacks}` with `previous` and
  `changed`), so every attached client sees it on its stream.
- A change applies to the next run of every conversation of that profile: Hermes reads the
  profile's config when a run starts. A run already going finishes on the model it started
  with. The bridge must not name a `model` in its run requests, or the request would override
  the profile's choice.

## Versioning

`contract_version` is an integer in every schema file and in every block. It increments on any
breaking change: a removed or renamed field, a tightened limit, a changed meaning. Adding an
optional field or a new event kind is not breaking: clients ignore what they do not know, and
the backend drops what it does not know. A client sends the version it was built for; the
backend accepts the current one and refuses others in `problems`.

## Deferred to the contract layer

Not part of the seam, and not built here: `session-update`, the panel kinds' rendering rules,
multi-client replay of UI state, the `agent-context` panel's computed parts, relaying live chat
tokens, messages reads from the Hermes transcript, and client-supplied guides by reference.
`ui-state.schema.json` describes `show`, `panels` and `asks`; the `reactions` map the seam
writes is an addition for the next revision of that file.
