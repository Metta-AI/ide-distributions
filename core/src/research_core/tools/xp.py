"""The ``xp`` tool (design C.2, B.8, B.13; amendments A3.2, A3.9, A4.1, A4.2, A4.4).

``request`` creates one experience request per (seed, arm) for a running Experiment and
records each as a run the moment the platform accepts it. ``status`` refreshes the runs,
downloads the results of completed requests, runs the mixin's ``evaluate`` command on them
and stores the per-seat rows on the run, in the results-row contract the ``experiment`` tool
closes on (``tools/experiment.py``: ``results.rows`` objects with ``episode_id``,
``game_version``, ``policy_version`` as the Version's record id, ``seed``, ``seats``,
``opponents``, ``metrics``, ``status``, ``reason``).

Rules the tool keeps (A4.4): it never puts a Task on a wait itself; a Researcher calls
``task.wait {kind: xp, ref: [...]}`` and ends its run, or ``task.wait {kind: credits}`` after a
credits refusal. There is no local credits pre-check: the platform's refusal (402) is the
refusal, reported with the balance, the held amount and the next refill. The
``xp.ask_above_requests_per_experiment`` setting never refuses: above it the Orchestrator gets
a warning and proceeds, a Researcher gets ``ask_orchestrator`` and a ``task.ask`` suggestion.

Every create is an intent (B.13): ``intents.begin(kind="xp", key="X-<n>:<seed>:<arm>")``
before the call; a pending intent found on a later call is reconciled by listing the Player's
requests by title (``platform/xp.py``) before anything is sent again. A platform error that
outlives the retries becomes a refusal through ``platform/wait.py`` (a task Session is put on
``waiting_on: platform``, A3.9).

``evaluate`` runs through ``mixin.run_command`` with the A3.2 budget: a command that outlives
it keeps running and the result carries ``background.resume``; ``status {resume: <token>}``
finishes the work when the process is done. A failed or non-conforming ``evaluate`` marks the
run ``invalid`` and reports the ``evaluate`` capability (A4.1). The evaluate command runs in
the code repository root with ``XP_RESULTS`` naming the folder of downloaded results.

Stdlib at import time (the plugin imports the catalog); PyYAML loads inside ``mixin.load`` in
the workspace environment where this tool runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any

from research_core import mixin as mixin_module
from research_core.platform import credits as credits_module
from research_core.platform import wait
from research_core.platform import xp as xp_module
from research_core.platform.errors import CreditsRefused, PlatformError
from research_core.platform.protocol import Credits, Platform, XpRequest, with_retries
from research_core.stats.ingest import rows_from_evaluate_output
from research_core.store import capabilities, experiments, intents, settings, versions
from research_core.store._common import parse_display_id
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolContext, ToolError, ToolSpec, tool

BOTH = frozenset({"orchestrator", "researcher"})
ARMS = ("candidate", "baseline")
ASK_ABOVE_KEY = "xp.ask_above_requests_per_experiment"
DEFAULT_EPISODES = 1
MAX_DEFAULT_SEEDS = 20
TERMINAL_STATES = ("completed", "failed", "cancelled")
RUN_KIND = "xp"
# A3.2: the budget an evaluate command gets before it is left running in the background.
EVALUATE_BUDGET_S = mixin_module.DEFAULT_BUDGET_S
RESULTS_ENV = "XP_RESULTS"
CREATE_ROUTE = "POST /v2/experience-requests"

EXPERIMENT = a.string("The Experiment, such as X-5.")


# --- request ---------------------------------------------------------------------------------


def handle_request(call: ToolCall) -> dict:
    ctx = call.ctx
    store, actor = ctx.store, ctx.actor
    record = experiments.read(
        store, a.record_number(call.get("experiment"), "experiment", "experiment")
    )
    n = parse_display_id(record["id"])[1]
    plan = _plan(call, record)
    platform = ctx.platform()
    warnings = list(plan["warnings"])

    existing_runs = [run for run in record["runs"] if run["kind"] == RUN_KIND]
    done = {(run["seed"], run["arm"]) for run in existing_runs}
    new_pairs = [pair for pair in plan["pairs"] if pair not in done]
    counts = {
        "existing": len(existing_runs),
        "new": len(new_pairs),
        "total": len(existing_runs) + len(new_pairs),
        "limit": settings.get((ctx.wstore, store), ASK_ABOVE_KEY),
    }
    above = counts["limit"] is not None and counts["total"] > counts["limit"]
    if above and ctx.role == "researcher":
        return _ask_orchestrator(record, counts)
    if above:
        warnings.append(
            f"{record['id']} will have {counts['total']} XP requests, above the"
            f" {ASK_ABOVE_KEY} setting of {counts['limit']}; proceeding because the Orchestrator"
            " decides (a Researcher would ask first)"
        )

    coworld_id = ctx.coworld().coworld_id
    created: list[str] = []
    reconciled: list[str] = []
    for seed, arm in plan["pairs"]:
        title = xp_module.title_for(n, seed, arm)
        intent = intents.begin(
            store, actor, kind="xp", key=f"X-{n}:{seed}:{arm}", subject=record["id"]
        )
        intent_n = parse_display_id(intent["id"])[1]
        run = _run_for(existing_runs, seed, arm)
        if run is None and intent["existing"] and intent["state"] == "pending":
            request = _reconcile(ctx, platform, title)
            if request is not None:
                run = _record_run(ctx, record, request, seed=seed, arm=arm, plan=plan)
                reconciled.append(request.id)
        if run is None:
            body = xp_module.request_body(
                coworld_id=coworld_id,
                policy_version_id=plan["policy_version_ids"][arm],
                seed=seed,
                episodes=plan["episodes"],
                title=title,
                league_id=plan["league"],
                opponents=plan["opponents"],
            )
            try:
                request = with_retries(partial(platform.xp_create, body), sleep=wait.sleep)
            except CreditsRefused as error:
                intents.finish(store, actor, intent_n, state="failed", result=error.to_dict())
                return _credits_refused(ctx, record, error, created, reconciled, plan)
            except PlatformError as error:
                refusal = wait.tool_error(ctx, CREATE_ROUTE, error)
                refusal.context.update(created=created, reconciled=reconciled)
                raise refusal from error
            run = _record_run(ctx, record, request, seed=seed, arm=arm, plan=plan)
            created.append(request.id)
        if intent["state"] == "pending" or not intent["existing"]:
            intents.finish(
                store,
                actor,
                intent_n,
                state="done",
                result={"request_id": run["platform_request_id"], "title": title},
            )
    runs = _xp_runs(store, n)
    ids = [run["platform_request_id"] for run in runs if run["state"] not in TERMINAL_STATES]
    credits = _read_credits(platform)  # after the creates: the balance that is left
    return {
        "experiment": record["id"],
        "runs": runs,
        "created": created,
        "reconciled": reconciled,
        "requests": [run["platform_request_id"] for run in runs],
        "credits": credits_module.describe(credits),
        "estimate": _estimate(ctx, credits, plan["episodes"] * len(new_pairs)),
        "warnings": warnings,
        "next": _next_after_request(ctx, record, ids),
    }


def _plan(call: ToolCall, record: dict) -> dict:
    """Everything a request needs, checked at once: the Versions, their uploads, seeds, arms."""
    ctx = call.ctx
    problems: list[str] = []
    xid = record["id"]
    if record["status"] == "proposed":
        problems.append(f"{xid} is proposed; start the experiment first (experiment.start)")
    elif record["status"] != "running":
        problems.append(f"{xid} is {record['status']}; runs are added only while it is running")
    methods = record["methods"]
    arms = call.get("arms") or list(ARMS)
    for arm in arms:
        if arm not in ARMS:
            problems.append(f"arms must be candidate and/or baseline, not {arm!r}")
    refs = {
        "baseline": _version_ref(
            call.get("baseline"), methods.get("baseline"), "baseline", xid, record, problems
        ),
        "candidate": _version_ref(
            call.get("candidate"), methods.get("candidate"), "candidate", xid, record, problems
        ),
    }
    policy_version_ids: dict[str, str] = {}
    for arm in ARMS:
        ref = refs[arm]
        if ref is None or arm not in arms:
            continue
        uploaded = versions.read(ctx.store, ref).get("uploaded")
        if not uploaded or not uploaded.get("policy_version_id"):
            problems.append(f"{ref} ({arm}) is not uploaded; run upload {{version: {ref}}} first")
        else:
            policy_version_ids[arm] = uploaded["policy_version_id"]
    n_min = (methods.get("decision_rule") or {}).get("n_min") or MAX_DEFAULT_SEEDS
    seeds = _seeds(call.get("seeds"), n_min, problems)
    episodes = call.get("episodes")
    if episodes is None:
        episodes = DEFAULT_EPISODES
    elif not isinstance(episodes, int) or isinstance(episodes, bool) or episodes < 1:
        problems.append("episodes must be a positive integer")
    opponents = call.get("opponents")
    if opponents is not None and not all(isinstance(o, str) and o for o in opponents):
        problems.append("opponents must be a list of policy version ids")
    if problems:
        raise ToolError(problems, experiment=xid)
    warnings = []
    if len(seeds) < n_min:
        warnings.append(
            f"{len(seeds)} seeds is below the decision rule's n_min of {n_min}; the verdict will"
            " be inconclusive unless more seeds are requested later"
        )
    return {
        "pairs": [(seed, arm) for seed in seeds for arm in arms],
        "arms": arms,
        "seeds": seeds,
        "episodes": episodes,
        "refs": refs,
        "policy_version_ids": policy_version_ids,
        "league": call.get("league"),
        "opponents": opponents,
        "warnings": warnings,
    }


def _version_ref(
    given: Any, locked: str | None, arm: str, xid: str, record: dict, problems: list[str]
) -> str | None:
    """The ``V-<n>`` of an arm: the argument, else the locked design, else the built Version.

    An argument that names another Version than the locked design is refused: the design is
    the Experiment, and a run of some other Version would be recorded under its name.
    """
    if given is not None:
        try:
            ref = f"V-{a.record_number(given, 'version', arm)}"
        except ToolError as error:
            problems.extend(error.problems)
            return None
        if locked and ref != locked:
            problems.append(
                f"{arm} {ref} differs from {xid}'s locked methods.{arm} {locked}; abandon {xid}"
                " and create a new Experiment to test another Version"
            )
            return None
        return ref
    if locked:
        return locked
    built = [v["id"] for v in record["versions_tested"] if v["role"] == "built"]
    if arm == "candidate" and len(built) == 1:
        return built[0]
    problems.append(
        f"{arm} is required: {xid}'s methods name no {arm}"
        + (f" and {len(built)} Versions were built for it" if arm == "candidate" else "")
    )
    return None


def _seeds(given: Any, n_min: int, problems: list[str]) -> list[int]:
    if given is None:
        return list(range(1, min(n_min, MAX_DEFAULT_SEEDS) + 1))
    if not given or not all(isinstance(s, int) and not isinstance(s, bool) for s in given):
        problems.append("seeds must be a non-empty list of integers")
        return []
    if len(set(given)) != len(given):
        problems.append("seeds must not repeat")
    return list(given)


def _read_credits(platform: Platform) -> Credits | None:
    """The balance for the result; a platform that cannot answer does not block the request."""
    try:
        return credits_module.read(platform)
    except PlatformError:
        return None


def _estimate(ctx: ToolContext, credits: Credits | None, episodes: int) -> dict:
    cost_per_episode = None
    try:
        cost_per_episode = mixin_module.load(ctx.coworld().mixin).cost_per_episode
    except mixin_module.MixinError:
        pass
    return {
        "episodes": episodes,
        "credits": credits_module.estimate(
            episodes, credits=credits, cost_per_episode=cost_per_episode
        ),
    }


def _reconcile(ctx: ToolContext, platform: Platform, title: str) -> XpRequest | None:
    """A pending intent: find the request by title before sending anything again (B.13)."""
    return wait.call_platform(
        ctx, "GET /v2/experience-requests", lambda: xp_module.find_by_title(platform, title)
    )


def _record_run(
    ctx: ToolContext, record: dict, request: XpRequest, *, seed: int, arm: str, plan: dict
) -> dict:
    """``add_run`` at once, then the state and the Version the run plays (``status`` needs both)."""
    store, actor = ctx.store, ctx.actor
    run = experiments.add_run(
        store,
        actor,
        record["id"],
        kind=RUN_KIND,
        platform_request_id=request.id,
        arm=arm,
        seed=seed,
        episodes_total=request.episode_count or plan["episodes"],
    )
    return experiments.update_run(
        store,
        actor,
        run["id"],
        state=request.status,
        episodes_completed=request.completed_count,
        results={
            "version": plan["refs"][arm],
            "policy_version_id": plan["policy_version_ids"][arm],
            "title": request.title,
        },
    )


def _ask_orchestrator(record: dict, counts: dict) -> dict:
    task = record.get("task_id")
    question = (
        f"{record['id']} would have {counts['total']} XP requests ({counts['new']} new), above"
        f" the {ASK_ABOVE_KEY} setting of {counts['limit']}. May I request them?"
    )
    return {
        "ok": False,
        "error": (
            f"{record['id']} would have {counts['total']} XP requests, above the"
            f" {ASK_ABOVE_KEY} setting of {counts['limit']}; nothing was created: ask the"
            " Orchestrator first"
        ),
        "ask_orchestrator": True,
        "counts": counts,
        "suggest": "task.ask {task: "
        + (f"T-{task}" if task else "<your Task>")
        + f", question: {question!r}}}",
    }


def _credits_refused(
    ctx: ToolContext,
    record: dict,
    error: CreditsRefused,
    created: list[str],
    reconciled: list[str],
    plan: dict,
) -> dict:
    n = parse_display_id(record["id"])[1]
    refused = {"balance": error.balance, "held": error.held, "next_refill_at": error.next_refill_at}
    if ctx.role == "researcher":
        next_step = (
            "task.wait {kind: credits, detail: 'XP requests for "
            f"{record['id']}', ref: {plan['episodes']}}} and end your run; the watcher resumes you"
            " when the balance covers it"
        )
    else:
        next_step = (
            f"credits are the platform's: wait for the refill at {error.next_refill_at} or tell"
            " the user; the runs already created continue"
        )
    return {
        "ok": False,
        "error": f"credits refused: {error.detail}",
        "credits_refused": refused,
        "experiment": record["id"],
        "created": created,
        "reconciled": reconciled,
        "runs": _xp_runs(ctx.store, n),
        "next": next_step,
    }


def _next_after_request(ctx: ToolContext, record: dict, pending_ids: list[str]) -> str:
    if not pending_ids:
        return (
            f"every request is finished; xp.status {{experiment: {record['id']}}} stores the rows"
        )
    if ctx.role == "researcher":
        return (
            f"task.wait {{kind: xp, ref: {json.dumps(pending_ids)}}} and end your run; the watcher"
            " resumes you when they finish, then xp.status stores the rows"
        )
    return (
        f"xp.status {{experiment: {record['id']}}} refreshes the runs and stores the rows once the"
        " requests finish (the platform runs them on its own schedule)"
    )


# --- status ----------------------------------------------------------------------------------


def handle_status(call: ToolCall) -> dict:
    ctx = call.ctx
    store = ctx.store
    background = None
    if call.get("resume"):
        n, background = _resume(call)
    elif call.get("experiment") is not None:
        n = a.record_number(call.get("experiment"), "experiment", "experiment")
    elif call.get("request"):
        n = _experiment_of_request(ctx, call.get("request"))
    else:
        raise ToolError("status needs an experiment, a request or a resume token")
    if background is not None:
        return background
    record = experiments.read(store, n)
    if record["status"] != "running":
        return _report(record, note=f"{record['id']} is {record['status']}; its runs are final")
    wanted = call.get("request")
    for run in _xp_runs(store, n):
        if wanted and run["platform_request_id"] != wanted:
            continue
        run = _refresh(ctx, record, run)
        if _needs_evaluate(run):
            background = _evaluate(ctx, record, run)
            if background is not None:
                return background
    return _report(experiments.read(store, n))


def _experiment_of_request(ctx: ToolContext, request_id: str) -> int:
    row = ctx.store.one(
        "SELECT experiment_id FROM experiment_runs WHERE platform_request_id = ? AND kind = ?",
        (request_id, RUN_KIND),
    )
    if row is None:
        raise ToolError(f"no run records the request {request_id}", request=request_id)
    return int(row["experiment_id"])


def _refresh(ctx: ToolContext, record: dict, run: dict) -> dict:
    """The run's platform state; a failed or cancelled request makes the run invalid."""
    if run["state"] in TERMINAL_STATES:
        return run
    platform = ctx.platform()
    request_id = run["platform_request_id"]
    request = wait.call_platform(
        ctx, f"GET /v2/experience-requests/{request_id}", lambda: platform.xp_get(request_id)
    )
    validity = None
    if request.status in ("failed", "cancelled") and run["validity"] == "valid":
        reason = f": {request.error}" if request.error else ""
        validity = f"invalid: request {request.status}{reason}"
    return experiments.update_run(
        ctx.store,
        ctx.actor,
        run["id"],
        state=request.status,
        episodes_completed=request.completed_count,
        validity=validity,
    )


