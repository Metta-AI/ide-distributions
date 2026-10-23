---
name: "{{GAME}}-ab"
description: Use when comparing {{GAME}} versions or policies.
---

# A/B binding: {{GAME_TITLE}}

<!-- research-template-stub: fill every section from the game source and observed results,
then delete this comment. -->

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/ab/SKILL.md`. The core `ab-compare` and `review-xp` skills carry the
comparison standard (fresh and matched arms, taint before means, decompose before judging,
noise is a verdict). This skill supplies what is game-shaped.

## Metrics that matter

Each `outcome_metrics` entry of `GAME.md`: what it measures, its direction, and one line on
why it matters for winning here. Say which field of the results file it comes from.

## Decompositions

The splits to apply before any verdict (seats, teams, roles, phases, maps) and why the
aggregate misleads without them.

## Taint

How to detect an episode that must be dropped before any mean (disconnects, no-shows,
platform failures), and how to tell "our policy crashed" from "the platform failed".

## Tests and floors

Which tests fit which metrics, and the episode floor below which a result is directional only
(`min_episodes_floor` in `GAME.md`; measured noise may raise it).
