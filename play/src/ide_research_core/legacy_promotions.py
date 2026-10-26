"""One-time migration of the old "promotion" records to champion submissions.

Softmax uses "promotion" for the commissioner moving an entry between Divisions, so the
research workspace renamed its own term (submitting a candidate as our champion) to
"champion submission". Canonical repositories created before the rename still hold the old
names; ``workspace.bootstrap`` calls ``migrate`` so the first bootstrap after an upgrade
rewrites them in one commit on canonical ``main``:

- ``promotions/P-<ulid>.json`` -> ``champion-submissions/CS-<ulid>.json`` (the ``id`` field
  too), and ``promotions/sources/*`` -> ``champion-submissions/sources/*``;
- an experiment's ``decision.outcome: promote`` -> ``submit``;
- a stored ``stats evaluate`` outcome's ``promote_eligible`` -> ``submit_eligible``;
- a version event's ``data.promotion_id: P-...`` -> ``champion_submission_id: CS-...``.

Free text (journals, ``plan.md``, history details) keeps the old words and ids. A workspace
without old records is left untouched, so the call is idempotent, and ``needed`` makes the
common no-op case two cheap git reads. Besides bootstrap, every ``research champion`` command
migrates first (so no champion state is read from unmigrated records) and the orchestrator's
gate runs ``research champion migrate`` for every game on its poll, waking the coordinator
with the summary so the user is told. A migration also prints one line to stderr.
"""

import json
import sys
from pathlib import Path

import frontmatter

from ide_research_core import workspace
from ide_research_core.io import gitops

OLD_DIR = "promotions"
NEW_DIR = "champion-submissions"


def _new_id(old: str) -> str:
    return "CS-" + old.removeprefix("P-") if old.startswith("P-") else old


def _json(data) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


def _tracked(bare: Path, prefix: str) -> list[str]:
    listed = gitops._run(bare, "ls-tree", "-r", "--name-only", workspace.MAIN, "--", prefix)
    return listed.stdout.decode().split()


def _submission_files(bare: Path, show) -> dict[str, bytes | None]:
    files: dict[str, bytes | None] = {}
    for path in _tracked(bare, f"{OLD_DIR}/"):
        relative = path.removeprefix(f"{OLD_DIR}/")
        content = show(path)
        name = Path(relative).name
        if "/" not in relative and name.startswith("P-") and name.endswith(".json"):
            record = json.loads(content)
            record["id"] = _new_id(record["id"])
            files[f"{NEW_DIR}/{_new_id(name.removesuffix('.json'))}.json"] = _json(record)
        else:
            files[f"{NEW_DIR}/{relative}"] = content
        files[path] = None
    return files


def _experiment_files(bare: Path, show) -> dict[str, bytes]:
    files = {}
    for path in _tracked(bare, "experiments/"):
        if not path.endswith(".md"):
            continue
        post = frontmatter.loads(show(path).decode("utf-8"))
        decision = post.metadata.get("decision")
        if isinstance(decision, dict) and decision.get("outcome") == "promote":
            decision["outcome"] = "submit"
            files[path] = (frontmatter.dumps(post) + "\n").encode("utf-8")
    return files


def _outcome_files(bare: Path, show) -> dict[str, bytes]:
    files = {}
    for path in _tracked(bare, "stats/outcomes/"):
        if not path.endswith(".json"):
            continue
        outcome = json.loads(show(path))
        if "promote_eligible" in outcome:
            outcome["submit_eligible"] = outcome.pop("promote_eligible")
            files[path] = _json(outcome)
    return files


def _version_event_files(bare: Path, show) -> dict[str, bytes]:
    files = {}
    for path in _tracked(bare, "versions/"):
        if "/events/" not in path or not path.endswith(".json"):
            continue
        event = json.loads(show(path))
        data = event.get("data")
        if isinstance(data, dict) and "promotion_id" in data:
            data["champion_submission_id"] = _new_id(data.pop("promotion_id"))
            files[path] = _json(event)
    return files


OLD_VALUES = r"promote_eligible|\"promotion_id\"|outcome: promote\b"


def needed(bare: Path) -> bool:
    """Whether canonical ``main`` still holds old records (cheap: ls-tree and git grep)."""
    if not gitops.git(bare, "for-each-ref", f"refs/heads/{workspace.MAIN}"):
        return False
    if _tracked(bare, f"{OLD_DIR}/"):
        return True
    found = gitops._run(
        bare, "grep", "-q", "-E", OLD_VALUES, workspace.MAIN, "--",
        "experiments", "stats/outcomes", "versions", check=False,
    )
    return found.returncode == 0


def migrate(clone: Path) -> dict | None:
    """Rewrite old promotion records on canonical ``main``; a summary if anything changed."""
    bare = workspace.canonical_of(clone)
    if not needed(bare):
        return None

    def show(path: str) -> bytes:
        return gitops.show(bare, workspace.MAIN, path)

    submissions = _submission_files(bare, show)
    experiments = _experiment_files(bare, show)
    outcomes = _outcome_files(bare, show)
    events = _version_event_files(bare, show)
    files = {**submissions, **experiments, **outcomes, **events}
    if not files:
        return None
    published = workspace.publish(
        clone, files, "migrate: rename promotion records to champion submissions"
    )
    summary = {
        "canonical": str(bare),
        "commit": published["main_sha"],
        "champion_submissions": sum(1 for path in submissions if path.startswith(f"{OLD_DIR}/")),
        "experiments": len(experiments),
        "outcomes": len(outcomes),
        "version_events": len(events),
    }
    print(f"research: migrated promotion records to champion submissions: {json.dumps(summary)}", file=sys.stderr)
    return summary
