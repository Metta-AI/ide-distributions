# Orchestrator design: implementation notes

The approved design is [orchestrator-design-2026-10-15.html](orchestrator-design-2026-10-15.html)
(v4.4). This file records where the implementation differs from it or fills in what it left
open. Each item names the reason. Section numbers refer to the design's brief (v4.4 §N).

## Names

- **One environment prefix for both profiles: `RESEARCH_*`** (`RESEARCH_PLAYER_ID`,
  `RESEARCH_GAME`, `RESEARCH_WORKSPACE`, `RESEARCH_CANONICAL`, `RESEARCH_CLI`,
  `RESEARCH_SOFTMAX`), not `RESEARCHER_*` and `ORCHESTRATOR_*`. Both profiles describe the
  same research setup and run the same shared CLI; one set of names is simpler.
- **The research CLI is `research`** (`$HERMES_HOME/bin/research`, `RESEARCH_CLI`), written
  by `scripts/bootstrap.py`. The package is `ide_research_core`.

## Toolsets (decision A6)

- **The orchestrator has full shell, file and Python access**, unlike the design, which gave
  it only the `orchestrator` plugin toolset and rejected a terminal (A6).
  `platform_toolsets.api_server` and `.cron` are `[orchestrator, terminal, file,
  code_execution]`. Reason: the user needed it to inspect and change files and run commands
  directly. The checked plugin tools remain the path for board, integration, upload,
  submission and promotion writes, but that is now doctrine (`SOUL.md`), not enforced: no
  deny list restricts the orchestrator's terminal. Hermes's default approvals apply
  (`smart` in chat via the bridge's approval events; `cron_mode: deny` in cron turns).

## Built-in knowledge and naming

- **Champion submission, not promotion.** Softmax's "promotion" is the commissioner moving an
  entry between Divisions, so ours was renamed: `research champion`, `champion_submit`,
  `champion-submissions/CS-*.json`, experiment decision `submit`. `legacy_promotions.migrate`
  rewrites older canonical records once. It runs at workspace bootstrap, before every
  `research champion` command, and on the gate's 10-minute poll (`research champion migrate`),
  which wakes the coordinator with a `records_migrated` item so the user is told; it also
  prints one stderr line. Hosted startup skips bootstrap when doctrine is unchanged, which is
  why the poll exists.
- **Built-in knowledge** (`ontology/KNOWLEDGE.md`) and the Softmax vocabulary
  (`ontology/VOCABULARY.md`, generated from metta) are embedded in both SOULs before the
  ontology. Method skills live in `shared/skills/autoresearch/` and both profiles get them.
- **`capabilities`** lists and searches the session's tools and skills, and
  **`research softmax exec`** runs the real CLIs as the Player.

## Shipped games

- **Pudge Wars Basic** (`researcher/games/pudge-wars-basic/`) is the second real shipped game.
  Its binding skills follow the contract (`games/<game>/skills/<binding>/SKILL.md`, named
  `<game>-<binding>`); `tools/sync_shared.py` copies them into both research profiles'
  `skills/<game>/`, because Hermes loads skills only from there. GotA's skills live in
  `researcher/games/gota/skills/` the same way but keep their older bare names (`survey`, `ab`, ...).
- **The orchestrator has every shipped game folder**, not only `GAME.md`: the docs, tools,
  baselines and seed, so it knows each game as well as a researcher does and can seed a
  game's workspace itself.
- **`game_prepare`** creates a game's workspace with the orchestrator's own CLI
  (`research workspace bootstrap --game <game>`). The design made it only as a side effect of
  `card_create` preparing a researcher's clone. A workspace the orchestrator seeds starts
  without `AGENTS.md`, because the doctrine ships only with the researcher; the first card's
  preparation runs the researcher's bootstrap, which publishes it before the card's worktree
  is branched. `game_list` reports each game's `league_id` so the selected league can be
  matched to a game folder.

## Shared code and the ontology (§5.5, §5.6)

- The ontology is **embedded at the end of each `SOUL.md`** by `tools/sync_shared.py`, not
  copied to `<distribution>/ontology/`. Hermes loads `SOUL.md` in every session, cron
  included, so every turn has it without reading another file.
- For the same reason the orchestrator's doctrine is the `## Doctrine` section of its
  `SOUL.md`, not a separate `doctrine/orchestrator.md` (which nothing loaded).
- `tools/sync_shared.py` also copies each whole `researcher/games/<game>/` folder into
  `orchestrator/games/` (see Shipped games), skipping the tools' local build cache and binaries.
  Games installed at runtime live in the host's `<root>/games/` instead, which both profiles
  search after their shipped ones.
- **One host, several games** (beyond the design, which bound a host to one game):
  `RESEARCH_GAME` is the default game, and each card names its own. See
  [multi-game-research.md](multi-game-research.md).
- There is **no CI equality check** on the generated copies (design v4.4 cut it); the script
  is idempotent and the copies carry a `GENERATED.md` marker.
- The shared package's tests run in `researcher/` against its copy.

## Researcher (§4)

- `runtime/signals.py` is deleted, not rewritten (v4.4 already says so).
- **`softmax xp evidence <E>`** (new) is the evidence half of the old xp-poll: it downloads
  finished XP results, runs the game's evaluate binding, reduces our seats by the mean, and
  writes evidence files, rows and index records into the review card's worktree. The design
  listed "XP polling" as moving to shared code without saying who turns results into rows;
  doing it by hand in a skill would risk the paired comparison. The polling half is the
  orchestrator's supervisor gate.
- **Bootstrap is split**: the researcher's bootstrap card builds the starter policy and runs
  `softmax upload-build`; the orchestrator submits that version with
  `champion bootstrap --version V-...` (§9 said the orchestrator runs `bootstrap_champion()`,
  but the build and upload need a researcher's terminal).
- The KB's hub and staleness checks report findings (`kb hubs`, `kb renew` -> `reverify`)
  instead of writing card requests. Automatic claim renewal when the game source pin changes
  is no longer a background step: a maintenance card runs `kb renew` (a reduced capability,
  accepted with the queue-keeper's removal).
- The experiment lock (`xp create`, `upload`) lives in the bare canonical repository, so it
  holds across every researcher's clone on the host.
- A clone's checked-out `main` moves only when that profile writes canonical. Checks that
  must see what another profile published read the bare repository's `main` directly (path
  and content at one commit): the experiment stop check in `xp create` and the promotions
  behind the champion-conflict check.
- Every locked workspace write (`integrate`, `publish`) starts by resetting the clone's `main`
  to canonical `main` with `git reset --keep`. Commits an interrupted or rejected operation
  left unpushed are dropped and the operation redoes its work, so a retry never reports
  unpublished work as merged; unrelated uncommitted files survive.
- `workspace.publish` takes the content (path -> bytes, or None to delete), not paths of
  files already written, so a retry carries what it publishes; on any failure it writes the
  content back into the clone. The CLI (`workspace publish --path FILE`) reads the named
  files; it refuses a directory.
- `integrate` leaves the generated KB views (`kb/index.md`, `kb/_generated/`) out of the card
  commit, so a worker may run `kb index` to inspect them.
- `researcher_guard` has the two rules of §4, with rule 2 widened from "no push to `main`"
  to "no terminal `git push`": integrate does every push, and a ref check missed implicit
  pushes (`git push` from the clone). About 60 lines.
- The researcher's deny list blocks `research champion submit|bootstrap|accept`; the
  commands themselves stay in the shared CLI (one vendored package for both profiles).

- The handoff's `journal` is `ar://journal/J-...`; the design's `ar://J-...` does not resolve
  with `research records resolve`.

## Promotion (§9)

- **`promotion.lock`** (a file in the bare repository) is held by each promotion command
  from its checks to the recorded platform result: the orchestrator's chat turn and its
  supervisor both run promotion commands, and a `status` between a submit's `intent` and its
  send would otherwise abort a live submit.
- Before a resend after the coach's answer, the `waiting` field is cleared on `main`, so a
  send that dies after the platform accepted it is found by the submissions lookup.
- Stages are as designed; `status` advances at most one step per call (permit answer read,
  resend after a yes, submitted -> recorded -> published). An `intent` with no recorded
  outcome is `aborted` when the platform has no submission, never resent blindly.
- **Champion conflict** = the league shows a champion the loop did not record as its latest
  `published` promotion (a `submitted` promotion explains a gap). A host migrated from
  autoresearch starts in conflict until a human confirms the league's champion:
  **`champion accept`** (new) records it.
- The guards of the old submit path are kept: decision `promote`, a candidate binding, an
  eligible and current `stats evaluate` outcome (rows and design hashes), identity, no
  conflict, outcome champion == league champion, one promotion at a time, the candidate
  strategy on `main`.

## Hosting (§13)

The metta changes R-1 to R-6 are in [docs/hosting.md](../hosting.md), updated for this
implementation: one `RESEARCH_*` environment for every profile, each profile's
`scripts/bootstrap.py` as the prepare step (in any order: whichever profile runs first creates
and seeds the bare canonical repository, since both ship the game folders' seeds), the board
created once,
and the supervisor resumed once.
