---
name: replay-diagnosis
description: Use when an investigation needs a cause seen in replays.
---

# Replay diagnosis

This skill implements the evidence part of System Design §5.2 (investigation): fetch
completed evidence, pass the replay-analysis gate, name a cause, and write claims. It adapts
co-gas `coworld-replay-diagnosis` (its generic part). The next step is `diagnose`.

Doctrine upheld: D-04, D-05, D-07, D-14, D-16, D-23.

## Reads and writes

- Reads: evidence (XP results, replays, logs), Topics, claims, hubs.
- Writes: evidence records, claims, hub sentences that rest on them, Topics (only to create
  a missing one), and the journal.

## Procedure

1. Choose the target: an active Topic, the worst metric, the worst matchup, or a steering
   request. If no Topic covers it, create one with metrics and guardrails first:
   `"$AUTORESEARCH_CLI" records new topic --meta-file .scratch/topic.json --body-file .scratch/topic.md`
   with `{"title": …, "objective": …, "metrics": [{"name": …, "direction": "up|down"}], "guardrails": […], "origin": {"kind": "agent"}, "priority": "P2", "status": "active"}`.
2. Find completed evidence. The XP poller stores each completed XP request at
   `evidence/xp/<E-id>/<request id>/` (`results.json`, `rows.jsonl`) with an index record:
   `"$AUTORESEARCH_CLI" records find evidence --where kind=xp-result`
   `"$AUTORESEARCH_CLI" records find evidence --where kind=replay`
   `"$AUTORESEARCH_CLI" records show <EV-id>`
   For replays of a completed experiment, check that its requests are terminal, then
   download them into scratch:
   `"$AUTORESEARCH_CLI" softmax xp status <E-id>`
   `"$AUTORESEARCH_CLI" softmax xp artifacts <E-id> --dest .scratch/xp/<E-id>`
   Use only completed episodes (D-04). A local run is for debugging, never for a decision.
3. Pass the replay-analysis gate before any strategy change (D-05). All of these must hold:
   - The replay is from a completed episode.
   - Its hash is verified (`sha256sum <file>` equals the value the platform or the evidence
     record gives) and its mechanics (game) version is known.
   - A committed tool decodes it: run the game's `replay_inspect` binding from `"$AR_HOME"`
     (see the `queue` skill for `GAME.md` and `AR_HOME`). Check that the output is not empty
     and parses. An exit code of 0 is not enough.
   - Actions map to stable player ids and policy versions, not to seat numbers alone.
   - You record a concrete "before" metric, the tick or observation where the behaviour goes
     wrong, and a named cause.
4. Record each replay you inspected as evidence. Copy the file to
   `evidence/replays/<sha256>/` in the worktree, then:
   `"$AUTORESEARCH_CLI" records new evidence --meta-file .scratch/evidence.json`
   with `{"sha256": …, "kind": "replay", "path": "evidence/replays/<sha256>/<file>", "game_version": …, "policy_versions": […], "source_ids": {"episode": "<id>", "xreq": "<request id>"}, "fetched_at": "<ISO time>"}`.
   Evidence records never change after creation.
5. Write or update claims (D-07). A new claim:
   `"$AUTORESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md --slug <slug>`
   with `kind` (`behaviour` or `mechanism`), `status` (`hypothesis` or `belief`),
   `confidence` (0 to 1), `evidence` (`[{"ref": "ar://evidence/<EV-id>", "tier": "D", "direct": true, "stance": "supports"}]`),
   `revise_if`, and `topics`. Tiers: S game source at a pinned commit, A official docs,
   B a controlled A/B with n, C several independent hash-verified replays, D one replay,
   E a forum post or another agent. A claim with only D or E evidence stays a `belief`. The
   linter computes the grade; do not set `evidence_grade` yourself.
   To add evidence to an existing claim (the card must name it, D-23):
   `"$AUTORESEARCH_CLI" records link <C-id> evidence '{"ref": "ar://evidence/<EV-id>", "tier": "C", "direct": true, "stance": "supports"}'`
6. Update the hub sentences that rest on the claims. Every hub sentence cites a claim (D-16),
   and prose describes the present (D-14).
7. If the replay cannot show the cause, do not change strategy. Request an instrumentation
   card: a STRATEGY change of kind instrumentation that adds telemetry and no behaviour
   change. It makes a version with no experiment:
   `"$AUTORESEARCH_CLI" queue request create --title "Instrument <what>" --mode experimentation --why "<observation gap>" --ref <ar://topic/T-id>`
8. Go on with `diagnose`, or finish with `complete-card`.

## Failure handling

- `softmax xp artifacts` reason `artifacts_incomplete`: a request is not complete yet. Use
  the evidence you have, or request a later investigation card. Never wait in the card
  (D-11).
- The replay does not decode, or the hash does not match: the gate fails. Record the gap in
  the journal and request instrumentation or a tool fix. Never edit strategy from a low score
  alone.
- No command fetches league episodes or replays outside our XP requests. Use XP evidence.
