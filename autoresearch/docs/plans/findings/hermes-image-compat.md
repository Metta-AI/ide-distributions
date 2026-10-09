# Finding: autoresearch on the pinned EC2 Hermes image

**Status:** the fixes on branch `jboggs/autoresearch-hermes-0216` address recommendations 3-5 (`kanban_schedule` block, `hermes_python()`, README `cron run`). One correction: `hermes cron run` already ran the job at once in v0.21.5; the README was wrong for both versions. Recommendations 1, 2, 6 and 7 are open.

**Date:** 2026-10-09. Run by Claude for James.

**Result: compatible, with one test-harness fix and one plugin gap.** The image runs
Hermes **v0.21.6** (commit `818c13be`), not our v0.21.5 (`f97608f`). The profile installs,
bootstraps, loads its plugin, creates its board, and runs its three cron jobs under that Hermes,
as uid 10000 on a read-only root. All model-free tests pass. Nothing in the parts we depend on
breaks. Two things need action: our `-m hermes` test harness assumes `hermes` is a Python script
(it is a bash launcher in the image), and v0.21.6 adds a worker tool, `kanban_schedule`, that
our plugin does not block.

Rohit asked us to test against the exact image rather than assume compatibility. This file is
that test. It is not a test of the real EC2 runtime: gVisor, the 2 GiB memory cap and Rohit's
`runtime.py` startup were not reproduced (see "Not covered").

## What the EC2 image is

Source: `~/coding/metta` at `6c01816c83` (pulled 2026-10-09, already current).

`packages/observatory-hosted-worker/Dockerfile`:

| Line | What it does |
|---|---|
| 1 | `FROM nousresearch/hermes-agent@sha256:9774f4f39a9bb8c2f68ce728ed5e99ddbad282163be56764afacf88ed952b784` |
| 3 | copies `uv`/`uvx` from `ghcr.io/astral-sh/uv@sha256:8f926a80…` to `/usr/local/bin` |
| 4 | venv `/opt/softmax-tools` with `coworld==0.1.58`, `softmax-cli==0.26.38` |
| 5-6 | clones `Metta-AI/rohit-optimizer` at `f6508fb1…` to `/opt/rohit-optimizer` |
| 7 | copies the bridge package to `/opt/softmax/observatory_hosted_worker` |
| 8 | `PYTHONPATH=/opt/softmax`, `PATH=/opt/softmax-tools/bin:/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin` |
| 9-10 | `USER 10000:10000`, `WORKDIR /opt/data` |
| 11-12 | entrypoint and healthcheck: `python -m observatory_hosted_worker.runtime [check]` |