def _needs_evaluate(run: dict) -> bool:
    results = run.get("results") or {}
    return run["state"] == "completed" and run["validity"] == "valid" and "rows" not in results


def _evaluate(ctx: ToolContext, record: dict, run: dict) -> dict | None:
    """Download the results, run ``evaluate`` on them; the background result when it outlives
    the budget, else ``None`` once the rows (or the failure) are stored."""
    coworld = ctx.coworld()
    platform = ctx.platform()
    request_id = run["platform_request_id"]
    dest = coworld.documents / "xp" / record["id"] / request_id
    files = wait.call_platform(
        ctx,
        f"GET /v2/experience-requests/{request_id}/download",
        lambda: platform.xp_download(request_id, include=["results"], dest=dest),
    )
    try:
        mixin = mixin_module.load(coworld.mixin)
    except mixin_module.MixinError as error:
        raise ToolError(error.problems, mixin=str(coworld.mixin)) from error
    if "evaluate" not in mixin.commands:
        raise ToolError(
            "the mixin declares no evaluate command; add commands.evaluate to MIXIN.md so"
            " downloaded results become metric rows",
            downloaded=[str(path) for path in files],
        )
    env = {**os.environ, **ctx.env, RESULTS_ENV: str(dest)}
    result = mixin_module.run_command(
        mixin,
        "evaluate",
        cwd=coworld.code,
        env=env,
        budget_s=EVALUATE_BUDGET_S,
        log_dir=coworld.documents / "logs",
    )
    return _finish_evaluate(ctx, record, run, result, mixin)


