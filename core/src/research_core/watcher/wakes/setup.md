# Setup wake

Event `{event_kind}` for Coworld `{coworld_id}` (subject `{subject}`). The workspace uses this
Coworld for the first time, or switched to it before it was set up. This is a background
Session: never write into a user conversation; use `ui.notify` for news and `ui.ask` only for a
question the user alone can answer.

Payload:

```json
{payload_json}
```

Do this in order, with the setup skill, and end the run when it is done:

1. Read where setup stands: `research coworld setup-status` and `workspace.doctor`. The switch
   already registered the Coworld and fetched its source at the pinned commit.
2. Make the mixin usable at `code/mixin/`: copy it from the source repository, or build one
   with the mixin-from-scratch skill when the repository has none. Check its required parts:
   the five binding skills under `mixin/skills/` that the core skills call for game-specific
   steps, the `MIXIN.md` front matter (`coworld`, `commands` with `build` and `check` at least,
   `upload` and `evaluate`, metrics, the artifact, evidence types, toolchains), a lab
   `AGENTS.md`, `docs/`, a reference policy with a version log and `tools/build.sh`.
3. Import the repository's lesson notes as claims (`claim`), whatever format they use.
4. `workspace.capabilities {check: true}` marks the tool checks due; the watcher runs them on
   its next tick. Setup ends only when the starter policy builds and one upload (real or
   dry-run) passes; a failed check creates a maintenance Task on its own.
5. When `pending_request` is in the payload, carry it out now: `version.create` on `main`,
   `upload` that Version, then `submit` with `user_requested: true` citing
   `pending_request.cites`. The user asked for it before setup existed; nothing refuses it.
6. `ui.notify` what was set up, what failed (with the maintenance Task) and what the pending
   request did, with record references.
