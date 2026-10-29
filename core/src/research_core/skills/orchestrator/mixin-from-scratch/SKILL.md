---
name: mixin-from-scratch
description: Use when a Coworld's repository has no mixin to copy.
---

# Build a mixin from scratch

Setup found no `MIXIN.md` in the Coworld's source repository (the setup status says the mixin
is missing). The tools need a mixin at `code/mixin/` before anything builds, uploads or
evaluates, so you write one from the source, in the optimizer-seed shape. Every mechanics fact
comes from the source with a citation, never from memory.

## What a mixin must have

- `MIXIN.md` with a YAML front matter the tools read: `coworld` (the platform Coworld id, exactly
  the workspace's), `coworld_version`, `commands {build, check, upload, evaluate}`, `metrics`
  (name, direction `up` or `down`, role `primary` or `guardrail`, `min_effect`,
  `minimum_episodes`), `artifact {kind: file, path}`, `evidence_types`, `toolchains` (another
  runtime, such as a Nim or Python version, with a URL and sha256) and `cost_per_episode`. The
  prose after it: identity, provenance (source URL, commit, date), the skill manifest, what else
  the mixin provides.
- Five binding skills under `skills/`, each named `<coworld>-<binding>`: `survey` (what a batch
  of episodes shows), `eval-design` (rosters, episode floors by question, seat pinning, pacing),
  `ab` (the metrics that matter, decompositions, taint, tests, floors), `replay-inspection` (how
  to open and decode replays, what the policy's own artifacts hold) and `diagnosis` (the failure
  vocabulary and instruments). A binding is filled when its knowledge is real even before its
  tooling exists; write "no tooling yet" rather than a stub.
- A lab `AGENTS.md`: rules, scoring, structures, platform details, where mechanics are verified.
- `docs/`: enough for a newcomer to understand the game and how it is won.
- A reference policy: the repository's starter or example policy, buildable, with a version log.
- `tools/build.sh`: the build entry point the `build` command calls.

## Procedure

1. Read what setup already did: `research coworld setup-status` from the workspace environment
   (`workspace.environment_guide` has the path) and `workspace.doctor`. `source/` holds the
   repository at the release commit; a `COWORLD.md` there, when present, explains the layout.
2. `platform.coworld {coworld}` for the manifest: name, version, `player_runtime`, source and
   commit. Only a file policy is supported now; `version.create` refuses an image artifact, so
   when the runtime needs an image, say so, and stop at the docs and the bindings.
3. Find the starter policy, the rules and the scoring in `source/`; the forum and wiki
   (`platform.forum_search`, `platform.wiki_search`) often hold strategy notes. Cite each fact as
   `<path>:<lines>@<commit>`.
4. Write `code/mixin/` on `main` in the Coworld's `code/` folder (this is setup, not a change of
   your own: no worktree). Start with the front matter, then `tools/build.sh`, `AGENTS.md`, the
   docs and the five bindings.
5. Make `evaluate` meet the contract: it runs in the code repository root with `XP_RESULTS`
   naming a folder of downloaded results and prints JSON lines, one per scored seat per episode:
   `{"episode", "seed", "seat", "opponents": [...], "metrics": {...}}`, with `"status":
   "failed"` and a `"reason"` for an episode that did not complete. Metric names match
   `metrics` in the front matter. Test it on real results: `platform.episodes {league}` and
   `platform.results` show finished episodes.
6. Make `build` produce the artifact at the declared path from a clean tree and `check` fail on
   a broken policy. `upload` may be a dry run of the upload route when the platform offers one.
7. Commit on `main` with a message naming the source commit, then `workspace.capabilities
   {check: true}`: the watcher runs build, check, upload and evaluate next tick. Setup ends only
   when build and upload pass; a failure creates a maintenance Task on its own.
8. Import the repository's lesson notes as claims with `claim.write` and `doc` evidence when the
   deterministic import skipped them (the setup status lists what was imported).

## Gaps

A binding you cannot fill from the source is a gap: say so in `MIXIN.md` and in the skill, with
what would fill it. Never paper over it. A private source repository you cannot read is a gap to
report with `ui.notify`, naming the repository. Record the mixin's provenance and its gaps as a
claim so the next Session finds them.
