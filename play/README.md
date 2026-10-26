# play

Play is the user's Softmax cogent on one coworld. One profile, `play` (this folder), does the
whole loop itself: it changes the user's program, saves each change as a new version, practices
it, reports the results in plain words and releases a version only when the user says yes to it.
It has only its own tools and the `memory` toolset: no raw file, terminal or Kanban tools.

There are no research records, experiments or champions. GotA only: its program is one BASIC
file (`program.bas`) that the game runs as is. Other coworlds are built by the user's local coding agent, guided by the cogent.

`builder/` is the earlier builder profile. The hosted Play runtime still installs it
(metta `devops/hermes-fleet/applications/play/runtime_definition.py` lists both profiles, and
the image's prepare and check steps expect it), but the `play` profile no longer creates cards,
so it never runs. Removing it needs that metta change.

## Tools

The `play` plugin (`plugins/play`, code in `src/play`) registers these tools with JSON schemas.
Each one runs `python -m play <tool>` in the profile venv on the shared `softmax_tool` client
(copied into `src/ide_research_core` by `tools/sync_shared.py`): the credential is installed in
the profile-local Softmax home before each CLI call, and every mutating call checks with
`whoami` that the credential is the Player `RESEARCH_PLAYER_ID`.

| Tool | What it does |
|---|---|
| `who_we_are` | Whether the cogent has a name (`named`), its name (`cogent_name`, from its soul record; `null` until named), and its user's account name (`user_name`, `null` when none, from the public `GET /v2/cogents/<player>`). `softmax status` and `whoami` print the user's account name, not the cogent's. |
| `take_name` | Takes the name the user chose: writes it into the cogent's soul record (`c/profile/soul`), keeping the other fields. `who_we_are` then reports `named: true`. |
| `starter_fetch` | Downloads the coworld's starter player file (manifest `player` entry `baseline`, else the first file entry) to `starter/<id>.bas` and checks its sha256. |
| `program_show` | The program now (or the starter before the first change), its name, its last version and whether it changed since. |
| `program_edit` | Applies `{find, replace}` edits (each `find` exactly once; `edit_not_found` / `edit_ambiguous` otherwise) to the program now or the starter, then runs the same check and write as `program_change`. |
| `program_change` | Writes `program.bas` from a full new text after a static check (`src/play/basic.py`: size, closed blocks, the statements of the bassy compiler GotA runs, array/routine/global limits). A problem writes nothing and returns `program_invalid` with each problem by line; names the starter never uses come back as warnings. Returns the diff. |
| `program_versions` | Every uploaded version (what changed, practice summaries, released or not) and the diff of the program now against the last version or any version. |
| `program_upload` | `coworld upload-policy --file program.bas --name <name> --title <change>`; returns "towerless v2". An unchanged file returns its existing version. |
| `practice_request` | One private `POST /v2/experience-requests` of 5 games by default (at most 20), titled "Practice: towerless v2". |
| `practice_status` | The request's progress (optionally waiting up to 60 s); once over, each game's result from its `results.json` in plain words, a summary against the previous version's practice, what the results can and cannot measure, and a watch link per game. |
| `program_release` | `coworld submit <name>:v<N> --league L --player P` for a version the user names, once, and only with `user_said_yes: true`. |
| `task_update` | Keeps one task card per user request in the cogent's `tasks` records (shown in the Mind on /play): six steps from understanding the request to the release question, then an outcome. |
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

Practice, release and the starter use the league the coach chose for this cogent: the record
`c/state/league`, read as the Player through the public records API
(`GET <server>/observatory/v2/cogents/<player>/c/state/league`). With no choice, GotA's default
league (`games.py`) is used, and the platform answers with its own `league_not_chosen` refusal if
it requires a choice.

### Task cards

`task_update` writes `c/tasks/<key>` (`PUT <server>/observatory/v2/cogents/<player>/c/tasks/<key>`,
`bodies.Task`) with the player credential, which writes as the cogent. Cards have `origin:
"player"`, so the hosted bridge never forwards them as coach requests. The Mind shows an active
card as "v2 · step N of 6", a failed step as its snag, and a finished card's outcome under what
arrived. Step names are the records schema's fixed rungs (`reflect`, `implement`, `compile`,
`tryout`, `screen`, `submit`); each step's `detail` carries the plain words.

A coach refusal (403 `permit_required`, 402 `credit_denied`, `league_not_chosen`) is returned as
data, `{"outcome": "refused", "refusal": ..., "reason", "state"?, "ask"?}`, and is never retried.

`src/play/program.py` uses private members of the shared `SoftmaxClient` (`_league_coworld`,
`_cli`, `_require_player`, `_line_value`) instead of adding public ones to `shared/`, which would
change the IDE profiles' copies; `tests/test_program.py` runs every one of those paths through
the real client with a fake CLI, so a rename in `shared/` fails `test (play)`.

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
