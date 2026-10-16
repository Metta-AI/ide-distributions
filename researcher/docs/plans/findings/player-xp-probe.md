# Finding: Player-only XP probe (Plan 01 Task 0.1)

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

**Result: PLAYER-ONLY: PASS** — 2026-10-07, run by Claude for James.

## Setup
- CLIs in a throwaway venv: coworld 0.1.57, softmax-cli 0.26.38 (latest on PyPI). The globally installed uv tool coworld is stale (0.1.38.post1.dev750); the project pins 0.1.57.
- Isolated `HOME` holding only a copy of the user token, so the real `~/.softmax/credentials.yaml` was not changed. `coworld player use ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1` (Games Bond) minted a 24 h player session in the isolated file. `softmax status` showed `subject_type: player`, `subject_id: ply_f39295d0-…` before every step. No `--elevated`.

## Steps and outcomes (all as the Player)
| Step | Command | Outcome |
|---|---|---|
| Create | `coworld xp-request create xp-probe.json --title "player-only XP probe" --json` (target league `league_3c60897b-25cf-4b37-9d1a-8554c1198f28`, 10 × `{"player":{"random":true}}`, `num_episodes: 1`, `private: true`) | exit 0; `xreq_b0c4ff36-2d9e-4ddb-8224-9ba8fa0c2fd5`; `coworld_version: 2026.10.6.1`, `variant_id: competition` |
| Get | `coworld xp-request get xreq_b0c4ff36-2d9e-4ddb-8224-9ba8fa0c2fd5 --json` | exit 0 |
| List mine | `coworld xp-request list --mine --json` | exit 0; contains the request |
| Episodes | `coworld xp-request episodes xreq_b0c4ff36-2d9e-4ddb-8224-9ba8fa0c2fd5 --json` | exit 0; episode `completed`, evidence `sealed` after ~3 min |
| Download | `coworld xp-request download xreq_b0c4ff36-2d9e-4ddb-8224-9ba8fa0c2fd5 --include results --include replay -d artifacts` | exit 0; `results.json` (188 B) and a 2.8 MB replay |

## Notes for the design
- The request record carries `requester_user_id` = the **user** account even when a Player creates it. Ownership is user-level; read access works from the Player token. The seed-lab F6 finding (Player `get` returns 404) no longer holds.
- Upload and submit as a Player are documented as working (coworld-player-swap skill) and were not re-tested here.
- GotA league: `league_3c60897b-25cf-4b37-9d1a-8554c1198f28` ("Gods of the Arena"). League scoring: "Emmett's Glory" — winning heroes' lifetime XP per simulated minute; losses, draws, and timeouts score zero.
- Live coworld version on 2026-10-07: 2026.10.6.1 (newer than the 2026.10.5.1 in the design docs).
- results.json sample: `{"scores":[116,181,405,452,156,0,0,0,0,0],"ticks":28877,"seed":1853398679,"outcome":"RedTeam","banked_gold":[],"returned":[],"total_xp":[2327,3645,8137,9080,3148,5587,5062,3857,2701,4225]}`
- Cost: one 1-episode private XP request.
