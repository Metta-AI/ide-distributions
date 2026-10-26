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
without old records is left untouched, so the call is idempotent.
"""

import json
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


def migrate(clone: Path) -> bool:
    """Rewrite old promotion records on canonical ``main``; True if anything changed."""
    bare = workspace.canonical_of(clone)
    if not gitops.git(bare, "for-each-ref", f"refs/heads/{workspace.MAIN}"):
        return False

    def show(path: str) -> bytes:
        return gitops.show(bare, workspace.MAIN, path)

    files: dict[str, bytes | None] = {}
    files.update(_submission_files(bare, show))
    files.update(_experiment_files(bare, show))
    files.update(_outcome_files(bare, show))
    files.update(_version_event_files(bare, show))
    if not files:
        return False
    workspace.publish(clone, files, "migrate: rename promotion records to champion submissions")
    return True
