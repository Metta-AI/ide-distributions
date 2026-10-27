# researcher

A research worker for the Softmax IDE: it improves game policies by working Kanban cards that
the `orchestrator` profile gives it, each card on one game. It ships three games, Gods of the
Arena (`gota`), Pudge Wars Basic (`pudge-wars-basic`, the game-hosted BASIC league) and the test
game `fakegame`; a card can also make the folder for a new game
(the `game-create` skill), which the orchestrator installs on the host. It never talks to the
user; the orchestrator does. Several researchers work on one host, each as its own profile
(`researcher-1`, `researcher-2`, ...) installed from this folder.

`researcher/` (formerly `autoresearch/`) is the root of a Hermes Agent profile distribution.
This file is the operations guide: install, bootstrap, configure, run, update, restore.

The design is
[docs/designs/orchestrator-design-2026-10-15.html](../docs/designs/orchestrator-design-2026-10-15.html)
at the repository root (open it in a browser). The research method itself (ontology,
doctrine, procedures) is still described by the
[System Design](docs/designs/autoresearch-system-design-2026-10-06.html); the other documents
under `docs/` describe the earlier single-profile harness and are kept as history.

## How a researcher works

1. The orchestrator creates a card on the Kanban board `autoresearch`, assigned to this
   profile, with a git worktree of this profile's clone of the card's game on the branch
   `card/<id>`. Every research command run in that worktree works on that game.
2. The gateway's dispatcher runs the card as `hermes -p <profile>`. The worker reads the card
   (`kanban_show`) and does the work with the tools and skills below.
3. It preserves its output with `workspace stage` (the research CLI), which commits the
   worktree and pushes the card branch. The orchestrator verifies the work and integrates it
   into canonical `main` (`workspace integrate`, under a lock that every profile on the host
   shares).
4. It ends the card with `kanban_complete(summary, metadata)` (see
   `skills/autoresearch/card/SKILL.md`), or `kanban_block` when it needs an answer it cannot
   work without. Mid-card it comments on its own card (`note:`, `question:`, ...); the
   orchestrator's comments arrive as user-authority steers.

What a researcher may and may not do:

| | Researcher | Enforced by |
|---|---|---|
| Upload builds, launch and read XP, write records, KB and stats in its worktree | yes | |
| Submit to the league, bootstrap or accept a champion (`promotion ...`) | no | `approvals.deny` in `config.yaml` |
| Integrate into `main` (`workspace integrate`) or install a game folder (`game install`) | no | `approvals.deny`, and the commands refuse in a researcher profile |
| Create, link or schedule cards | no | the `researcher_guard` plugin |
| Run `git push` (any branch) | no, `workspace stage` pushes the card branch | the `researcher_guard` plugin |
| Change `strategy/STRATEGY.md`, `control/`, `promotions/` | no | `workspace integrate` refuses |
| Force pushes, `coworld` platform mutations, `softmax login`, `player use` | no | `approvals.deny` |

The `researcher_tools` plugin adds `capabilities` (every tool and skill in the session) and
`host_capabilities` (installed executables, writable places, missing game host requirements;
it runs nothing).

There is no spend management in this profile on purpose: the hosting allowance and Hermes's
own limits (`agent.max_turns`, `agent.run_budget_seconds`, card runtime and retry limits) are
the bounds.

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| `git` | any recent | the canonical workspace and its clones |
| `uv` | any recent | the profile venv (the launcher runs `uv sync --frozen`) |
| Hermes Agent | v0.21.6 | the dispatcher, the workers and Kanban |
| Nim | 2.2.10 | building GotA and Pudge Wars Basic native tools and running native gate/replay tests; pure Python tests need no Nim |

```sh
git --version
uv --version
hermes --version
nim --version
```

## Install

Install from a clean checkout (nothing has run `uv sync` or tests in it): the installer
copies the folder whole and rejects symlinks.

```sh
export PROFILE_NAME=researcher-1
hermes profile install /path/to/ide-distributions/researcher --name "$PROFILE_NAME" --yes
export PROFILE="${HERMES_HOME:-$HOME/.hermes}/profiles/$PROFILE_NAME"
```

Repeat with `researcher-2`, ... for more researchers. Every researcher on a host uses the same
board, `autoresearch`, and the same bare canonical repository of each game.

## Configure `.env`

Write `$PROFILE/.env` (Softmax hosting writes it for you):

| Variable | Meaning |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter key for the research model |
| `SOFTMAX_PLAYER_CREDENTIAL` | the player's credential (a `cga_` agent token, or a player session as JSON) |
| `RESEARCH_PLAYER_ID` | that player's id |
| `RESEARCH_GAME` | selected task scope only; do not set it in profile configuration. Bootstrap without a game creates a neutral workspace. |
| `RESEARCH_CANONICAL` | the neutral bare canonical repository, shared by every profile on the host, for example `<host root>/canonical.git`; other games' repositories sit beside it as `canonical-<game>.git` |
| `RESEARCH_WORKSPACE` | this profile's neutral clone (default `$PROFILE/workspace`); other games' clones are `$PROFILE/workspace-<game>` |
| `COGAMES_API_URL` | the Softmax API server (default `https://softmax.com/api`) |

