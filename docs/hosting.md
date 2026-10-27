# Hosting handoff: the metta changes (R-1 to R-6)

This is the work Softmax hosting (metta) needs to run this repository's distributions: one
`orchestrator` profile and N `researcher-<i>` profiles in one sandbox. It replaces today's
single `autoresearch` profile, which is installed from `Metta-AI/ide-mvp`.

The design behind it is section 13 ("Hosting Changes for Rohit") of
[the orchestrator design](designs/orchestrator-design-2026-10-15.html). Where that design
and this file disagree, this file is current: it was checked against this repository's code
and against metta `main` at `b82f335e29` (2026-10-16). Re-check the metta file references
against current `main` before changing them.

## What changes, in one paragraph

Today a hosted runtime has one application profile (`autoresearch`), the bridge sends coach
chat to that profile, and `prepare.py` creates one bare workspace remote and starts three cron
jobs. After the change the runtime installs several profiles from two folders of this
repository: the bridge sends coach chat to `orchestrator`, every profile shares one bare
canonical git repository per game, and each profile's own `scripts/bootstrap.py` prepares it.
The orchestrator creates Kanban cards, and the gateway's dispatcher runs them as the
researcher profiles. Nothing else in the sandbox changes: one gateway (`hermes -p default
gateway run`), the API server on `127.0.0.1:8642`, multiplexed profiles.

## The items

| Item | Kind | Summary |
|---|---|---|
| R-1 | change | A runtime definition holds a list of profiles; install and credential refresh loop over all of them |
| R-2 | change | The bridge targets the orchestrator with no workspace, and the research capability stops reading "unavailable" |
| R-3 | change | Prepare runs each profile's bootstrap, creates the board, resumes the supervisor once, sets the dispatcher |
| R-4 | change | The image and lock file come from `Metta-AI/ide-distributions`, both folders |
| R-5 | verify | A card's notification subscription carries the bridge session's destination |
| R-6 | verify | The orchestrator's `ide_say` lines show in the IDE coach chat like the bridge's |

### R-1: a list of profiles

Where: `packages/observatory-core/src/observatory_core/hermes/definitions.py`
(`HostedRuntimeDefinition.profile`), `packages/observatory-hosted-worker/src/observatory_hosted_worker/runtime.py`
(`prepare_profile`, `configure_profile_credentials`, `renew_inference`, `run`) and `fleet.py`.

- `HostedRuntimeDefinition.profile: HermesProfileDefinition` becomes
  `profiles: list[HermesProfileDefinition]`, plus a field naming the profile the bridge
  targets (for example `bridge_profile: str`). Profile names and workspace paths must be
  unique.
- `prepare_profile` installs every profile (`hermes profile install <source> --name <name>`).
  The installed-identity marker (`.softmax-runtime.json`) is per profile; an existing
  single-profile install needs a migration path, because today a differing marker refuses to
  start.
- Credential refresh (`configure_profile_credentials`, `renew_inference`) writes the root
  `.env` and every profile's `.env`, not the root plus one profile.
- Sources: `/opt/ide-distributions/orchestrator` once, and
  `/opt/ide-distributions/researcher` once per researcher (`researcher-1`, `researcher-2`, ...).
  The default is two researchers.

### R-2: the bridge

Where: `fleet.py` (the bridge payload), `runtime.py` (`run`, which sets `research_profile`),
`bridge.py` (`Config`, `advertise_research`).

- `hermes_url` becomes `http://127.0.0.1:8642/p/orchestrator/`. Today `fleet.py` builds it
  from the single profile's name.
- `workspace` stays unset. A set workspace turns on the old harness relays (task forwarding
  into `comms/inbox`, the `comms/outbox` relay), which no longer exist.
- `research_profile` stays unset. The bridge's native `/research` listing and cancellation
  read the old card files and are not used with the orchestrator (the orchestrator handles
  research requests that arrive as chat).
