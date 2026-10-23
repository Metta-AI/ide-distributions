---
name: "{{GAME}}-diagnosis"
description: Use when diagnosing why {{GAME}} episodes are lost.
---

# Diagnosis binding: {{GAME_TITLE}}

<!-- research-template-stub: fill every section from the game source and real episodes,
then delete this comment. -->

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/diagnosis/SKILL.md`. The core `diagnose` skill carries the method:
signals become two to four varied, mechanistic hypotheses, each pinned to a code location.
This skill supplies the game's failure vocabulary and instruments.

## Failure vocabulary

The named ways policies lose here, specific enough that an episode can be classified against
them.

## Triage table

Seat, role or phase, then the failure classes to check first, cheapest first.

## Instruments

What confirms a mechanism (queries, trace flags, analysis scripts under `tools/`), each with
its command line.
