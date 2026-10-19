---
name: core-loop
description: Use when a card is a coach's core-loop program request.
---

# Core loop: make, change, upload and practise a program

A plain, fixed recipe for the coach's direct request: make a first program, change one, or
practise one. It is not research. Implements the build, upload and launch steps of
System Design §5.4 and nothing else. Doctrine upheld: D-01, D-03, D-21, D-26, D-27.

**Core-loop mode only.** This skill applies only when `$SOFTMAX_COGENT_MODE` is `core-loop`
(check with `printenv SOFTMAX_COGENT_MODE`). If it is anything else, stop: do nothing the steps
below describe, and block the card (`kanban_block`, `needs_input`) saying this runtime is not in
core-loop mode. A research card never uses this skill.

Skip, and never ask for: a champion, topics, hypotheses, a noise record, an evaluation design,
seeds or an experiment. Do not call `stats`, and do not wait for XP. If the card seems to need
one of them, the card is wrong: say so in your report; do not invent it.

**IDE tools first.** The IDE's tools (MCP server `ide`) are the primary path for upload, practice
and the container build. Each takes a fresh `request_id` you choose (letters, digits, `-`, `_`);
keep it for a retry of the same call. `conversation_id` is filled in for you. The shell commands
below (`"$RESEARCH_CLI" softmax ...`, the host build request) are the fallback, used only when an
IDE call answers `ide_unreachable`. You never call `enter_league`: the orchestrator releases.

Use `card` for the lifecycle (read the card with `kanban_show`, one short journal with
`## Decisions`, integrate last, complete or block) with `experiment: null`. Use `report` for
every message. The orchestrator tells the user; you never do.

## Steps

1. **Read the card.** Title, body and the coach's words: the coworld, the league id, the
   `play.md` URL, the program to start from, the change wanted (none for a first program), the
   practice `policy_ref` when the coach gave one, the program name.
2. **Read `play.md`.** `curl -fsSL "<play.md URL>"`. Take from it the recommended starter and
   the practice variant. `game_contract` (IDE) lists the published practice `variant_id`s. Do not
   read research notes, the knowledge base or `plan.md`.
3. **Get the starter.** Follow the game's `## Core loop: the recommended starter` section in
   `GAME.md` (`"$RESEARCH_CLI" game show` prints its path). A starter the coach named by
   `policy_ref` is forked as given. Check every hash the game's section tells you to check. A
   mismatch is a failure (see Failures).
4. **Make the change.** Apply exactly the change the coach asked for, or only rename it for a
   first program. Check the changed file really contains the change; a build that passes does
   not prove it. Choose the name (lowercase letters, digits, hyphens, at most 23 characters)
   from the coach's wording and reuse it on retry.
5. **Check it.** Only the checks the game's section marks as required to produce a valid
   program. Skip all research gates. Never reduce or bypass a required check (D-27).
6. **Upload it as the cogent's own program.** By the game's player kind:
   - **File players (GOTA)**: IDE `file_policy_upload` with `name` and the complete file text
     as `source`. It returns the version (`id` UUID, `name`, `version`); the label is
     `<name>:v<version>`. No local build is needed for the upload.
   - **Container coworlds**: IDE `container_policy_source` (`action: save`, the complete file
     set, a fresh `revisionId`), then `container_policy_build` (`revision_id`, `request_key`),
     then `container_policy_status` (`kind: build`, at most once a minute, stop on a terminal
     state) or `container_policy_wait`, then `container_policy_upload` (`build_id`,
     `player_id`, `name`). Reuse `request_key` on a retry; never start a second build to
     recover an uncertain upload.
   Keep the version `id` and the `<name>:v<N>` label.
7. **Request practice.** IDE `policy_practice` for a file program (`policy_version_id` is the
   version `id`, `variant_id` from `game_contract`, `request_key` fresh), or
   `container_policy_practice` for a container program (`upload_id`, `variant_id`,
   `request_key`). Keep the returned practice `id`.
8. **Approvals.** Upload, build and practice run in the coach's Ask mode and may answer
   `approval_required`. You cannot tell the coach. Send a `help_request:` comment on your own
   card with the tool name, its exact arguments and its `request_id`, then finish with
   `kanban_block` (`needs_input`) stating "waiting for the coach's approval in the IDE". When the
   orchestrator answers that the coach approved, repeat the **same** call with the **same**
   `request_id`; never change the `request_id`, the `name` or the `request_key`.
9. **Report the program.** `note:` comment on your own card: the program name and version, the
   version UUID, the practice id, and the sentence "practice requested".
10. **Do not wait for practice (D-11).** Complete now with `awaiting: {"xp": [<practice id>]}`.
    The orchestrator reads the result with `hosted_xp_wait` and tells the user. If the card
    is a recovery of an earlier attempt, read the practice with `hosted_xp_wait` (or
    `container_policy_status`, `kind: practice`) and put the result in the summary in plain words.
11. **Finish with `card`.** Summary, findings first: "Uploaded <name>:v<N> (version
    <version UUID>). Practice <practice id> requested." Metadata: `outcome: done`,
    `experiment: null`, `awaiting: {"xp": [<practice id>]}`, `next: "release <name>:v<N> when
    practice completes"`. Never release (submit) a program yourself; the orchestrator does.

## Fallback: the IDE is unreachable

Only when an IDE call answers `ide_unreachable` (the IDE could not be contacted or the
cogent has no IDE conversation), use the CLI:

- Upload: `"$RESEARCH_CLI" softmax upload-build --name <name> --build-dir <completed build dir>`
  after building with the game's required checks. Keep `data.version_id` and
  `data.platform_policy_version_id`, the `<name>:v<N>` label.
- Practice: `"$RESEARCH_CLI" softmax practice --policy-ref <ref> --episodes 1 --seats 0`
  (the coach's `policy_ref` exactly as given, else the label you just uploaded; the seats and
  episode count from `play.md`; `--league` only if the card names one). Keep `data.request_id`.
  `credit_denied`, `permit_required` and `league_not_chosen` come back with `request_id: null` and
  a `reason`; report them as they are (D-27) and do not retry around them. Read an earlier
  request with `"$RESEARCH_CLI" softmax practice --status <request id>`.
- Container build: write `/opt/data/builds/<uuid>/request.json` with `{name, dockerfile, files[],
  run, env}` (at most 256 files and 4 MB; plain files only, no symlinks) and a fresh `<uuid>`, then
  create the empty `ready` marker. Read `status.json` (`queued`, `building`, `pushing`, then
  `registered` with `policy_version_id` and `version`, or `failed` with `error_tail`) with a short
  wait between reads, for at most 2 minutes. With no `status.json` by then, stop and report that
  container builds are not enabled on this host; do not retry.

Then complete as in steps 9 to 11 with the CLI's `request_id`.

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
