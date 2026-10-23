---
name: "{{GAME}}-eval-design"
description: Use when sizing a {{GAME}} evaluation.
---

# Eval-design binding: {{GAME_TITLE}}

<!-- research-template-stub: fill every section from the game source and measured noise,
then delete this comment. -->

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/eval-design/SKILL.md`. The core `experiment` and `calibrate-noise`
skills freeze a design (`stats design`); this skill names the game's knobs.

## Sensible line-ups

For each common question (field evaluation, A/B arm, seat-pinned probe, crash test): how many
seats, which `seats` we fill, and the `opponent_count` (see `## Seats and opponents` in
`GAME.md`).

## Episode-count floors

The smoke, directional and verdict counts, with the variance evidence behind them (the noise
record, `stats/metric-noise.yaml`).

## Seat pinning

What can be pinned, when pinning helps a question and when it hides the effect being tested.

## Pacing

Episode duration and the batch pacing that avoids contaminated comparisons.
