# play

Play is the user's Softmax cogent on one coworld. One profile, `play` (this folder), does the
whole loop itself: it changes the user's program, saves each change as a new version, practices
it, reports the results in plain words and releases a version only when the user says yes to it.
It has only its own tools and the `memory` toolset: no raw file, terminal or Kanban tools.

There are no research records, experiments or champions. GotA and Generals programs are
BASIC files that their games execute directly.
The selected league's published manifest supplies the starter, rules, and player protocol.
Generals uses its own source limits, variant roster, and native score summaries; it needs
no player container. Unsupported coworlds still use a local coding agent.
Existing GoTA files stay in the workspace root; Generals files and journals live in
`generals/`. `game.json` records which supported game the source tools currently edit.
Fetch the selected league's starter before switching games or requesting practice.

`builder/` is the earlier builder profile. The hosted Play runtime still installs it
(metta `devops/hermes-fleet/applications/play/runtime_definition.py` lists both profiles, and
the image's prepare and check steps expect it), but the `play` profile no longer creates cards,
so it never runs. Removing it needs that metta change.

## Tools

The `play` plugin (`plugins/play`, code in `src/play`) registers these tools with JSON schemas.
Each one runs `python -m play <tool>` in the profile venv on the `softmax_tool` client in
`src/ide_research_core` (play's own vendored copy of the former shared package): the credential is installed in
the profile-local Softmax home before each CLI call, and every mutating call checks with
`whoami` that the credential is the Player `RESEARCH_PLAYER_ID`.

| Tool | What it does |
|---|---|
| `starter_fetch` | Selects the game's workspace, downloads `baseline` (GotA) or `basic` (Generals) to `starter/<id>.bas`, checks its sha256, and returns the published rules and protocol. |
| `program_show` | The program now (or the starter before the first change), its name, its last version and whether it changed since. |
| `program_edit` | Applies `{find, replace}` edits (each `find` exactly once; `edit_not_found` / `edit_ambiguous` otherwise) to the program now or the starter, then runs the same check and write as `program_change`. |
| `program_change` | Writes `program.bas` from a full new text after a static check (`src/play/basic.py`: size, closed blocks, bassy statements, and the selected game's countable limits). A problem writes nothing and returns `program_invalid` with each problem by line; names the starter never uses come back as warnings. Returns the diff. |
| `league_status` | Read-only, free: the live league's newest round, each division's top scores, the user's own rank and score, what changed since the last look (a small `league_snapshot.json` in the workspace) and their latest league game. |
| `program_versions` | Every uploaded version (what changed, practice summaries, released or not) and the diff of the program now against the last version or any version. |
| `program_upload` | `coworld upload-policy --file program.bas --name <name> --title <change>`; returns "towerless v2". An unchanged file returns its existing version. |
| `practice_request` | One private `POST /v2/experience-requests` of 5 games by default (at most 20), titled "Practice: towerless v2". |
| `practice_status` | The request's progress (optionally waiting up to 60 s); once over, each game's result from its `results.json` in plain words, a summary against the previous version's practice, what the results can and cannot measure, and a watch link per game. |
| `program_release` | `coworld submit <name>:v<N> --league L --player P` for a version the user names, once, and only with `user_said_yes: true`. |
| `task_update` | Keeps one task card per user request in the existing workspace journal: six steps from understanding the request to the release question, then an outcome. |
| `fresh_start` | Only when the user asks to start over: empties the profile's `memories/MEMORY.md` and `memories/USER.md` so the next conversation is a first meeting. Needs `confirm: true`; touches nothing else. |

Tools name programs as the user does: a name and a version number. The platform's policy name
is the user's name, or, when someone else already has that name, the name plus the first 8
characters of the player id (the platform's own rule); it is returned as `platform_name` and is
never shown to the user. Names are lowercase words and hyphens, at most 55 characters so the
suffixed form fits the platform's 64.

### The program workspace

`$PLAY_WORKSPACE` holds `program.bas`, `starter/`, `journal.json` (the program's name, every
version with what it changed, its practices and their summaries, its release, and the current
task card), `versions/<platform name>/v<N>.bas` (a copy of each uploaded file, for diffs) and
`practice/<request>/` (the downloaded results and replays).

### The league

`starter_fetch`, `practice_request`, and `program_release` require the `league` argument.
The league is always explicit for platform actions. `game.json` records the fetched contract
for source editing; it never supplies a default league for practice or release.

### Task cards

`task_update` updates the existing current card in `journal.json` without publishing it to the platform.

An exhausted account-credit balance is returned as a `credit_denied` refusal.
Practice and release each use the league explicitly supplied for that operation.

`src/play/program.py` uses private members of the vendored `SoftmaxClient` (`_league_coworld`,
`_cli`, `_require_player`, `_line_value`); `tests/test_program.py` runs every one of those paths
through the real client with a fake CLI, so a rename in `src/ide_research_core` fails `test (play)`.

## Install

From a clean checkout (see the repository README):

```bash
hermes profile install ~/ide-distributions/play --name play --yes
```

The profile's `.env` needs `OPENROUTER_API_KEY`, `PLAY_WORKSPACE`, `SOFTMAX_PLAYER_CREDENTIAL`,
`RESEARCH_PLAYER_ID` and, off production, `COGAMES_API_URL`. Then prepare it once and after each
update:

```bash
HERMES_HOME=<play profile dir> PLAY_WORKSPACE=<dir> python3 <play profile dir>/scripts/bootstrap.py
```

## Develop and test

```bash
cd play
uv sync --frozen
uv run pytest -q             # model-free: tools, schemas, refusals, sync check
uv run pytest -q -m hermes   # with a real Hermes install: clean-export install and registration
```

The opt-in system check exercises writing, file upload and completed practice against real local
services. It catches rejected file players, wrong rosters, and results parsed as the wrong game.
Prepare an isolated local Generals league and test Player as documented in
`tests/system/test_generals_flow.py`, then run:

```bash
GENERALS_PLAY_SYSTEM=1 uv run pytest -q tests/system/test_generals_flow.py
```

The hosted game image requires an AMD64 host with AVX; an ARM Mac's Docker emulator
cannot run its JAX runtime. A native game episode alone does not validate hosted practice.