How the container runs (`src/observatory_hosted_worker/fleet.py`, `docker run` at lines 167-204):
`--runtime=runsc` (gVisor, :174), `--user=10000:10000` (:179), `--read-only` (:180),
`--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, `--pids-limit 512`, CPUs and memory
from `HostConfig` (defaults 2 CPUs, 2048 MB, 10 GB disk: `fleet.py:33-35`), `/tmp` tmpfs 256 MiB
(:192) and `/run` tmpfs 32 MiB (:194), and a per-workspace ext4 loop disk bound at `/opt/data`
(:196; mounted and chowned to 10000 at :133-135).

Process environment (`src/observatory_hosted_worker/runtime.py:123-138`): `HOME`, `HERMES_HOME`
and `HERMES_REAL_HOME` are all `/opt/data`, `TERMINAL_HOME_MODE=real`,
`GATEWAY_MULTIPLEX_PROFILES=true`, API server on `127.0.0.1:8642`. One gateway runs as
`hermes -p default gateway run` (:146). Profiles live at `/opt/data/profiles/<name>`.

## Hermes in the image

| | Image | Ours |
|---|---|---|
| Version | `Hermes Agent v0.21.6 (2026.9.24) · upstream 818c13be` | v0.21.5 (2026.9.24) |
| Commit | `818c13be1dc4fd28987e1e881a9408224afd4535` (tags `v0.21.6`, `rc.4-v0.21.6`; 2026-10-08) | `f97608f` (tag `v2026.9.24`) |
| Python (Hermes venv) | 3.14.7 | 3.12.11 |
| Install method | `docker` (managed store, `/opt/hermes`) | `git` (editable uv tool) |
| OS | Debian 13 (trixie), git 2.47.3, no uv, no Nim | macOS host |

Evidence: `/opt/hermes/install-stamp.json` in the image (`"commit": "818c13be…", "branch":
"rc.4-v0.21.6", "dirty": false, "source": "ci"`). `/opt/hermes` has no `.git`, and the package
metadata says `0.0.0`, so the stamp is the source of truth. The display date "(2026.9.24)" is
stale; the commit date is 2026-10-08. The index `9774f4f3…` holds `linux/amd64` (`ffa4fbb4…`)
and `linux/arm64` (`7e36c109…`). Both carry the same commit. The tests below ran on arm64.

`818c13be` is a descendant of `f97608f`: 9,180 commits later (8,342 files changed). The base
image has a `hermes` user with uid 10000 and home `/opt/data`, and sets
`HERMES_HOME=/opt/data` and `HERMES_WRITE_SAFE_ROOT=/opt/data`.

## How it was tested

1. Built `ar-compat:ec2` from a copy of Dockerfile lines 1-4 and 8-10 (same base digest, same uv
   digest, same coworld/softmax-cli pins, same `PATH`/`PYTHONPATH`, uid 10000, workdir
   `/opt/data`). Left out the rohit-optimizer clone, the bridge copy and the entrypoint; none of
   them is on our path.
2. Ran it with the `fleet.py` flags we can reproduce on Docker Desktop: `--init --user
   10000:10000 --read-only --cap-drop=ALL --security-opt=no-new-privileges:true --pids-limit 512
   --tmpfs /tmp:rw,nosuid,nodev,size=256m,uid=10000,gid=10000 --tmpfs
   /run:rw,nosuid,nodev,size=32m,uid=10000,gid=10000`, a named volume at `/opt/data` (chowned to
   10000), `HOME=/opt/data`, and the default bridge network (uv needs PyPI and a CPython 3.12
   download). Memory was 4 GiB, not 2. No `runsc`.
3. Source: `git archive origin/main autoresearch` at `d525ac3`, mounted read-only and copied into
   a fresh git repo at `/opt/data/ide-mvp` (the integration tests `git archive HEAD`).
4. No real credentials anywhere. Profile `.env`: the fake player credential, `ply_fake`, a local
   bare remote, `AUTORESEARCH_GAME=fakegame`, `AUTORESEARCH_SOFTMAX=fake`,
   `OPENROUTER_API_KEY=sk-or-fake`, and `AUTORESEARCH_SKIP_OPENROUTER_CHECK=1` so preflight did
   not send the fake key to OpenRouter.

## Results

### Install, bootstrap, cron, CLI (README procedure): PASS

| Step | Command (in the container, `HERMES_HOME=/opt/data/hh`) | Result |
|---|---|---|
| Install | `hermes profile install /opt/data/ide-mvp/autoresearch --name autoresearch -y` | `✓ Installed 'autoresearch' v0.1.0`; 3 cron jobs arrive `paused` |
| Bootstrap | the README subshell: `HERMES_HOME="$PROFILE" python3 "$PROFILE/scripts/queue_keeper.py" --bootstrap-only` | exit 0 in 18 s; `uv sync` downloaded CPython 3.12.11 and built `local/venv`; `bin/autoresearch` written; `AUTORESEARCH_CLI` and `HERMES_WRITE_SAFE_ROOT` added to `.env`; remote `main` has `bootstrap: seed workspace from games/fakegame/seed` and `board epoch e1` |
| Board | `hermes kanban boards list` | `autoresearch` board present (`/opt/data/hh/kanban/boards/autoresearch`) |
| Plugin listed | `hermes -p autoresearch plugins list` | `autoresearch │ enabled │ 0.1.0 │ … │ user` |
| Cron | `cron resume` ×3, `cron run` ×3, `cron tick` | all three `last_status: ok`; output `{"admitted": 0, …, "paused": false, "pushed": true}` (queue-keeper), `{"evidence": 0, …}` (xp-poll), `{"blocked": [], "problems": [], …}` (health) |
| Cron under the multiplex gateway | `GATEWAY_MULTIPLEX_PROFILES=true hermes -p default gateway run` for 150 s | the gateway ran queue-keeper on schedule: `"admitted": 1, "paused": false` (it admitted the planning card `t_60242c43`, assignee `autoresearch`). So the passthrough credential reached the script under the real topology. The gateway was stopped before its dispatcher spawned a worker, so no model call happened. |
| CLI | `$AUTORESEARCH_CLI queue list`, `comms list` (with `HERMES_HOME=$PROFILE`) | `{"data": {"cards": []}, "ok": true}` and `{"data": {"cursor": 0, …}, "ok": true}` |

The plugin, loaded in-process by the image's Hermes (Python 3.14) through
`hermes_cli.plugins.discover_plugins` / `invoke_hook`, registered all five hooks
(`pre_tool_call`, `post_tool_call`, `pre_llm_call`, `on_session_end`, `kanban_task_completed`).
`pre_tool_call` blocked `coworld player use ply_x` and `kanban_create`, and allowed `ls`.

### Default test tier (unit, contract, protocol): PASS except Nim

```sh
uv sync --frozen --group dev
uv run --frozen pytest -q -p no:cacheprovider --basetemp=/opt/data/pt -rfE
```

`2180 passed, 28 skipped, 11 deselected, 22 errors in 416.97s`. All 22 errors are
`ValueError: Nim 2.2.10 is required` (GotA native gate, backend and prebuilt tests in
`tests/unit/gota/`). Skips: Nim missing (5), handoff engine not built (3), and
`test_distribution.py:112` "Hermes source checkout not available" (1), plus parametrized
repeats.

That skipped test (`test_deny_globs_are_anchored`) runs our `approvals.deny` globs through
Hermes's own matcher. Run against the image's source by pointing `$HOME/coding/tools/
hermes-agent-v2026.9.24` at `/opt/hermes`: `tests/unit/test_distribution.py` → `22 passed`.

With pytest's default base temp, the same run gave `92 failed, 879 passed, 484 errors`, all
from the environment: pytest filled the 256 MiB `/tmp` tmpfs, and Docker mounts tmpfs `noexec`
by default, so a git hook written under `/tmp` did not run
(`tests/protocol/test_promotion.py::test_record_intent_refuses_when_ledger_push_fails`). Run
tests in this image with `--basetemp` on `/opt/data`.

### `-m hermes` tier, model-free tests only: PASS after a harness fix

```sh
uv run --frozen pytest -p no:cacheprovider --basetemp=/opt/data/pt5 -m hermes -rfEs \
  tests/integration/test_kanban_queue_hermes.py tests/integration/test_config_migrations.py \
  tests/integration/test_install.py tests/integration/test_plugin_hermes.py
