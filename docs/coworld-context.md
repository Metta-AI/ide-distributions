# Coworld repository context

IDEa sends a fresh repository selection for every admitted turn. It resolves the
conversation's saved league, current Coworld, and manifest `game.runnable.source_url`.
Hermes's orchestrator middleware loads the selected source before the model request.
The same operation is available through the native `coworld_repository` tool.

## Repository convention

Set `source_url` to `https://github.com/OWNER/REPO/tree/FULL_COMMIT/path/to/game`.
Place `COWORLD.md` in that directory. A `blob` URL uses its parent directory.
Metta's Coworld bundle command pins supported GitHub source URLs during publication.
Floating branches are reported as `unpinned_source`; they are never silently followed.

Keep `COWORLD.md` within 8,000 UTF-8 bytes. Include:

- The game and player runtime this guide describes.
- A short source map and policy-development workflow.
- Relative links to relevant docs and standard `skills/NAME/SKILL.md` files.
- How to reproduce a complete baseline, compare a candidate, and inspect failures.
- Historical or alternative runtimes that should not be confused with this one.

Only the entry guide is automatically included. Read linked docs and skills as needed
using native file tools at the returned checkout. Skills stay local to this repository;
loading a game does not install its skills into every conversation's global catalog.
Keep a skill's description and trigger in the entry guide so the model can choose it.

Pudge Wars uses a root `COWORLD.md` and `skills/pudge-wars-policy/SKILL.md` for its
BASIC policy loop. Its older Python/container research remains explicitly historical.

## Selection, updates, and evidence

The desktop's current system context selects automatic reads. User messages, tool
output, and conversation history cannot inject another automatic repository selection.
Repository text is included as a user-level reference, before the current user message.
It does not change authorization, tools, profile secrets, or research defaults.

Checkouts are cached by repository and full commit. Concurrent conversations reuse a
completed checkout under a file lock. Guidance comes from committed Git objects, so
local candidate edits cannot become release guidance. Nothing resets or deletes edits.
Develop candidates in separate worktrees; cite the engine commit and policy hash.

Changing Coworlds applies on the next admitted turn. A new source commit gets another
checkout. Returning to an earlier commit reuses its checkout. In-flight runs retain
their original selection. No `RESEARCH_GAME`, global skill list, or canonical research
workspace is retargeted. Worker cards must still name their game and source revision.

Results include the selection, checkout path/commit, guide path/hash, and a status:
`ready`, `missing_guide`, `no_source`, or `unpinned_source`. Transport/validation errors
use the existing tool error envelope. Missing guidance is not proof that file tools,
submitted policy downloads, or installed build/evaluation bindings are unavailable.
Use `game_support` to inspect those bindings separately.

Public repositories clone without credentials. Private repositories use the owner's
consented Softmax GitHub broker. No provider token enters the profile or tool result.
Fetching the selected SHA uses the same transport as the clone.

## Local iteration

Mount local repositories into the Hermes container under
`COWORLD_REPOSITORIES_DIR/OWNER/REPO` and set `COWORLD_REPOSITORIES_DIR` in the
orchestrator profile's `.env`. Each mirror must have its matching HTTPS GitHub origin.
The local mode reads only those mirrors and fails visibly if a mirror/commit is absent.
It does not fall back to a hosted revision while testing local changes.

Commit the guide/source change locally, then point the local Coworld manifest's
`source_url` at that commit. The next turn loads it without rebuilding the Hermes image.
Uncommitted guide edits are deliberately excluded from release context. The native
`coworld_repository` tool can also inspect an explicit selection during development.

Existing published releases will report `missing_guide` until a Coworld containing
`COWORLD.md` is published and selected. Merging documentation alone does not update a
live release. IDEa and the orchestrator distribution must both include this feature.