`distribution.yaml` lists the rest. `RESEARCH_SOFTMAX=fake` selects the fake platform in tests.

## Bootstrap

Run once after the install, and again after an update (it is idempotent). It syncs the
profile venv, creates and seeds the bare canonical repository when it is the first profile on
the host (from `games/<game>/seed`), clones it into `RESEARCH_WORKSPACE`, writes `AGENTS.md`
from `doctrine/`, and writes the CLI shim `$PROFILE/bin/research` and `RESEARCH_CLI` into the
profile `.env`:

```sh
( set -a; . "$PROFILE/.env"; set +a; HERMES_HOME="$PROFILE" python3 "$PROFILE/scripts/bootstrap.py" )
```

## Host configuration

The board is created once per host:

```sh
hermes kanban boards create autoresearch
```

The dispatcher runs in the gateway and reads `kanban.*` from the host's default profile.
Allow only the researchers to claim cards, one card each at a time:

```sh
hermes config set kanban.dispatch_profiles '["researcher-1", "researcher-2"]'
hermes config set kanban.max_in_progress 2
hermes config set kanban.max_in_progress_per_profile 1
hermes config set kanban.dispatch_in_gateway true
```

The `orchestrator` profile creates the cards; without it, a researcher only runs cards
created by hand with the Kanban CLI. Softmax hosting does all of this in its prepare step; the
metta changes it needs are in [docs/hosting.md](../docs/hosting.md).

## Operate

The research CLI needs the profile environment. In an operator shell:

```sh
set -a; . "$PROFILE/.env"; set +a; export HERMES_HOME="$PROFILE"
```

Then, for example:

```sh
"$RESEARCH_CLI" records summary
"$RESEARCH_CLI" records find experiment status=evaluating
"$RESEARCH_CLI" records resolve ar://plan
"$RESEARCH_CLI" softmax whoami
"$RESEARCH_CLI" softmax leaderboard
"$RESEARCH_CLI" softmax exec -- coworld leagues --json   # any real CLI command, as the Player
"$RESEARCH_CLI" kb lint
"$RESEARCH_CLI" workspace merged --branch card/t_1234abcd
```

| Group | Commands | Notes |
|---|---|---|
| `records` | `new`, `show`, `update`, `status`, `link`, `find`, `validate`, `summary`, `resolve` | the ontology's records; `resolve` reads an `ar://` ref at a commit |
| `stats` | `design`, `noise`, `ingest`, `evaluate` | pre-registered evaluation and the decision rule |
| `kb` | `lint`, `apply`, `index`, `stale`, `renew`, `compact`, `hubs` | maintenance cards run `apply`/`stale`/`renew`/`compact` in their worktree |
| `game` | `show`, `list`, `new`, `check`, `install` | game folders; `install` is the orchestrator's |
| `source` | `fetch` | read-only snapshots of public GitHub repositories |
| `softmax` | `whoami`, `credits`, `xp create/status/recover/evidence/artifacts`, `upload`, `upload-build`, `leaderboard`, `game-version`, `standings`, `league-episodes`, `league-results`, and the discovery reads (`discover-leagues`, `league-players`, `game-support`, `player-champion`, `player-result`) | every platform action of a researcher; `exec -- <coworld\|softmax> <args>` runs any other real CLI command as the Player (identity switches and `--server` refused) |
| `workspace` | `stage`, `integrate`, `merged`, `publish`, `bootstrap` | `integrate` and `publish` are the orchestrator's; `bootstrap --game G` prepares another game's clone |
| `promotion` | `submit`, `bootstrap`, `status`, `accept` | the orchestrator's; denied to researchers |

Every command prints one JSON object: `{"ok": true, "data": ...}` or
`{"ok": false, "error": {"reason", "message", "details"?}}`.

To see the cards: `hermes kanban --board autoresearch list`.

## Games

A game is a folder `games/<game>/`: `GAME.md` (the contract: bindings, metrics, league,
guide), the binding scripts, the binding skills `skills/<binding>/SKILL.md` (named
`<game>-ab`, `<game>-survey`, `<game>-replay-inspection`, `<game>-eval-design`,
`<game>-diagnosis`) and a `seed/` for its canonical repository. The field reference is
[games/_template/CONTRACT.md](games/_template/CONTRACT.md); the rest of `games/_template/` is
the scaffold for a new game. The design is
[docs/designs/multi-game-research.md](../docs/designs/multi-game-research.md).

- **Shipped** games are this folder's `games/`. **Installed** games live in the host's
  `<hermes root>/games/` (`/opt/data/games` in hosting); the orchestrator installs one from a
  card's committed folder. A shipped name always wins.