def _finish_evaluate(
    ctx: ToolContext, record: dict, run: dict, result: mixin_module.CommandResult, mixin
) -> dict | None:
    if not result.finished:
        token = json.dumps({"run": run["id"], "token": result.resume_token})
        return {
            "experiment": record["id"],
            "run": run["id"],
            "request": run["platform_request_id"],
            "background": {
                "pid": result.pid,
                "log": str(result.log_path),
                "resume": token,
                "output_tail": result.output_tail,
            },
            "next": (
                f"evaluate is still running (pid {result.pid}); task.wait {{kind: process, ref:"
                f" {result.pid}}} and end your run, or call xp.status {{resume: <token>}} when"
                " it is done"
            ),
        }
    results = dict(run.get("results") or {})
    results["evaluate"] = {"log": str(result.log_path), "exit_code": result.exit_code}
    if not result.ok:
        capabilities.report_failure(
            ctx.store, ctx.actor, "evaluate", output=result.output_tail, command=result.command
        )
        _store_rows(ctx, run, results, f"invalid: evaluate failed (exit {result.exit_code})")
        return None
    try:
        rows = rows_from_evaluate_output(
            _evaluate_output(result.log_path),
            policy_version=results["version"],
            game_version=mixin.coworld_version or "unknown",
            request_id=run["platform_request_id"],
        )
    except ValueError as error:
        capabilities.report_failure(
            ctx.store,
            ctx.actor,
            "evaluate",
            output=f"{result.output_tail}\n\n{error}",
            command=result.command,
        )
        _store_rows(ctx, run, results, f"invalid: evaluate output: {error}")
        return None
    results["rows"] = [asdict(row) for row in rows]
    _store_rows(ctx, run, results, None)
    return None


