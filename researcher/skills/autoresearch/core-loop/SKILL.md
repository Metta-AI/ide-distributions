---
name: core-loop
description: Use when a card is a coach's core-loop program request.
---

# Core loop: make, change, upload and practise a program

A plain, fixed recipe for the coach's direct request: make a first program, change one, or
practise one. It is not research. Implements the build, upload and launch steps of
System Design §5.4 and nothing else. Doctrine upheld: D-01, D-03, D-21, D-26, D-27.

Skip, and never ask for: a champion, topics, hypotheses, a noise record, an evaluation design,
seeds or an experiment. Do not call `stats`, and do not wait for XP. If the card seems to need
one of them, the card is wrong: say so in your report; do not invent it.

Use `card` for the lifecycle (read the card with `kanban_show`, one short journal with
`## Decisions`, integrate last, complete or block) with `experiment: null`. Use `report` for
every message. The orchestrator tells the user; you never do.

## Steps

1. **Read the card.** Title, body and the coach's words: the coworld, the `play.md` URL, the
   program to start from, the change wanted (none for a first program), the practice
   `policy_ref` when the coach gave one, the program name.
2. **Read `play.md`.** `curl -fsSL "<play.md URL>"`. Take from it the recommended starter and
   the practice seats and episode count (default 1 episode in seat 0). Do not read research
   notes, the knowledge base or `plan.md`.
3. **Get the starter.** Follow the game's `## Core loop: the recommended starter` section in
   `GAME.md` (`"$RESEARCH_CLI" game show` prints its path). A starter the coach named by
   `policy_ref` is forked as given. Check every hash the game's section tells you to check. A
   mismatch is a failure (see Failures).
4. **Make the change.** Apply exactly the change the coach asked for, or only rename it for a
   first program. Check the changed file really contains the change; a build that passes does
   not prove it. Choose `--name` (lowercase letters, digits, hyphens, at most 23 characters)
   from the coach's wording and reuse it on retry.
5. **Build it.** Only the checks the game's section marks as required to produce a valid
   program. Skip all research gates. Never reduce or bypass a required check (D-27).
6. **Upload it as the cogent's own program.** Never log in with another client or credential:
   `"$RESEARCH_CLI" softmax upload-build --name <name> --build-dir <completed build dir>`.
   Keep `data.version_id` and `data.platform_policy_version_id`; the latter is the program's
   `<name>:v<N>` label on the platform.
7. **Request practice.** Use the `policy_ref` exactly as the coach gave it, with no lookup
   first. When the coach gave none, use `platform_policy_version_id` of the program you just uploaded:
   `"$RESEARCH_CLI" softmax practice --policy-ref <ref> --episodes 1 --seats 0`
   (use the seats and episode count from `play.md`; add `--league` only if the card names
   one). Keep `data.request_id`. A refusal is not a failure to hide: `credit_denied`,
   `permit_required` and `league_not_chosen` come back with `request_id: null` and a `reason`;
   report them as they are (D-27) and do not retry around them.
8. **Report the program.** `note:` comment on your own card: the program name and version,
   the practice request id, and the sentence "practice requested".
9. **Do not wait for practice (D-11).** Complete now with `awaiting: {"xp": [<request id>]}`.
   The orchestrator reads the result and tells the user. If the card is a recovery of an
   earlier attempt, read it with `"$RESEARCH_CLI" softmax practice --status <request id>` and
   put the result (state, episodes completed) in the summary in plain words.
10. **Finish with `card`.** Summary, findings first: "Uploaded <name>:v<N> (version
    <V-id>). Practice <request id> requested." Metadata: `outcome: done`, `experiment: null`,
    `awaiting: {"xp": [<request id>]}`, `next: "release <name>:v<N> when practice completes"`.
    Never release (submit) a program yourself; the orchestrator does.

## Container coworlds (available when the host supports it)

A coworld whose player is a container image cannot use the build above (no Docker in the
sandbox). Its build goes through the host build request: the host builds and uploads the
image as the cogent's program (Metta-AI/metta PR #28145; off by default, status pending).

1. Write `/opt/data/builds/<uuid>/request.json` with `{name, dockerfile, files[], run, env}`
   (at most 256 files and 4 MB; plain files only, no symlinks), using a fresh `<uuid>`.
2. Create the empty `ready` marker in the same directory, only after `request.json` is complete.
3. Read `/opt/data/builds/<uuid>/status.json`: `queued`, `building`, `pushing`, then
   `registered` (with `policy_version_id` and `version`) or `failed` (with `error_tail`).
   Read it with a short wait between reads, for at most 2 minutes.
4. If no `status.json` appears within 2 minutes, stop. Report that container builds are not
   enabled on this host yet, and give the directory you wrote. Do not retry and do not stall.
5. On `registered`, continue at step 7 with the new `<name>:v<N>`. On `failed`, report the
   `error_tail` verbatim.

## Failures: never go silent

On any failure (a command, a hash check, a refusal, a timeout): send a `help_request:` comment
on your own card with the exact command you ran and the exact error text (the first lines of
stderr, or the JSON `error.reason` and `error.message`), what you had finished, and what the
coach should know. Then finish through `card`: `kanban_block` with `kind: "needs_input"`, or
`partial` when the upload succeeded and only practice failed (include the version label so the
orchestrator can retry the practice). A tool fault follows D-26: no retry. A `credit_denied`,
`permit_required` or `league_not_chosen` practice answer is `partial` with that reason in the
summary. `identity` is D-03: stop, do not change the Player.
The user must always hear what happened: your report is the only way they will.