- `advertise_research` writes `available: config.workspace is not None` on every start, which
  would now always say "unavailable". It must stop writing that, or write `available: true`
  when the bridge targets the orchestrator.
- The bridge config is persisted and not rebuilt on every start, so stored configs need a
  migration.

### R-3: prepare, per profile

Where: `devops/hermes-fleet/applications/autoresearch/prepare.py` (it becomes this
repository's application; rename the folder as you see fit).

Today's `prepare.py` writes `AUTORESEARCH_*` settings, creates `<home>/autoresearch-workspace.git`,
runs `scripts/queue_keeper.py --bootstrap-only` and resumes the `queue-keeper`, `xp-poll` and
`health` cron jobs. None of those scripts or jobs exist any more. The new steps:

1. **Write each profile's `.env`** (on top of the credentials R-1 writes). The names are the
   same for the orchestrator and every researcher:

   | Variable | Value |
   |---|---|
   | `RESEARCH_PLAYER_ID` | `SOFTMAX_PLAYER_ID` |
   | `RESEARCH_GAME` | omitted: game scope comes from the current task, not the profile |
   | `RESEARCH_CANONICAL` | `<home>/canonical.git`, the same neutral repository for every profile |
   | `RESEARCH_WORKSPACE` | this profile's neutral clone, `<profile dir>/workspace` |
   | `TMPDIR` | `<home>/tmp` (the sandbox `/tmp` is a small tmpfs) |
   | `GOTA_HERMES_PYTHON` | `/opt/hermes/.venv/bin/python` (researchers; GotA builds need it) |
   | `PATH` | the runtime's `PATH`, as today (Nim must be on it for researchers) |

   `SOFTMAX_PLAYER_CREDENTIAL`, `COGAMES_API_URL`, `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`
   come from the runtime as today. Drop every `AUTORESEARCH_*` variable, including the spend
   settings (`AUTORESEARCH_DAILY_BUDGET_USD`, `AUTORESEARCH_CHAT_DAILY_USD`,
   `AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD`, `AUTORESEARCH_AUTONOMY`,
   `AUTORESEARCH_CARD_MODELS`): the profiles have no spend management. The full list each
   profile reads is in its `distribution.yaml` (`env_requires`).

2. **Run each profile's bootstrap.** Whichever profile runs first creates the bare canonical
   repository and seeds it from its `games/<game>/seed`; both research profiles ship the
   shipped games' folders, so the order does not matter. The orchestrator also creates a
   game's workspace later with its `game_prepare` tool.
   Pass the profile's `.env` values in the environment, as today's prepare does for
   `queue_keeper.py`:

   ```sh
   HERMES_HOME=<profile dir> HERMES_REAL_HOME=<home> <.env values> \
     /opt/hermes/.venv/bin/python <profile dir>/scripts/bootstrap.py
   ```

   It is idempotent. It syncs `<profile dir>/local/venv` from the profile's `uv.lock`, creates
   or clones the canonical workspace into `RESEARCH_WORKSPACE`, writes the `research` CLI shim
   to `<profile dir>/bin/research` and adds `RESEARCH_CLI` to the profile `.env`.

3. **Create the Kanban board once:** `hermes kanban boards create autoresearch` (the slug stays
   `autoresearch`). Today `queue_keeper.py --bootstrap-only` created it.

4. **Resume the orchestrator's supervisor once.** It ships paused. Resume it on first install
   only, behind a marker as today, so a later deliberate pause survives restarts:

   ```sh
   hermes -p orchestrator cron resume supervisor
   ```

   Researchers have no cron jobs.

5. **Set the root dispatcher once** (the gateway's dispatcher reads the root profile's config,
   as today's prepare notes):

   ```sh
   hermes config set kanban.dispatch_in_gateway true
   hermes config set kanban.max_in_progress <N>
   hermes config set kanban.max_in_progress_per_profile 1
   hermes config set kanban.dispatch_profiles researcher-1,researcher-2
   ```

   Today's prepare sets `max_in_progress` to 2 on every run and never sets
   `dispatch_profiles`. Keep unsetting `kanban.default_assignee`, as today.

### R-4: image and lock

Where: `distribution.py`, `build.py`, `autoresearch.lock.json`, `Dockerfile`,
`test_distribution.py` and `README.md` in the application folder.

- The source repository becomes `Metta-AI/ide-distributions` (private). `build.py --ide-mvp`
  becomes `--repo`.
- The archive carries both folders, `orchestrator/` and `researcher/`, with their vendored
  `src/ide_research_core/` (they are committed copies; no build step).
  `distribution.py`'s member check, which today accepts only the `autoresearch` tree, accepts
  both.
- The Dockerfile installs them at `/opt/ide-distributions/orchestrator` and
  `/opt/ide-distributions/researcher` and writes one commit marker per folder. Nim stays for
  GotA builds and Pudge Wars Basic's native headless match runner.
- The lock file records the commit and tree of the new repository
  (`ide-distributions.lock.json`). Existing pinned images and selections stay valid.

### R-5: verify the card subscription

When the orchestrator creates a card from a chat turn, its subscription row must name the
bridge session's `api_server` destination, so the card's completion wakes that session. Check
it in the smoke test by reading the board's `notify_subs` table, not the create response.

### R-6: verify `ide_say`

The orchestrator posts to `/v2/cogents/<player>/say` with the player credential, as the
bridge does. Check in the smoke test that those lines render in the IDE coach chat as the same
player as the bridge's replies.

## Several games on one host

Since [multi-game research](designs/multi-game-research.md), a sandbox researches any shipped
or installed game. Profiles have no default game: the conversation selects task scope.
Hosting prepares the neutral workspace described above. What hosting should know:

- **New durable paths under `/opt/data`**, all inside the one volume: `games/<game>/`
  (installed game folders), `game-history/` (replaced ones), `sources/github.com/...`
  (fetched GitHub snapshots and their caches), `canonical-<game>.git` for each other game, and
  `profiles/<name>/workspace-<game>` clones. `.game-staging/` and `.game-install.lock` are
  reconstructible.
- **Researcher `config.yaml` gains `skills.external_dirs: ["../../games"]`**, so installed
  games' binding skills load. Hermes keeps an installed `config.yaml` on update, so an existing
  sandbox gets it only with `hermes -p researcher-<i> config set skills.external_dirs
  '["../../games"]'` (or `hermes profile update --force-config`). Without it, research still
  works but installed games' skills are not loadable as skills.
- **The bootstrap creates `/opt/data/games`** (Hermes skips an external skills directory that
  does not exist at start), so keep running each researcher's bootstrap before the gateway
  starts.
- **Doctrine.** Prepare's doctrine merge updates `profiles/<name>/workspace` only. Other games'
  clones get doctrine when the CLI bootstraps them, which `card_create` does for every card of
  that game.
- **Network.** `source fetch` needs outbound HTTPS to `github.com`. The sandbox firewall
  (`devops/hermes-fleet/firewall.sh`) rejects only private ranges, so it is allowed today.
- **Not supported here:** container-image players (no Docker in the sandbox, and `softmax
  upload` sends a file) and private GitHub repositories (no credential).

## Smoke test before advancing the lock

On a hosted sandbox with two researchers: one card for each researcher, one repair card, one
`ide_say` from a supervisor turn and one from a wake turn, a bridge reply followed by a wake in
the same session, and R-5 and R-6. This repository's model-free check of the same wiring on one
host is `orchestrator/tests/test_end_to_end.py`.

## Not hosting's work

- The ide-mvp shim (`autoresearch-shim/`) still installs the old `autoresearch` folder; moving
  it to this repository is ide-mvp work.
- Removing `autoresearch/` from ide-mvp happens after hosting runs from this repository.