def _store_rows(ctx: ToolContext, run: dict, results: dict, validity: str | None) -> None:
    experiments.update_run(ctx.store, ctx.actor, run["id"], results=results, validity=validity)


def _evaluate_output(log_path: Path) -> str:
    """The log without the ``$ <command>`` line ``run_command`` writes first."""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("$ "):
        _, _, text = text.partition("\n")
    return text


def _resume(call: ToolCall) -> tuple[int, dict | None]:
    """Finish a backgrounded evaluate: the run's Experiment and, while it runs, the report."""
    ctx = call.ctx
    try:
        info = json.loads(call.get("resume"))
        run_id = int(info["run"])
        token = str(info["token"])
    except (ValueError, KeyError, TypeError) as error:
        raise ToolError(f"resume is not a token from xp.status: {error}") from error
    row = ctx.store.one("SELECT experiment_id FROM experiment_runs WHERE id = ?", (run_id,))
    if row is None:
        raise ToolError(f"run {run_id} does not exist", resume=call.get("resume"))
    n = int(row["experiment_id"])
    record = experiments.read(ctx.store, n)
    run = next(r for r in record["runs"] if r["id"] == run_id)
    if not _needs_evaluate(run):
        return n, None
    try:
        result = mixin_module.resume(token)
    except mixin_module.MixinError as error:
        raise ToolError(error.problems, resume=call.get("resume")) from error
    mixin = mixin_module.load(ctx.coworld().mixin)
    return n, _finish_evaluate(ctx, record, run, result, mixin)


