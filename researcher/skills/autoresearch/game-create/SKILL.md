---
name: game-create
description: Use when a card asks for a new game's folder.
---

# Make a game folder

A card asks for research support for a game that has none: no shipped or installed
`games/<game>/`. You make the folder in your worktree; the coordinator checks and installs it
(`game_install`), and then cards can name the game. You never install it yourself.

This adapts optimizer-seed's mixin method (`tools/add_game.sh` and `games/_template/`): install
a game's existing knowledge pack when one is public, otherwise build the folder from the game's
source. A binding you cannot fill is a **gap**: say so plainly, never paper over it.

Doctrine upheld: D-04, D-07, D-23, D-25, D-26.

## Reads and writes

- Reads: the card (league, Coworld, any mixin or source it names), the league's participation
  guide and Coworld manifest, the game source, a mixin, completed league episodes.
- Writes: `games/<game>/` in your worktree, staged. Nothing else: no research records in this
  game's workspace, no upload, no installation.

## Procedure

1. Resolve the league and what already exists:
   `"$RESEARCH_CLI" softmax game-support --league <league_id>`
   and `"$RESEARCH_CLI" game list`. If `installed_research_support` is not empty, stop and
   report it: the game has support. Read the participation guide and the Coworld manifest:
   the game runnable's `source_url` (repository and commit), the player entries (the starter
   policy) and how a policy is submitted (a game-hosted file, or a container image).
2. Choose the folder name: lowercase letters, digits and hyphens, at most 40 characters, not
   a name `game list` shows. Use the card's name when it gives one.
3. Fetch the game source at the manifest's commit:
   `"$RESEARCH_CLI" source fetch --repo https://github.com/<owner>/<repo> --ref <commit>`
   It prints the snapshot's `path` (read it; never edit it) and `commit`. Only public GitHub
   repositories can be fetched. A `fetch_failed` for a private repository is a gap: say which
   repository the folder could not read.
4. Look for a mixin, the game's optimizer-seed knowledge pack: the card may name one, else try
   `https://github.com/Metta-AI/<game>-mixin` with `source fetch`. Most are private, so a
   failure only means there is no public mixin.
5. Scaffold from your worktree's root:
   `"$RESEARCH_CLI" game new --name <game> --league <league_id> --title "<Game name>" --source <repo url> --source-ref <commit>`
   and add `--mixin <url>` when step 4 found one. It writes `games/<game>/` from the template:
   `GAME.md`, five stub bindings in `tools/`, the five binding skills in `skills/`, `seed/`
   and `PROVENANCE.md`. With a mixin, `mixin/` holds the mixin's files and `skills/` its
   skills, renamed `<game>-<skill>`.
6. Review a mixin as `add_game.sh` asks: `mixin/MIXIN.md` lists the skills it provides (a
   missing required one is a gap), and `mixin/AGENTS.md` names the mechanics source of truth.
   Mixin knowledge is a lead, not a fact, until the source confirms it.
7. Fill the contract. The field reference is `games/_template/CONTRACT.md` under your profile
   (`"$HERMES_HOME"`). Every mechanics fact comes from the source snapshot and is cited
   `<path>:<a>-<b>@<commit>` (D-07); never write one from memory.
   - Front matter: `outcome_metrics` from the fields of the results file, with directions;
     `performance_guardrails`; `min_episodes_floor`; `layers`; `compiler: none`;
     `host_requirements` for every tool a binding needs (for example `docker` for an image
     build). No `{{...}}` placeholder may remain.
   - The guide: every section, including `## Seats and opponents` and `## Starter policy`.
   - The five binding skills in `skills/`, from the source and the mixin.
8. Implement the bindings this host can run. Always `evaluate`: get real results with
   `"$RESEARCH_CLI" softmax league-results --league <league_id> --limit 5 --dest .scratch/league-results`
   and make `<evaluate> --results <file> --json` print one entry per seat for each of them.
   Run bindings from your worktree's root (the folder that holds `games/`), with absolute
   paths. For a game-hosted file policy, also implement `build`, `local_check` and the starter
   build. For a container-image policy, describe the build in `## Bindings`, declare its tools
   in `host_requirements`, and leave a stub that cannot run here as a gap. Tools write only
   where `games/_template/CONTRACT.md` ("Where tools may write") allows: once shipped, the
   game folder is read-only.
9. Write `seed/strategy/STRATEGY.md` (the starter policy and what a change can target) and
   `seed/plan.md`.
10. Check until there are no problems:
    `"$RESEARCH_CLI" game check --name <game>`
    `problems` block installation; fix every one. `gaps` and `host_missing` may remain; each
    must be true and explained in `GAME.md`.
11. Stage the folder with `"$RESEARCH_CLI" workspace stage` and complete the card (see
    `card`). The summary gives the folder name, `games/<game>`, the source and mixin commits,
    the remaining gaps and missing host requirements, and what research the folder can
    support now. The coordinator installs it.

## Failure handling

- The league or its Coworld cannot be read: block with a `question:` naming what you need.
- A command of our tooling fails with a traceback or `errored`: a tool fault (D-26, see `card`).
- The game's policy cannot be built or uploaded on this host: that is a finding. Finish the
  folder with the gaps, and say exactly what the host would need.
