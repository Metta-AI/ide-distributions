---
name: "{{GAME}}-replay-inspection"
description: Use when decoding or reading {{GAME}} replays.
---

# Replay-inspection binding: {{GAME_TITLE}}

<!-- research-template-stub: fill every section from the game source and a real replay,
then delete this comment. -->

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/replay-inspection/SKILL.md`. The core `replay-diagnosis` skill carries
the method: the replay is ground truth, the policy's artifacts are its point of view, and
diagnosis lives in the gap between them. This skill supplies the formats and tools.

## Replays

The format, how to open one (viewer URL or the `replay_inspect` binding), how to decode it
into analysable data, and any version coupling between decoder and game build.

## Policy artifacts

What our policy's per-episode artifacts or logs contain, where its decision trace is, and the
field that joins an artifact event to a replay moment.

## What to look at

The moments that carry signal in this game (deaths, scoring events, engagements, idle time).