- `config.yaml` adds `../../games` (relative to the profile) to `skills.external_dirs`, so
  installed games' binding skills load as skills. The directory must exist when Hermes starts;
  the bootstrap creates it.
- A card's game is the game of its worktree's clone. The CLI points `RESEARCH_GAME`,
  `RESEARCH_CANONICAL` and `RESEARCH_WORKSPACE` at it inside the worker, so every command,
  binding and resolver works on that game. The episode budget stays one host-wide allowance.
- A new game: the `game-create` skill fetches the game's source (and a public mixin, an
  optimizer-seed knowledge pack, if there is one), scaffolds the folder with `game new`,
  fills it from verified source facts, and runs `game check` until it has no problems. Gaps,
  such as a binding this host cannot run, are reported, not hidden.

```sh
"$RESEARCH_CLI" game list
"$RESEARCH_CLI" source fetch --repo https://github.com/Metta-AI/polyworld
"$RESEARCH_CLI" game new --name newgame --league league_example --out /tmp/newgame
"$RESEARCH_CLI" game check --name newgame --root /tmp/newgame
```

`source fetch` accepts only public `https://github.com/<owner>/<repo>` URLs. It keeps a bare
cache of every fetched commit at `<hermes root>/sources/github.com/<owner>/<repo>.git` and a
read-only snapshot per commit at `<repo>@<sha12>/` beside it, and prints both paths.

## Update

```sh
git -C /path/to/ide-distributions pull --ff-only
hermes profile update "$PROFILE_NAME" -y
```

This replaces the files the distribution owns and keeps `.env`, `config.yaml`, memories, the
workspace and `local/`. Run the bootstrap again afterwards: it resyncs the venv when `uv.lock`
changed and re-seeds `AGENTS.md` (one canonical commit) when `doctrine/VERSION` changed.

## Restore on a new host

All research state is in the canonical repository: records, plan, strategy, promotions,
and every pushed `card/*` branch. Keep a copy of the bare repository (`RESEARCH_CANONICAL`).
On a new host, put that copy at `RESEARCH_CANONICAL`, install each profile, write its `.env`
and run the bootstrap: it clones the existing history instead of seeding. The Kanban board is
not restored. The orchestrator's doctrine then stops new work until an operator confirms the
recovery, so it does not re-plan over surviving card branches and platform requests.

## Develop and test

The Python package `src/ide_research_core/` is a generated copy of
`shared/ide_research_core/` at the repository root: edit there, then run
`python3 tools/sync_shared.py` from the repository root.

```sh
uv sync --frozen
uv run pytest -q
```

Tests that need a real Hermes install carry the `hermes` marker and are excluded by default.
They install from `git archive HEAD`, so commit first. Run them with the Hermes install's own
`bin` directory first on `PATH` (a `hermes` launcher that resolves `python3` through `PATH`
would otherwise run in this project's venv), and limit them to `tests/integration`: the GotA
tests' fixture refuses any real agent binary once it is active:
`HERMES_PYTHON=<hermes>/bin/python PATH=<hermes>/bin:$PATH uv run pytest -q -m hermes tests/integration`.

## Known gaps

- **GotA is bound but not proven end to end.** `games/gota` has real build, local check,
  evaluate and upload bindings, and the compiler passed M1 on engine 2026.10.7.2 only. No
  research cycle has run on GotA under the orchestrator yet.
- **Pudge Wars Basic is bound but not proven end to end.** `games/pudge-wars-basic` has real
  build, local check (G1–G4), evaluate and replay bindings, and a native headless match runner
  checked against the league host on 2026-10-26 (version 0.1.8); no research cycle has run on it
  under the orchestrator yet.
- **Container players.** Most coworlds other than GotA play a Docker image. Hosted sandboxes
  have no Docker, and `softmax upload` sends a file or directory, so a new game folder for such
  a game can hold its docs, skills and `evaluate` binding but not a build that runs here; its
  `host_requirements` say what is missing.
- **Public repositories only.** `source fetch` has no GitHub credential; most mixins are
  private, so `game-create` usually builds a folder from the game source alone.
- **One OS user.** Workers, the orchestrator and Hermes run as one user, so no guard is a
  security boundary: a script that pushes canonical `main` from Python is not blocked (the
  guard sees model tool calls only), and `canonical.lock` is advisory. The next integrate's
  overlap check notices an unexpected commit on `main`.
- **Curator.** Hermes has no key to exclude single skills from the curator. Shipped skills
  are safe because the curator only manages skills with an agent-created record, and
  `curator.prune_builtins` is false. Do not run `hermes curator adopt` on a shipped skill.
- **Credits.** The player reads the XP credit balance over HTTP
  (`GET /observatory/usage/me/credits`), because coworld 0.1.58 has no `credits` command yet.