```

`OPENROUTER_API_KEY` unset. First run: `4 failed, 2 passed, 3 skipped`. The four failures
(`test_config_migrations.py` ×2, `test_install.py::test_launcher_bootstraps`,
`::test_update_preserves`) are one harness bug: `tests/integration/conftest.py`
`hermes_python()` reads the shebang of `which hermes` and gets `/usr/bin/env bash`, because the
image's `/opt/hermes/.venv/bin/hermes` is a bash launcher, then tries to execute that string
(`FileNotFoundError: '/usr/bin/env bash'`). With `hermes_python()` returning
`/opt/hermes/.venv/bin/python` (patched in the container copy only): `6 passed, 3 skipped`.
The skips are the three model tests (`test_install.py:166`, `:175`, `test_plugin_hermes.py:98`).

Not run, because they call a model and spend credit: `test_protocol_slice.py`,
`test_fake_cycle.py`, the worker tests in `test_install.py`, `test_plugin_hermes.py`.

## Differences that matter

The CLI surface we call is unchanged. `--help` option sets are identical between v0.21.5 and
the image for `kanban create/list/show/edit/complete/block/unblock/assign/reclaim/claim/runs/
init/dispatch`, `kanban boards create`, `cron create`, `profile install/update` and
`config set`. Also unchanged at `818c13be` (checked in source):

- Plugin hook names, including `kanban_task_completed` (`hermes_cli/plugins.py:162`, fired at
  `hermes_cli/kanban_db.py:2761`).
- `terminal.timeout` default 180 (`hermes_cli/config_defaults.py:314`), foreground cap 600
  (`tools/terminal_tool.py:76`), and `process_manage` `wait` clamped to `TERMINAL_TIMEOUT`
  (`tools/process_registry.py:2082-2092`).
- Every config key we pin still exists in `config_defaults.py`, and `kanban.max_in_progress`
  still defaults to `None`.
- `HERMES_KANBAN_TASK`/`HERMES_KANBAN_RUN_ID` scrubbing from worker subprocesses
  (`agent/delegation_context.py` only gained `explicit_board_intent_is_pinned`).

Changed:

1. **New worker tool `kanban_schedule`** (`tools/kanban_tools.py:852-870`, schema
   `tools/kanban_tools_schemas.py:205`). It ends the worker's run and parks the card in
   `scheduled` until something unblocks it. Our plugin blocks only `kanban_create` and
   `kanban_link` as tools (`src/autoresearch/plugin/policy.py:204`), so a worker can call it.
   `KanbanQueue` maps `scheduled` to `pending` (`queue/kanban_queue.py:22`), and nothing in our
   runtime unblocks a scheduled card, so the card would sit there with its work unmerged. A model
   has to choose this tool, but our skills never mention it. **Recommended fix: block
   `kanban_schedule` in `tool_policy`, with the same message as `kanban_complete` before
   `card finish`.** The shell form `hermes kanban schedule` is already refused by the
   read-only allowlist.
2. **`hermes` is a bash launcher in the Docker install.** It runs
   `/opt/hermes/.venv/bin/python -P -c …` with `PYTHONPATH=/opt/hermes:<site-packages>`. Cron
   scripts do not inherit that `PYTHONPATH` (`cron/scheduler_script.py:141-160`: the managed
   store runs `.py` scripts on its dependency venv with no `PYTHONPATH`). Our launcher then
   execs `local/venv/bin/python` (3.12). That worked in every cron run above. Only our test
   harness is affected (see the `-m hermes` tier).
3. **`hermes cron run` now runs the job at once** ("Ran now: succeeded."). The README says
   `cron run` only marks the job for the next tick. That is now wrong for v0.21.6.
4. **Passthrough refuses Hermes-managed credentials** (`tools/env_passthrough.py`). None of our
   ten names is affected: `is_env_passthrough()` is true for all of them under the image.
5. `hermes profile install` now also creates empty `home/`, `workspace/`, `plans/`, `skins/`,
   `sessions/`, `logs/`, `memories/` in the profile. Bootstrap uses the empty `workspace/` as the
   canonical clone without trouble.

## Image and uid effects

- **One uid for everything.** Hermes, the bridge and our runtime all run as uid 10000.
  `AUTORESEARCH_INBOX_WRITERS` adopts inbox files by owner, so in this sandbox it cannot tell
  the bridge from the agent. Inbound messages there are effectively unauthenticated. This needs
  a different trust mechanism if the bridge is to write our inbox on EC2.
- **Read-only root: no problem.** Everything we write is under `/opt/data` (profile, venv, uv
  cache at `/opt/data/.cache/uv`, uv's CPython at `/opt/data/.local/share/uv/python`) or `/tmp`.
- **`/tmp` is 256 MiB and `noexec`.** Our runtime keeps only small data files there
  (`io/gitops.py:67`, `queue/kanban_queue.py:248`, `softmax_tool/cli.py:659`). Nothing is run
  from `/tmp`, so production is fine. Tests need `--basetemp` (above).
- **No Nim and no uv in the base image.** The EC2 Dockerfile adds uv. Nim is not added, so GotA
  native build, gate and replay tools cannot run in this image as built. Fakegame needs neither.
- **First bootstrap needs network**: PyPI and the python-build-standalone CPython 3.12 download.
  The EC2 sandbox has `--network=bridge`, so this should work.
- **Disk per workspace**: `local/venv` 472 MB, CPython 3.12 97 MB, uv cache 27 MB, profile
  17 MB, about 0.6 GB of the default 10 GB disk.
- **`HERMES_WRITE_SAFE_ROOT`**: the image sets it to `/opt/data`, and bootstrap writes the
  canonical workspace into the profile `.env`. Hermes loads the profile `.env` with override
  (`hermes_cli/env_loader.py:461`), so workers should get ours. UNVERIFIED without a worker run.

## Rohit's runtime and our profile

`runtime.py` installs only `/opt/rohit-optimizer` as profile `optimizer`
(`runtime.py:21,38-66`), so our profile is not installed today. If that step is reused for our
profile, two of its `config set` calls would break us. `terminal.env_passthrough` is set to
three names (`runtime.py:61`), which replaces our ten. Under the multiplex gateway, cron scripts
would then lose `SOFTMAX_PLAYER_CREDENTIAL`, `AUTORESEARCH_WORKSPACE_REMOTE` and the rest.
`terminal.cwd` would point at `/opt/data/workspace`. Our profile needs its own install path, or
those values must be merged into our list, not replace it.

## Not covered

- gVisor (`runsc`). Docker Desktop on macOS has no runsc. Syscall-level differences (git, uv
  hardlinks, sqlite locking under gVisor) are untested.
- The 2 GiB memory and 2 CPU caps. We ran with 4 GiB. A gateway plus two concurrent workers in
  2 GiB is untested.
- Any model turn: worker hook firing, briefing injection, `kanban_complete`/`kanban_block` from a
  real worker.
- amd64. Only the arm64 image was run; the amd64 image was checked for version and commit only.
- Rohit's `runtime.py` startup, the bridge, and the API server.

## Recommendations

1. **Pin.** Treat `nousresearch/hermes-agent@sha256:9774f4f3…` = Hermes v0.21.6 (`818c13be`) as
   the EC2 target, and record it beside v0.21.5 in the README prerequisites. Do not pin the
   floating tag. Ask Rohit to tell us before he moves the digest so we can re-run this check.
2. **Move local Hermes to v0.21.6** (tag `v0.21.6`), so local tests and EC2 run the same
   commit. The v0.21.5 → v0.21.6 changes we depend on are compatible.
3. **Block `kanban_schedule`** in the plugin's `tool_policy` and add a unit test.
4. **Fix `hermes_python()`** in `tests/integration/conftest.py` (and its copy in
   `test_protocol_slice.py:89`). Ask Hermes for its interpreter, or read `HERMES_PYTHON`, which
   the image sets to `/opt/hermes/.venv/bin/python`. Do not read the launcher's shebang.
5. **Update the README** for `cron run` (it runs the job now in v0.21.6).
6. **Before EC2 integration:** agree the profile install path with Rohit so our
   `terminal.env_passthrough` is not overwritten, and decide inbox authentication for the
   single-uid sandbox.
7. **Next check, with credit:** one `test_install.py` worker run and `test_fake_cycle.py` inside
   this image under a 2 GiB cap. On a Linux host with runsc, add `--runtime=runsc`.

## Housekeeping

- Disk used: the image pulls and the build added about 9.8 GB of images (arm64 base 8.98 GB
  shared with `ar-compat:ec2` 4.9 GB, plus the amd64 base 4.51 GB), and the `/opt/data` volume
  held 2.8 GB. All of it was removed afterwards: container `ar-compat`, volume `ar-compat-data`,
  images `ar-compat:ec2` and both `nousresearch/hermes-agent` digests. The BuildKit cache
  for that build (the uv layer and the softmax-tools venv layer) is left in place, because
  `docker builder prune` would also clear other projects' caches.
- `~/coding/tools/hermes-agent-v2026.9.24` was un-shallowed and fetched tags
  (`git fetch --unshallow`, `--tags`) to diff against `818c13be`. Its working tree and `HEAD`
  (`f97608f`) are unchanged.
- `~/coding/metta` was only pulled (already current at `6c01816c83`).