def _report(record: dict, note: str | None = None) -> dict:
    runs = [run for run in record["runs"] if run["kind"] == RUN_KIND]
    pending = [run["platform_request_id"] for run in runs if run["state"] not in TERMINAL_STATES]
    settled = all(
        run["state"] in TERMINAL_STATES
        and ("rows" in (run.get("results") or {}) or run["validity"] != "valid")
        for run in runs
    )
    report = {
        "experiment": record["id"],
        "runs": runs,
        "complete": bool(runs) and settled,
        "pending": pending,
    }
    if note:
        report["note"] = note
    elif report["complete"]:
        report["next"] = (
            f"experiment.close {{experiment: {record['id']}}} applies the decision rule"
        )
    elif pending:
        report["next"] = f"{len(pending)} requests still run; task.wait {{kind: xp}} or try later"
    return report


# --- shared ----------------------------------------------------------------------------------


def _xp_runs(store, n: int) -> list[dict]:
    return [run for run in experiments.read(store, n)["runs"] if run["kind"] == RUN_KIND]


def _run_for(runs: list[dict], seed: int, arm: str) -> dict | None:
    for run in runs:
        if run["seed"] == seed and run["arm"] == arm:
            return run
    return None


def dispatch(call: ToolCall) -> dict:
    if call.action == "request":
        return handle_request(call)
    if call.action == "status":
        return handle_status(call)
    raise AssertionError(call.action)


