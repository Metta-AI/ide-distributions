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

## Shared code and the ontology (§5.5, §5.6)

- The ontology is **embedded at the end of each `SOUL.md`** by `tools/sync_shared.py`, not
  copied to `<distribution>/ontology/`. Hermes loads `SOUL.md` in every session, cron
  included, and the orchestrator has no file tools to read a separate file.
- `tools/sync_shared.py` also copies `researcher/games/*/GAME.md` into `orchestrator/games/`:
  the shared package reads the league id from the game contract under the distribution root.
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
  `softmax upload-bootstrap`; the orchestrator submits that version with
  `promotion bootstrap --version V-...` (§9 said the orchestrator runs `bootstrap_champion()`,
  but the build and upload need a researcher's terminal).
- The KB's hub and staleness checks report findings (`kb hubs`, `kb renew` -> `reverify`)
  instead of writing card requests. Automatic claim renewal when the game source pin changes
  is no longer a background step: a maintenance card runs `kb renew` (a reduced capability,
  accepted with the queue-keeper's removal).
- The experiment lock (`xp create`, `upload`) lives in the bare canonical repository, so it
  holds across every researcher's clone on the host.
- `researcher_guard` has the two rules of §4; it is about 70 lines.
- The researcher's deny list blocks `research promotion submit|bootstrap|accept`; the
  commands themselves stay in the shared CLI (one vendored package for both profiles).

## Promotion (§9)

- Stages are as designed; `status` advances at most one step per call (permit answer read,
  resend after a yes, submitted -> recorded -> published). An `intent` with no recorded
  outcome is `aborted` when the platform has no submission, never resent blindly.
- **Champion conflict** = the league shows a champion the loop did not record as its latest
  `published` promotion (a `submitted` promotion explains a gap). A host migrated from
  autoresearch starts in conflict until a human confirms the league's champion:
  **`promotion accept`** (new) records it.
- The guards of the old submit path are kept: decision `promote`, a candidate binding, an
  eligible and current `stats evaluate` outcome (rows and design hashes), identity, no
  conflict, outcome champion == league champion, one promotion at a time, the candidate
  strategy on `main`.

## Hosting (§11)

Unchanged: R-1 to R-6 are metta changes (Rohit). The prepare step runs
`HERMES_HOME=<profile> python3 <profile>/scripts/bootstrap.py` once per profile. **Order:
researchers first.** The first researcher creates and seeds the bare canonical repository
from its `games/<game>/seed`; the orchestrator ships no seed, so its bootstrap only clones
(on an empty canonical it fails with "no seed for game").
