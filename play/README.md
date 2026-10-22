# play

Play is the user's Softmax cogent on one coworld. It has two profiles that share one program
workspace and coordinate through native Hermes Kanban:

- `play` (this folder) takes the user's request and owns every platform act: the starter, the
  upload, private practice and the release. It edits no files; it has only its own tools and
  the `kanban` toolset.
- `builder` ([`builder/`](builder)) works the cards `play` creates and edits the program in the
  workspace. It has no Play tools, and its deny list blocks uploading, practice and submitting
  from the terminal.

There are no research records, experiments or champions. GotA only: its program is one BASIC
file (`program.bas`) that the game runs as is. Container coworlds are not supported yet.

## Tools

The `play` plugin (`plugins/play`, code in `src/play`) registers these tools with JSON schemas.
Each one runs `python -m play <tool>` in the profile venv on the shared `softmax_tool` client
(copied into `src/ide_research_core` by `tools/sync_shared.py`): the credential is installed in
the profile-local Softmax home before each CLI call, and every mutating call checks with
`whoami` that the credential is the Player `RESEARCH_PLAYER_ID`.

| Tool | What it does |
|---|---|
| `starter_fetch` | Downloads the coworld's starter player file (manifest `player` entry `baseline`, else the first file entry) to `starter/<id>.bas` and checks its sha256. |
| `program_upload` | `coworld upload-policy --file <workspace file> --name <name>-<player>`; returns `<name>:v<N>`. |
| `practice_request` | One private `POST /v2/experience-requests` in the game's league, idempotency key `practice:<version>:a<attempt>`. |
| `practice_status` | The request's state and episode counts; once completed, the results downloaded into `practice/<request id>/`. |
| `program_release` | `coworld submit <name>:v<N> --league L --player P`, once: an existing submission of that version is reported as released. |
| `fresh_start` | Only when the user asks to start over: empties the profile's `memories/MEMORY.md` and `memories/USER.md` (the lead's notes about itself and the user) so the next conversation is a first meeting. Needs `confirm: true`; touches nothing else. |

A coach refusal (403 `permit_required`, 402 `credit_denied`, `league_not_chosen`) is returned as
data, `{"outcome": "refused", "refusal": ..., "reason", "state"?, "ask"?}`, and is never retried.

`src/play/program.py` uses private members of the shared `SoftmaxClient` (`_league_coworld`,
`_cli`, `_require_player`) instead of adding public ones to `shared/`, which would change the IDE
profiles' copies; `tests/test_program.py` runs every one of those paths through the real client
with a fake CLI, so a rename in `shared/` fails `test (play)`.

## Install

From a clean checkout (see the repository README):

```bash
hermes profile install ~/ide-distributions/play --name play --yes
hermes profile install ~/ide-distributions/play/builder --name builder --yes
```

Both profiles' `.env` need `OPENROUTER_API_KEY` and the same absolute `PLAY_WORKSPACE`. The
`play` profile also needs `SOFTMAX_PLAYER_CREDENTIAL`, `RESEARCH_PLAYER_ID` and, off production,
`COGAMES_API_URL`. Then prepare it once and after each update:

```bash
HERMES_HOME=<play profile dir> PLAY_WORKSPACE=<dir> python3 <play profile dir>/scripts/bootstrap.py
```

The host's Kanban dispatcher must run `builder` cards (`kanban.dispatch_profiles` includes
`builder`).

## Develop and test

```bash
cd play
uv sync --frozen
uv run pytest -q             # model-free: tools, schemas, refusals, sync check
uv run pytest -q -m hermes   # with a real Hermes install: clean-export install and registration
```
