---
name: "{{GAME}}"
bindings:
  build: "python3 games/{{GAME}}/tools/build.py"
  local_check: "python3 games/{{GAME}}/tools/local_check.py"
  upload: "python3 games/{{GAME}}/tools/upload.py"
  evaluate: "python3 games/{{GAME}}/tools/evaluate.py"
  replay_inspect: "python3 games/{{GAME}}/tools/replay_inspect.py"
reference_types: {}
outcome_metrics:
  - {name: "{{METRIC}}", direction: up}
performance_guardrails: ["{{METRIC}}"]
min_episodes_floor: 30
league_id: "{{LEAGUE_ID}}"
noise_source: league_episodes
layers: [parameters]
compiler: none
source: {repo: "{{SOURCE_REPO}}", pin: coworld_manifest}
---
# {{GAME_TITLE}}

<!-- research-template-stub: replace every section below with verified facts, then delete
this comment. Cite the game source as `<path>:<a>-<b>@<commit>` (from `"$RESEARCH_CLI" source
fetch`) and never answer a mechanics question from memory. The field reference is
games/_template/CONTRACT.md in the researcher distribution. -->

{{MIXIN_NOTE}}

## The game in one paragraph

<!-- research-template-stub -->
What kind of game this is, how many players, what an episode looks like and what winning
means, written for an agent who has never seen it.

## Scoring and what ranks

<!-- research-template-stub -->
How episode scores are produced (the results file the `evaluate` binding reads), how the
league aggregates them, and so which `outcome_metrics` tuning should target.

## Seats and opponents

<!-- research-template-stub -->
How many seats an episode has, which slots our policy fills (`seats` in a design), team
rules, and so how many opponents a line-up needs (`opponent_count`).

## Mechanics ground truth

<!-- research-template-stub -->
The game source of truth is the `source` repository at the commit the Coworld manifest pins.
Verified facts accumulate here, each with its citation.

## Policy surface

<!-- research-template-stub -->
What a policy is for this game (a game-hosted file, or a container image), what
`strategy/STRATEGY.md` controls, and which STRATEGY.md `layers` apply.

## Bindings

<!-- research-template-stub -->
How to run each binding from the game's root (`root` in `"$RESEARCH_CLI" game show`), with
absolute paths: `build <candidate> --out <build dir> --json` (writes the artifact and
`version.json`), `local_check`, `evaluate --results <results.json> --json` (one entry per
seat), `replay_inspect`. A binding still printing `not_implemented` is a gap. A build that
needs a tool this host lacks (for example `docker`) lists it in `host_requirements`.

## Starter policy

<!-- research-template-stub -->
How to build the game's starter policy with the `build` binding, from
`<worktree>/strategy/STRATEGY.md`, so a bootstrap card can upload it with `"$RESEARCH_CLI"
softmax upload-build` and the orchestrator can submit it as the first champion.

## Binding skills

| Skill | Use when |
|---|---|
| `{{GAME}}-ab` | comparing versions or policies |
| `{{GAME}}-survey` | reading a batch of episodes |
| `{{GAME}}-replay-inspection` | extracting what happened in an episode |
| `{{GAME}}-eval-design` | sizing an evaluation |
| `{{GAME}}-diagnosis` | turning signals into hypotheses |
