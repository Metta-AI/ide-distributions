"""Read-only sleep veto for work the local supervisor must observe without a browser."""

import json
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

from ide_research_core import champion_submission
from ide_research_core.io import gitops

from .context import subprocess_context
from .gate import other_research_games
from .storage import league_results_seen, snapshot


def activity(context):
    jobs = json.loads((context.home / "cron/jobs.json").read_text())["jobs"]
    supervisor = next(job for job in jobs if job["id"] == "supervisor")
    if not supervisor["enabled"] or supervisor["state"] in {"paused", "completed"}:
        return {"waiting_on": []}
    waiting = []
    for run in snapshot(context)["awaiting"]:
        awaiting = json.loads(run["metadata"]).get("awaiting") or {}
        if any(awaiting.values()):
            waiting.append(f"task:{run['task_id']}:external")

    # Read canonical records directly. No clone bootstrap, migration, remote polling or writes.
    seen = league_results_seen(context)
    for game in [None, *other_research_games(context.env)]:
        env = context.env if game is None else context.for_game(game).env
        if "RESEARCH_CANONICAL" not in env:
            continue
        canonical = Path(env["RESEARCH_CANONICAL"])
        if not (canonical / "HEAD").exists():
            continue
        paths = gitops._run(canonical, "ls-tree", "--name-only", "main", "champion-submissions/")
        submissions = [
            champion_submission.ChampionSubmission(
                **json.loads(gitops.show(canonical, "main", path))
            )
            for path in sorted(paths.stdout.decode().split())
            if Path(path).name.startswith("CS-")
        ]
        waiting.extend(
            f"submission:{item.id}"
            for item in submissions
            if item.stage in champion_submission.IN_FLIGHT
        )
        latest = next(
            (
                item
                for item in reversed(submissions)
                if item.submission_id and item.stage in champion_submission.ACCEPTED
            ),
            None,
        )
        if latest is not None and latest.id not in seen:
            waiting.append(f"league-result:{latest.id}")

    binding = json.loads(
        (Path(context.env["SOFTMAX_BRIDGE_DIRECTORY"]) / "config.json").read_text()
    )
    request = Request(
        binding["hermes_url"].rstrip("/") + "/v1/coordinator/pending",
        headers={"Authorization": "Bearer " + binding["service_key"]},
    )
    with closing(urlopen(request, timeout=5)) as response:
        if json.load(response)["pending"] is not None:
            waiting.append("coordinator:pending")
    return {"waiting_on": waiting}


def main():
    print(json.dumps(activity(subprocess_context())))


if __name__ == "__main__":
    main()