SPEC = tool(
    ToolSpec(
        name="xp",
        description=(
            "Experience (XP) requests for a running Experiment: one request per seed and arm, "
            "recorded as runs the moment the platform accepts them; status refreshes them, "
            "downloads finished results and stores the mixin's evaluate rows on the runs."
        ),
        handler=dispatch,
        roles=BOTH,
        executor="workspace",
        actions=(
            ActionSpec(
                "request",
                "Create the requests for an Experiment's seeds and arms (never waits itself).",
                {
                    "experiment": EXPERIMENT,
                    "candidate": a.string("The candidate Version (default: the locked design's)."),
                    "baseline": a.string("The baseline Version (default: the locked design's)."),
                    "seeds": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Seeds to play (default 1..n_min of the rule, at most 20).",
                    },
                    "episodes": a.integer("Episodes per request (default 1)."),
                    "league": a.string("A league id the platform fills the other seats from."),
                    "opponents": a.strings("Policy version ids for the other seats."),
                    "arms": a.strings("candidate and/or baseline (default both)."),
                },
                ("experiment",),
            ),
            ActionSpec(
                "status",
                "Refresh the runs, download finished results and store the evaluate rows.",
                {
                    "experiment": EXPERIMENT,
                    "request": a.string("One platform request id instead of the Experiment."),
                    "resume": a.string("The token of a backgrounded evaluate, to finish it."),
                },
            ),
        ),
    )
)
