# researcher

A research worker for the Softmax IDE: it improves a game policy (first game: Gods of the
Arena, GotA) by working Kanban cards that the `orchestrator` profile gives it. It never
talks to the user; the orchestrator does. Several researchers can work one game on one host,
each as its own profile (`researcher-1`, `researcher-2`, ...) installed from this folder.

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
   profile, with a git worktree of this profile's clone of the canonical workspace on the
   branch `card/<id>`.
2. The gateway's dispatcher runs the card as `hermes -p <profile>`. The worker reads the card
   (`kanban_show`), does the research with the tools and skills below, and writes one journal
   entry.
3. Its last step is `workspace integrate` (the research CLI): it commits the worktree, pushes the
   card branch, validates the changed records, merges into canonical `main` under a lock that
   every profile on the host shares, regenerates the knowledge-base views and pushes `main`.
4. It ends the card with `kanban_complete(summary, metadata)` (the metadata contract is in
   `skills/autoresearch/card/SKILL.md`), or `kanban_block` when integrate fails or it needs an
   answer it cannot work without. Mid-card it comments on its own card (`note:`, `question:`,
   ...); the orchestrator's comments arrive as user-authority steers.

What a researcher may and may not do:

| | Researcher | Enforced by |
|---|---|---|
| Upload builds, launch and read XP, write records, KB and stats in its worktree | yes | |
| Submit to the league, bootstrap or accept a champion (`promotion ...`) | no | `approvals.deny` in `config.yaml` |
| Create, link or schedule cards | no | the `researcher_guard` plugin |
| Run `git push` (any branch) | no, `workspace integrate` pushes the card branch and `main` | the `researcher_guard` plugin |
| Change `strategy/STRATEGY.md`, `control/`, `promotions/` | no | `workspace integrate` refuses |
| Force pushes, `coworld` platform mutations, `softmax login`, `player use` | no | `approvals.deny` |

There is no spend management in this profile on purpose: the hosting allowance and Hermes's
own limits (`agent.max_turns`, `agent.run_budget_seconds`, card runtime and retry limits) are
the bounds.

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| `git` | any recent | the canonical workspace and its clones |
| `uv` | any recent | the profile venv (the launcher runs `uv sync --frozen`) |
| Hermes Agent | v0.21.6 | the dispatcher, the workers and Kanban |
| Nim | 2.2.10 | building GotA native tools and running native gate/replay tests; pure Python tests need no Nim |

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
board, `autoresearch`, and the same bare canonical repository of the game.

## Configure `.env`

Write `$PROFILE/.env` (Softmax hosting writes it for you):

| Variable | Meaning |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter key for the research model |
| `SOFTMAX_PLAYER_CREDENTIAL` | the player's credential (a `cga_` agent token, or a player session as JSON) |
| `RESEARCH_PLAYER_ID` | that player's id |
| `RESEARCH_CANONICAL` | the bare canonical repository of the game, shared by every profile on the host, for example `<host root>/canonical-gota.git` |
| `RESEARCH_GAME` | the game, a directory under `games/` (default `gota`) |
| `RESEARCH_WORKSPACE` | this profile's clone (default `$PROFILE/workspace`) |
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
"$RESEARCH_CLI" kb lint
"$RESEARCH_CLI" workspace merged --branch card/t_1234abcd
```

| Group | Commands | Notes |
|---|---|---|
| `records` | `new`, `show`, `update`, `status`, `link`, `find`, `validate`, `summary`, `resolve` | the ontology's records; `resolve` reads an `ar://` ref at a commit |
| `stats` | `design`, `noise`, `ingest`, `evaluate` | pre-registered evaluation and the decision rule |
| `kb` | `lint`, `apply`, `index`, `stale`, `renew`, `compact`, `hubs` | maintenance cards run `apply`/`stale`/`renew`/`compact` in their worktree |
| `game` | `show` | the game contract |
| `softmax` | `whoami`, `credits`, `xp create/status/recover/evidence/artifacts`, `upload`, `upload-bootstrap`, `leaderboard`, `game-version`, `standings`, `league-episodes` | every platform action of a researcher |
| `workspace` | `integrate`, `merged`, `publish`, `bootstrap` | `publish` is the orchestrator's |
| `promotion` | `submit`, `bootstrap`, `status`, `accept` | the orchestrator's; denied to researchers |

Every command prints one JSON object: `{"ok": true, "data": ...}` or
`{"ok": false, "error": {"reason", "message", "details"?}}`.

To see the cards: `hermes kanban --board autoresearch list`.

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
- **One OS user.** Workers, the orchestrator and Hermes run as one user, so no guard is a
  security boundary: a script that pushes canonical `main` from Python is not blocked (the
  guard sees model tool calls only), and `canonical.lock` is advisory. The next integrate's
  overlap check notices an unexpected commit on `main`.
- **Curator.** Hermes has no key to exclude single skills from the curator. Shipped skills
  are safe because the curator only manages skills with an agent-created record, and
  `curator.prune_builtins` is false. Do not run `hermes curator adopt` on a shipped skill.
- **Credits.** The player reads the XP credit balance over HTTP
  (`GET /observatory/usage/me/credits`), because coworld 0.1.58 has no `credits` command yet.
